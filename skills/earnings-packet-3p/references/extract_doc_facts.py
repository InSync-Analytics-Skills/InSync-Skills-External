#!/usr/bin/env python3
"""earnings-packet-3p, section 10 source material without the whole document.

    python3 references/extract_doc_facts.py transcript.txt --kind transcript
    python3 references/extract_doc_facts.py tenq.txt --kind 10q --budget 2200
    python3 references/extract_doc_facts.py rel.txt --kind release --region 4      # widen one
    python3 references/extract_doc_facts.py --selftest

WHAT PROBLEM THIS SOLVES, AND WHAT IT COSTS
-------------------------------------------
Step 4 parses the last transcript, release and 10-Q so Step 6 can write five risks and
five catalysts with sources. Those three documents run tens of thousands of tokens and
roughly forty sentences of them are used. Reading all of it to use that much is the
single largest cost in this skill.

This windows the documents down to the passages that carry risk and catalyst language
and prints them as numbered REGIONS with their character offsets, so nothing is hidden:
what was dropped is counted, and any region can be re-read at full width with --region.

BE HONEST ABOUT THE TRADE. A keyword window is not a read. It will surface a hedge that
uses one of its cue words and miss one that does not, and management's most interesting
qualification is sometimes the sentence that avoids every cue in this file. Two habits
make that safe rather than merely cheap:

  * The Q&A section of a transcript is where the unrehearsed answer lives, so it is
    scored up, never sampled out, and a transcript that HAS a Q&A block always keeps at
    least one window inside it.
  * The footer states the drop. When a document loses more than the reported share, or
    when the packet's key debate turns on something the windows did not carry, widen it
    with --region or read the document. The tool is a default, not a ceiling.

Rule 2 is untouched: every quoted figure is still document-sourced, still carries the
InSync document it came from, and still gets declared in the narrative's
`document_figures`. Rule 1 is untouched: nothing here computes anything.
"""
import argparse
import os
import re
import sys

CUES = {
    3.0: [r"\bguid(?:e|es|ed|ance|ing)\b", r"\braise[sd]?\b", r"\blower(?:ed|ing)?\b",
          r"\breaffirm", r"\bwithdraw", r"\bsuspend", r"\boutlook\b", r"\bfor the (?:full )?year\b",
          r"\bnext (?:quarter|year)\b", r"\bexpect(?:s|ed|ing)? to\b"],
    2.0: [r"\bheadwind", r"\btailwind", r"\bpressure\b", r"\bsoften", r"\bslow(?:er|down|ing)\b",
          r"\bdeteriorat", r"\bimprov(?:e|ed|ing|ement)", r"\bacceler", r"\bdeceler",
          r"\bmargin\b", r"\bprice\b|\bpricing\b", r"\bvolume", r"\bdemand\b", r"\bbacklog\b",
          r"\bbook(?:ings|-to-bill)\b", r"\bchurn\b", r"\brenewal", r"\bretention\b",
          r"\bcapacit", r"\bsupply\b", r"\binventor", r"\bcost invento"],
    1.5: [r"\brisk", r"\buncertain", r"\bexposure\b", r"\bdepend(?:s|ent|ency)\b",
          r"\bcould (?:adversely|materially)\b", r"\bmay be (?:adversely|materially)\b",
          r"\bmaterial(?:ly)? (?:adverse|impact)", r"\blitigation\b", r"\bregulat",
          r"\binvestigation\b", r"\bimpair", r"\brestructur", r"\bcovenant\b",
          r"\bgoing concern\b", r"\btariff", r"\bfx\b|\bcurrency\b", r"\brecall\b"],
    1.2: [r"\blaunch", r"\bapprov(?:al|ed)\b", r"\bramp\b", r"\bpipeline\b", r"\bcontract\b",
          r"\baward", r"\bacquisi", r"\bdivest", r"\bclos(?:e|ing) (?:in|by|during)\b",
          r"\bmilestone\b", r"\bcatalyst\b", r"\bon track\b", r"\btarget(?:s|ing|ed)?\b"],
}
NEGATIVE = [r"^\s*safe harbor", r"forward-looking statements within the meaning",
            r"^\s*operator\b", r"private securities litigation reform act",
            r"^\s*\[?(?:end|conclusion) of", r"non-gaap financial measures\b.{0,80}reconcil",
            r"thank you(?: all| everyone| very much)?[, ].{0,40}\bjoin(?:ing)?\b",
            r"look forward to (?:speaking|seeing|updating|talking)",
            r"^\s*(?:this|that) (?:concludes|completes|ends)\b",
            r"\bhave a (?:great|good|nice) (?:day|morning|afternoon|weekend)\b"]

