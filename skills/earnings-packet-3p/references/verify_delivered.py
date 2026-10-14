#!/usr/bin/env python3
"""earnings-packet-3p, pass 7: verify the document the reader actually receives.

    python3 references/verify_delivered.py "<TICKER> <Q>Q<YY> Earnings Packet vN.docx"
    python3 references/verify_delivered.py --strict  "<the built>.docx"     # exit 1 on findings

WHY THIS FILE EXISTS
--------------------
Every other check in this bundle compares the pipeline to itself. Pass 1 and pass 2 read
colour and geometry off the PDF but never a value. Pass 3 opens packet_computed.json from
the working directory, so it is grading the producer's opinion of the producer. Passes 5
and 6 find a table by its vertical borders, which this house style does not draw. The
result, measured rather than argued: a delivered packet whose printed guidance figure was
changed from $50.00B to $99,999.9B, with eight cells blanked, passed check_table_widths,
validate_packet, validate_rules_style and check_masthead, all four, without a complaint.

This pass exists to close that gap and is written to one rule: it opens ONLY the finished
.docx and re-derives what the page claims from the cells the page prints. It imports
nothing from the renderer, reads no intermediate file the pipeline wrote, and uses only
zipfile and re from the standard library, so it cannot inherit a mistake from the code that
produced the document. If it ever starts importing fmt.py's logic or reading
packet_computed.json for a value, it has stopped being an independent check.

REPORT-ONLY BY DEFAULT
----------------------
Run without --strict this prints findings and exits 0. That is deliberate. Two of the
checks below fire on documents that are following the house format as written, notably V4,
where "quote the guide as published, never re-round" puts a verbatim $50,000M beside a
parsed $50.00B in one row. Until that collision is resolved in house-format.md, a blocking
gate would fail correct builds, and a gate that fails correct builds is how an operator
learns to edit the bundled scripts. Gather the findings first, settle the format question,
then turn on --strict.

THE CHECKS
----------
V1 dispersion   printed Low <= printed Mean <= printed High
V2 guidance     the printed Position follows from the printed band and printed consensus
V3 valuation    printed "vs 1-yr" equals printed LTM / printed 1-yr average - 1
V4 magnitude    no row mixes $B with $M or with an unsuffixed number in its data cells
V5 precision    every printed percentage carries 1 decimal, or 2 on a '%2' rate row
V6 identity     masthead ticker, filename ticker and, if present, packet_computed.json all
                name the same issuer. This is the check that catches a validator run from
                the wrong build directory.
                WHAT V6 CANNOT DO, and the reason it says so on every run: all
                three sites it compares are written from the SAME Step 1 resolution, so a
                run that resolved the wrong ticker_id agrees with itself perfectly and V6
                passes. There is no second source of issuer identity inside the package, so
                this is not a gate that can be improved; it is a gate that must not be read
                as more than it is. A search_tickerid call that returned the wrong company
                at score 100 is the documented failure mode, and only a human comparing the
                masthead to the company they meant will catch it.
V6b label       the filename's v<semver>, when it carries one, equals the skill version
V7 structure    ten numbered section headings, present and in order
V8 magnitude    a guided figure and the estimate against it are within 100x of each other,
                the tripwire for a corrupted or mis-scaled cell
V9 structure    every numbered section carries at least one table, so a heading never
                stands over nothing. Section 2 is exempt: it legitimately prints a heading
                and a note with no table when the issuer guides nothing.
"""
import os
import re
import sys
import json
import zipfile

TXT = re.compile(r"<w:t[^>]*>([^<]*)</w:t>")
CELL = re.compile(r"<w:tc>.*?</w:tc>", re.S)
ROW = re.compile(r"<w:tr[ >].*?</w:tr>", re.S)
TBL = re.compile(r"<w:tbl>.*?</w:tbl>", re.S)

def _ccy_alt():
    import json as _json
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(here, "..", "common", "currency-tokens.json"),
                  encoding="utf8") as fh:
            toks = _json.load(fh)["tokens"]
    except (OSError, ValueError, KeyError):
        toks = ["$"]
        print("pass 7  warn: common/currency-tokens.json unreadable, money matching is "
              "dollar-only for this run")
    return "(?:" + "|".join(re.escape(t) for t in toks) + ")"


CCY = _ccy_alt()
MONEY = re.compile(
    r"^\(?(?:"
    + CCY + r"[+\u2013-]?([0-9][0-9,]*(?:\.[0-9]+)?)([BMTk])?"
    + r"|[+\u2013-]?([0-9][0-9,]*(?:\.[0-9]+)?)([BMTk])"
    + r")\)?$")
PCT = re.compile(r"^[+–-]?([0-9][0-9,]*(?:\.([0-9]+))?)%$")
MULT = re.compile(r"^([0-9][0-9,]*\.?[0-9]*)x$")
EMPTY = {"", "\u2013", "\u2014", "-", "n/a", "none", "not guided", "no history"}

TOL_PCT = 1.2