QA_MARKERS = [r"question[- ]and[- ]answer", r"^\s*q\s*&\s*a\b", r"we(?:'ll| will) now (?:begin|open|take)",
              r"first question comes from", r"our next question"]

KIND_BOOST = {
    "transcript": [(r"\bq\s*&\s*a\b", 1.0)],
    "10q": [(r"\bitem\s+1a\b", 3.0), (r"\brisk factors\b", 3.0),
            (r"management's discussion", 1.5)],
    "release": [(r"\boutlook\b", 2.0), (r"\bguidance\b", 2.0)],
}

COMPILED = {w: [re.compile(p, re.I) for p in pats] for w, pats in CUES.items()}
NEG = [re.compile(p, re.I | re.M) for p in NEGATIVE]


def paragraphs(text):
    out, pos = [], 0
    for chunk in re.split(r"\n\s*\n", text):
        start = text.find(chunk, pos)
        if start < 0:
            start = pos
        pos = start + len(chunk)
        s = chunk.strip()
        if s:
            out.append((start, s))
    return out


def qa_offset(text):
    for pat in QA_MARKERS:
        m = re.search(pat, text, re.I | re.M)
        if m:
            return m.start()
    return None


def score(para, kind, in_qa):
    s = 0.0
    for w, pats in COMPILED.items():
        for p in pats:
            if p.search(para):
                s += w
    for p, w in KIND_BOOST.get(kind, []):
        if re.search(p, para, re.I):
            s += w
    for p in NEG:
        if p.search(para):
            s -= 6.0
    if in_qa:
        s *= 1.35
    n = max(len(para.split()), 1)
    if n < 12:
        s *= 0.4
    if n > 320:
        s *= 0.85
    return s


def extract(text, kind="transcript", budget=2400):
    qa = qa_offset(text) if kind == "transcript" else None
    paras = paragraphs(text)
    scored = []
    for off, p in paras:
        in_qa = qa is not None and off >= qa
        scored.append((score(p, kind, in_qa), off, p, in_qa))
    scored.sort(key=lambda r: -r[0])

    picked, words = [], 0
    for sc, off, p, in_qa in scored:
        if sc <= 0:
            break
        n = len(p.split())
        if words + n > budget and picked:
            continue
        picked.append((off, p, sc, in_qa))
        words += n
        if words >= budget:
            break

    if qa is not None and not any(r[3] for r in picked):
        for sc, off, p, in_qa in scored:
            if in_qa and sc > 0:
                picked.append((off, p, sc, True))
                words += len(p.split())
                break

    picked.sort(key=lambda r: r[0])
    total_words = sum(len(p.split()) for _, p in paras)
    return picked, {
        "paragraphs_total": len(paras),
        "paragraphs_kept": len(picked),
        "words_total": total_words,
        "words_kept": words,
        "qa_offset": qa,
    }


def render(picked, stats, kind, path):
    out = []
    out.append("SOURCE   %s   (kind: %s)" % (os.path.basename(path), kind))
    out.append("REGIONS  %d of %d paragraphs, %d of %d words (%.0f%% of the document was "
               "NOT read)" % (stats["paragraphs_kept"], stats["paragraphs_total"],
                              stats["words_kept"], stats["words_total"],
                              100.0 * (1 - stats["words_kept"] / max(stats["words_total"], 1))))
    if stats["qa_offset"] is not None:
        out.append("Q&A      begins at offset %d; regions inside it are marked [Q&A]"
                   % stats["qa_offset"])
    out.append("")
    for i, (off, p, sc, in_qa) in enumerate(picked, 1):
        out.append("--- region %d   offset %d   score %.1f%s ---"
                   % (i, off, sc, "   [Q&A]" if in_qa else ""))
        out.append(p)
        out.append("")
    out.append("Quote from these regions only, cite this document, and declare every figure "
               "you quote in the narrative's `document_figures`.")
    out.append("If the key debate turns on something no region carries, this window missed "
               "it: re-read with --region N for full width, or read the document.")
    return "\n".join(out)


def selftest():
    doc = "\n\n".join([
        "Operator. Good morning and welcome to the fourth quarter earnings call.",
        "Forward-looking statements within the meaning of the Private Securities "
        "Litigation Reform Act of 1995 are subject to risks and uncertainties.",
        "Revenue for the quarter was 1,240 million dollars, an increase over the prior "
        "year period, reflecting continued execution across the portfolio and the "
        "contribution of recent additions to the platform in all regions we serve.",
        "We are raising our full year guidance to a range of 4.90 to 5.00 dollars per "
        "share, which reflects the strength we saw in the quarter and our expectation "
        "that pricing holds through the remainder of the year despite the tariff exposure.",
        "Question-and-answer session. Our next question comes from the line of an analyst.",
        "On the margin question, I would say we do expect some pressure in the first half "
        "as the input cost inventory rolls through, and we have not assumed any recovery "
        "in volume in the guidance, so that would be upside if demand improves.",
        "Thank you all for joining us today and we look forward to speaking next quarter.",
    ])
    picked, stats = extract(doc, "transcript", budget=120)
    texts = " ".join(p for _, p, _, _ in picked)
    checks = [
        ("guidance paragraph is kept", "raising our full year guidance" in texts),
        ("the Q&A margin answer is kept", "expect some pressure" in texts),
        ("safe-harbour boilerplate is dropped", "Private Securities" not in texts),
        ("the operator greeting is dropped", "Good morning and welcome" not in texts),
        ("the sign-off is dropped", "look forward to speaking" not in texts),
        ("at least one region sits in the Q&A", any(r[3] for r in picked)),
        ("offsets are ascending", [r[0] for r in picked] == sorted(r[0] for r in picked)),
        ("the drop is reported", stats["words_kept"] < stats["words_total"]),
    ]
    bad = 0
    for label, okk in checks:
        print(("  ok   " if okk else "  FAIL ") + label)
        bad += 0 if okk else 1

    empty, st2 = extract("", "10q")
    if empty or st2["words_total"] != 0:
        print("  FAIL  an empty document must yield no regions"); bad += 1
    else:
        print("  ok   an empty document yields no regions rather than raising")

    print("EXTRACT SELFTEST PASSED" if not bad else "EXTRACT SELFTEST FAILED (%d)" % bad)
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?")
    ap.add_argument("--kind", default="transcript",
                    choices=["transcript", "release", "10q", "other"])
    ap.add_argument("--budget", type=int, default=2400, help="words to keep (default 2400)")
    ap.add_argument("--region", type=int, help="print one region at full paragraph width")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if not a.path:
        print("supply a parsed document, or --selftest", file=sys.stderr)
        return 2
    with open(a.path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()

    picked, stats = extract(text, a.kind, a.budget)
    if not picked:
        print("no region scored above zero in %s. Read the document; do not infer that it "
              "carries no risk or catalyst." % os.path.basename(a.path))
        return 0
    if a.region:
        if not 1 <= a.region <= len(picked):
            print("region %d does not exist; there are %d" % (a.region, len(picked)),
                  file=sys.stderr)
            return 2
        off, p, sc, in_qa = picked[a.region - 1]
        print("region %d   offset %d   score %.1f%s\n" % (a.region, off, sc,
                                                          "   [Q&A]" if in_qa else ""))
        print(p)
        return 0
    print(render(picked, stats, a.kind, a.path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