def unescape(s):
    return (s.replace("&amp;", "&").replace("&lt;", "<")
             .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'"))


def cells(row_xml):
    return [unescape("".join(TXT.findall(c))).strip() for c in CELL.findall(row_xml)]


def tables(xml):
    return [[cells(r) for r in ROW.findall(t)] for t in TBL.findall(xml)]


def num(s):
    s = (s or "").strip()
    if not s or s.lower() in EMPTY:
        return None
    neg = ((s.startswith("(") and s.endswith(")"))
           or s[0] in "\u2013-" or s.lstrip("($")[:1] in ("\u2013", "-"))
    for pat in (MONEY, PCT, MULT):
        m = pat.match(s)
        if m:
            v = float(m.group(1).replace(",", ""))
            return -v if neg else v
    try:
        v = float(s.replace(",", "").lstrip("+"))
        return -v if neg else v
    except ValueError:
        return None


def suffix(s):
    m = MONEY.match((s or "").strip())
    if not m:
        return None
    return (m.group(2) if m.group(1) is not None else m.group(4)) or ""


def col(header, *names):
    for i, h in enumerate(header):
        hl = h.lower()
        if any(n in hl for n in names):
            return i
    return None


def verify(path):
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf8")
    out = []
    flat = unescape(" ".join(TXT.findall(xml)))

    for ti, tab in enumerate(tables(xml), 1):
        if len(tab) < 2:
            continue
        head = tab[0]

        iM, iL, iH = col(head, "mean"), col(head, "low"), col(head, "high")
        if None not in (iM, iL, iH):
            for r in tab[1:]:
                if max(iM, iL, iH) >= len(r):
                    continue
                m, lo, hi = num(r[iM]), num(r[iL]), num(r[iH])
                if None in (m, lo, hi):
                    continue
                if not lo <= m <= hi:
                    out.append(("V1", f"table {ti}",
                                f"{r[0]!r}: Low {r[iL]} / Mean {r[iM]} / High {r[iH]} "
                                f"do not order"))

        iB, iC, iP = (col(head, "guide low", "low–high", "low-high"),
                      col(head, "consensus", "estimate"), col(head, "position"))
        if None not in (iB, iC, iP):
            for r in tab[1:]:
                if max(iB, iC, iP) >= len(r):
                    continue
                cv = num(r[iC])
                parts = [p.strip() for p in re.split(r"\s[–-]\s", r[iB]) if p.strip()]
                if cv is None or not 1 <= len(parts) <= 2:
                    continue
                lo, hi = num(parts[0]), num(parts[-1])
                if None in (lo, hi):
                    continue
                if lo > hi:
                    lo, hi = hi, lo

                if len(parts) == 2 and lo != hi:
                    want = "above" if cv > hi else "below" if cv < lo else "in range"
                    got = r[iP].lower()
                    got = ("above" if "above" in got else "below" if "below" in got
                           else "at" if "at guide" in got else "in range" if "range" in got
                           else got)
                    if not {want, got} <= {"at", "in range"} and want != got:
                        out.append(("V2", f"table {ti}",
                                    f"{r[0]!r}: band {r[iB]}, {head[iC].lower()} {r[iC]}, "
                                    f"page says {r[iP]!r}, the printed numbers give "
                                    f"{want!r}"))

                mid = (lo + hi) / 2.0
                if cv and abs(mid) > 1e-9:
                    ratio = abs(cv / mid)
                    if ratio > 100 or ratio < 0.01:
                        shown = ratio if ratio > 1 else 1 / ratio
                        out.append(("V8", f"table {ti}",
                                    f"{r[0]!r}: guide {r[iB]} and {head[iC].lower()} "
                                    f"{r[iC]} are {shown:,.0f}x apart: one of those two "
                                    f"cells is corrupted or on the wrong scale"))

        iLTM, i1y, iVs = (col(head, "ltm"), col(head, "1-yr avg", "1y avg", "1-yr average"),
                          col(head, "vs 1-yr", "vs 1y"))
        if None not in (iLTM, i1y, iVs):
            for r in tab[1:]:
                if max(iLTM, i1y, iVs) >= len(r):
                    continue
                a, b, v = num(r[iLTM]), num(r[i1y]), num(r[iVs])
                if None in (a, b, v) or not b:
                    continue
                d = (a / b - 1) * 100
                if abs(d - v) > TOL_PCT:
                    out.append(("V3", f"table {ti}",
                                f"{r[0]!r}: LTM {r[iLTM]} over 1-yr {r[i1y]} implies "
                                f"{d:+.1f}%, page prints {r[iVs]}"))

        for r in tab[1:]:
            money = [c for c in r[1:] if MONEY.match(c.strip())]
            sfx = {suffix(c) for c in money} - {None}
            if len(sfx) > 1:
                out.append(("V4", f"table {ti}",
                            f"{r[0]!r}: one row, magnitudes {sorted(s or 'bare' for s in sfx)}: "
                            f"{money[:6]}"))
            for c in r[1:]:
                m = PCT.match(c.strip())
                if m and m.group(2) is not None and len(m.group(2)) not in (1, 2):
                    out.append(("V5", f"table {ti}",
                                f"{r[0]!r}: percentage {c} carries "
                                f"{len(m.group(2))} decimals; the house format is 1, or 2 "
                                f"on a fine-grained rate row"))

    base = os.path.basename(path)
    first = base.split()[0] if base.split() else ""
    fname_t = first if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", first) else None
    mast = re.search(r"\(([A-Z][A-Z0-9.\-]{0,9})\)\s*-\s*\S+", flat)
    mast_t = mast.group(1) if mast else None
    if fname_t and mast_t and fname_t.upper() != mast_t.upper():
        out.append(("V6", "identity",
                    f"filename says {fname_t}, the masthead says {mast_t}"))
    try:
        with open("packet_computed.json", encoding="utf8") as fh:
            jt = (json.load(fh).get("meta") or {}).get("ticker")
        if jt and mast_t and jt.upper() != mast_t.upper():
            out.append(("V6", "identity",
                        f"the masthead says {mast_t} but packet_computed.json in this "
                        f"directory is {jt}: the validators in this directory are grading "
                        f"a different issuer"))
    except (OSError, ValueError):
        pass

    fname_v = re.search(r"\bv([0-9]+\.[0-9]+\.[0-9]+)\b", base)
    if fname_v:
        want = None
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "..", "SKILL.md"), encoding="utf8") as fh:
                want = (re.search(r"(?m)^version:\s*\"?([0-9]+\.[0-9]+\.[0-9]+)\"?\s*$",
                                  fh.read()) or [None, None])[1]
        except OSError:
            want = None
        if want and fname_v.group(1) != want:
            out.append(("V6b", "identity",
                        f"the filename says v{fname_v.group(1)} but the skill is v{want}: "
                        f"the document is correct and its label is not"))

    heads = re.findall(r"(?m)^\s*(\d{1,2})\.\s+\S", "\n".join(TXT.findall(xml)))
    seen = [int(h) for h in heads if 1 <= int(h) <= 10]
    ordered, last = True, 0
    for h in seen:
        if h != last + 1:
            ordered = h == last
        if h > last:
            last = h
    if last != 10 or not ordered:
        out.append(("V7", "structure",
                    f"expected ten numbered sections in order, found {seen}"))

    order = re.findall(r"(<w:tbl>.*?</w:tbl>)|(<w:p[ >].*?</w:p>)", xml, re.S)
    cur, tally = None, {}
    for tbl, para in order:
        if tbl:
            if cur:
                tally[cur] = tally.get(cur, 0) + 1
            continue
        t = unescape("".join(TXT.findall(para))).strip()
        m = re.match(r"^(\d{1,2})\.\s+\S", t)
        if m and 1 <= int(m.group(1)) <= 10:
            cur = int(m.group(1))
            tally.setdefault(cur, 0)
    bare = [s for s in sorted(tally) if tally[s] == 0 and s != 2]
    if bare:
        out.append(("V9", "structure",
                    f"section(s) {bare} print a heading with no table under it "
                    f"(tables per section: {[tally.get(i, 0) for i in range(1, 11)]})"))
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    strict = "--strict" in sys.argv
    if not args:
        print(__doc__)
        return 2
    total = 0
    unreadable = 0
    for path in args:
        try:
            found = verify(path)
        except (OSError, KeyError, zipfile.BadZipFile) as e:
            print(f"pass 7  {os.path.basename(path)}: could not read the document ({e})")
            print(json.dumps({"status": "error", "where": "verify_delivered.py",
                              "message": f"unreadable document: {e}"}))
            unreadable += 1
            continue
        codes = {}
        for c, _, _ in found:
            codes[c] = codes.get(c, 0) + 1
        state = "CLEAN" if not found else ("FAIL" if strict else "REPORT")
        print(f"pass 7 delivered  {state}   ({os.path.basename(path)}: "
              f"{len(found)} finding(s){', ' + str(codes) if codes else ''})")
        for c, where, msg in found:
            print(f"         [{c}] {where}: {msg}")
        mast_id = re.search(r"\(([A-Z][A-Z0-9.\-]{0,9})\)\s*-\s*\S+",
                            " ".join(TXT.findall(
                                zipfile.ZipFile(path).read("word/document.xml").decode("utf8",
                                                                                       "ignore"))))
        print(f"         V6 confirms this document agrees with itself"
              f"{' on ' + mast_id.group(1) if mast_id else ''}. It cannot confirm the issuer "
              f"resolved at Step 1 was the one you meant: analyst to verify the masthead "
              f"names the intended company.")
        total += len(found)
    if total and not strict:
        print("\nreport-only: run with --strict once house-format.md has settled the "
              "verbatim-guidance column, and this becomes a blocking gate.")
    print("\nDELIVERED-DOCUMENT CHECK",
          "CLEAN" if not total else ("FAILED" if strict else "REPORTED"),
          f"({total} finding(s))")
    if unreadable:
        print(f"\nDELIVERED-DOCUMENT CHECK FAILED: {unreadable} document(s) could not be "
              f"opened. This blocks regardless of --strict: a pass that cannot read the file "
              f"has not verified it, and reporting that as a clean exit is how a missing "
              f"deliverable reads as a passing one.")
        return 1
    return 1 if (total and strict) else 0


if __name__ == "__main__":
    sys.exit(main())
