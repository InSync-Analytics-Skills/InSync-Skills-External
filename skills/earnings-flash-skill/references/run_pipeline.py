#!/usr/bin/env python
"""
earnings-flash PATH B PIPELINE — single LLM-callable entry point.

Usage:
  python references/run_pipeline.py <input.json> <narrative.json> <output.docx>

The LLM only needs to:
  1. Fetch raw MCP data via insync_assistant for the LTCODE list given by
     the MCP search_lineitem(filter='top_kpi') call (or paste responses for all
     master ltcodes).
  2. Write input.json with: ticker, period, prior_period, mcp_responses
  3. Write narrative.json with the SEVEN required flash fields: companyName,
     flashVerdict, investmentRead, whatMatters, variantView, guidance,
     nextWatchItems (all seven are REQUIRED_NARRATIVE_FIELDS)
  4. Call: python references/run_pipeline.py input.json narrative.json output.docx

Python does the rest: self-fetches master KPIs, computes math, builds the
final data.json with the master's authoritative top_kpi_1..5, and invokes
the renderer (_render_internal.py). The LLM never touches data.json or the renderer.

Exits with non-zero + clear error if:
  - LLM forgot to fetch actuals/estimates for any master KPI ltcode
  - Required narrative sections are missing
  - the renderer fails
"""
import json
import os
import re
import shutil as _sh
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_math


REQUIRED_NARRATIVE_FIELDS = [
    "companyName", "guidance", "forwardView",
    "flashVerdict", "investmentRead", "whatMatters", "variantView", "nextWatchItems",
]

_CONDITIONAL_NARRATIVE_FIELDS = ("guidance", "forwardView")


_DIAGNOSTICS = []


_SCRATCH_DIR = None


def _cleanup_scratch(keep: bool, failed: bool, output_docx=None) -> None:
    global _SCRATCH_DIR
    d, _SCRATCH_DIR = _SCRATCH_DIR, None
    if not d or not os.path.isdir(d):
        return
    if keep or failed:
        print(f"[scratch retained] {d}", file=sys.stderr)
        return
    if output_docx:
        try:
            if os.path.commonpath([os.path.abspath(d),
                                   os.path.abspath(output_docx)]) == os.path.abspath(d):
                print(f"[scratch retained] {d} — the output file is inside it",
                      file=sys.stderr)
                return
        except Exception:
            return
    try:
        _sh.rmtree(d)
    except Exception as _e:
        print(f"[scratch] could not remove {d} ({_e.__class__.__name__})", file=sys.stderr)
        return
    try:
        os.rmdir(os.path.dirname(d))
    except OSError:
        pass


def _note(items) -> None:
    for _w in (items or []):
        if _w and _w not in _DIAGNOSTICS:
            _DIAGNOSTICS.append(str(_w))


def _err(msg: str) -> int:
    _payload = {"status": "error", "message": msg}
    if _DIAGNOSTICS:
        _payload["warnings"] = list(_DIAGNOSTICS)
        _payload["diagnostics_note"] = (
            "These were raised BEFORE the failure above and are usually where the cause is. "
            "The error names what broke; these name why.")
    print(json.dumps(_payload, indent=2))
    sys.stdout.flush()
    print("ERROR: " + msg.split("\n")[0], file=sys.stderr)
    for _d in _DIAGNOSTICS[:6]:
        print("  note: " + _d.split("\n")[0][:160], file=sys.stderr)
    return 1


FLASH_MODE = os.environ.get("EF_FLASH_MODE", "1") != "0"

_WRAP_ONLY_FIELDS = (
    "summary", "qualityOfEarnings", "managementTake",
    "revenueDrivers", "marginProfitability",
    "strategicInitiatives", "consumerLandscape",
)

import re as _re
import re
_NUM_RE = _re.compile(r'-?\$?\d[\d,]*\.?\d*\s*(?:bps|ppt|pts?|%|bn|b|m|k)?', _re.I)


def _fact_sig(text):
    toks = set()
    for m in _NUM_RE.findall(text or ""):
        t = _re.sub(r'[\s,]', '', m).lower().rstrip('.')
        if len(t) >= 2 and any(ch.isdigit() for ch in t):
            toks.add(t)
    return toks


def _words(text):
    return set(_re.findall(r'[a-z]{4,}', (text or "").lower()))


def _too_similar(a, b):
    sa, sb = _fact_sig(a), _fact_sig(b)
    if sa and sb:
        num_contain = len(sa & sb) / min(len(sa), len(sb))
    else:
        num_contain = 0.0
    wa, wb = _words(a), _words(b)
    word_contain = len(wa & wb) / min(len(wa), len(wb)) if (wa and wb) else 0.0
    return num_contain >= 0.7 and word_contain >= 0.6


def shape_narrative(narrative: dict, flash_mode: bool = True):
    warnings = []
    if flash_mode:
        for f in _WRAP_ONLY_FIELDS:
            v = narrative.get(f)
            if v:
                n = "text" if isinstance(v, str) else f"{len(v)} item(s)"
                warnings.append(
                    f"[flash] dropped Wrap-only section '{f}' ({n}) — the flash is 6 sections; "
                    f"fold its single key point into whatMatters."
                )
                narrative[f] = "" if isinstance(v, str) else []

    wm = [b for b in (narrative.get("whatMatters") or []) if str(b).strip()]
    watch = [b for b in (narrative.get("nextWatchItems") or []) if str(b).strip()]
    if wm and watch:
        deduped = [b for b in watch if not any(_too_similar(b, w) for w in wm)]
        if deduped and len(deduped) < len(watch):
            warnings.append(
                f"[flash] removed {len(watch) - len(deduped)} Next Watch item(s) that duplicated a What Matters bullet."
            )
            narrative["nextWatchItems"] = deduped
    return narrative, warnings



_BANNED_ADJECTIVES = {
    "strong", "disappointing", "impressive", "significantly",
    "robust", "notable", "unmatched", "beloved", "award-winning",
}

_NARRATIVE_LIST_FIELDS = ("whatMatters", "guidance", "forwardView", "nextWatchItems")
_VARIANT_KEYS = ("street", "expectationGap", "evidence", "whyItMatters",
                 "streetExpected", "whatChanged", "whyMoving", "potentialVariant")
_VERDICT_KEYS = ("positives", "negatives", "net")


def _collect_narrative_strings(narrative: dict) -> list:
    out = []
    fv = narrative.get("flashVerdict")
    if isinstance(fv, dict):
        for k in _VERDICT_KEYS:
            if isinstance(fv.get(k), str) and fv[k].strip():
                out.append((f"flashVerdict.{k}", fv[k]))
    elif isinstance(fv, str) and fv.strip():
        out.append(("flashVerdict", fv))
    ir = narrative.get("investmentRead")
    if isinstance(ir, str) and ir.strip():
        out.append(("investmentRead", ir))
    vv = narrative.get("variantView")
    if isinstance(vv, dict):
        for k in _VARIANT_KEYS:
            if isinstance(vv.get(k), str) and vv[k].strip():
                out.append((f"variantView.{k}", vv[k]))
    for k in _NARRATIVE_LIST_FIELDS:
        v = narrative.get(k)
        if isinstance(v, list):
            for i, s in enumerate(v):
                if isinstance(s, str) and s.strip():
                    out.append((f"{k}[{i}]", s))
    return out


_BROKER_LINT_FIELDS = ("flashVerdict", "whatMatters", "variantView", "guidance",
                       "forwardView", "nextWatchItems")

_BROKER_PATTERNS = (
    (r"\bbrokers?\b",
     "a broker count or a reference to the broker panel"),
    (r"\boutside\s+(?:the\s+|its\s+|their\s+)?(?:\S+[\s-]+){0,3}?ranges?\b",
     "an 'outside the range' panel claim"),
    (r"\b(?:above|below|beyond)\s+(?:the|its|their)\s+(?:whole|entire|full|complete|published)\b",
     "an 'above/below the whole range' panel claim"),
    (r"\b(?:whole|entire|complete|published)\s+(?:\S+[\s-]+){0,3}?ranges?\b",
     "a whole/entire/published range claim"),
    (r"\bfull\s+(?:\S+[\s-]+){0,2}?ranges?\s+of\s+estimates?\b",
     "a 'full range of estimates' claim"),
    (r"\branges?\s+of\s+(?:published\s+)?estimates?\b",
     "a 'range of estimates' claim"),
    (r"\b(?:above|below|beyond|outside)\s+every\s+(?:published\s+)?estimate\b",
     "an 'above/below every estimate' panel claim"),
    (r"\bevery\s+published\s+estimate\b",
     "an 'every published estimate' panel claim"),
    (r"\b(?:consensus|estimate|analyst|street|sell-side)\s+ranges?\b",
     "a consensus/analyst range claim"),
    (r"\b(?:highest|lowest)\s+(?:published\s+)?estimate\b",
     "a highest/lowest-estimate panel claim"),
)


_GUIDE_RANGE_RE = r"(?:guid(?:e|ed|ance)|manage(?:ment|d)|compan(?:y|y's)|target|prior\s+guide)"


def _lint_broker_disclosure(narrative: dict) -> list:
    import re
    out = []
    for field, text in _collect_narrative_strings(narrative):
        if re.split(r"[.\[]", field)[0] not in _BROKER_LINT_FIELDS:
            continue
        for pat, what in _BROKER_PATTERNS:
            m = re.search(pat, text or "", re.I)
            if not m:
                continue
            if pat != r"\bbrokers?\b" and re.search(_GUIDE_RANGE_RE, m.group(0), re.I):
                continue
            out.append(
                f"[disclosure] {field} carries {what}: {m.group(0)!r} in {text!r}. Broker "
                f"counts and broker ranges are INTERNAL — they belong in the consensus probe "
                f"and in stderr, never in flashVerdict, whatMatters, variantView, guidance, "
                f"forwardView or nextWatchItems. DELETE the clause; do not move it to a "
                f"caption, a footnote or another field. State the actual against consensus "
                f"and stop: 'came in $1.40 above consensus (+28.0%)'.")
            break
    return out


def _lint_banned_adjectives(narrative: dict) -> list:
    import re
    findings = []
    for field, text in _collect_narrative_strings(narrative):
        lower = text.lower()
        for word in _BANNED_ADJECTIVES:
            if re.search(rf"\b{re.escape(word)}\b", lower):
                findings.append(f"[style] {field}: banned adjective '{word}' -- use a variance descriptor instead")
    return findings


_BANNED_CONSTRUCTS = (
    "the debate is whether", "the real question is", "the story is",
    "the market looked past", "left holders with little to underwrite",
    "drove the sell-off", "towered over", "edged past", "grinding on",
)
_GUIDE_TAGS = ("[RAISED]", "[LOWERED]", "[MAINTAINED]", "[NEW]", "[WITHDRAWN]")


def _lint_house_style(narrative: dict, ticker: str = "") -> list:
    import re
    out = []
    _root = lambda f: re.split(r"[.\\[]", f)[0]
    fv = narrative.get("flashVerdict")
    if isinstance(fv, dict):
        for k in ("positives", "negatives"):
            v = (fv.get(k) or "").strip()
            n = len(v.split())
            if n > 12:
                out.append(f"[style] flashVerdict.{k} is {n} words; the rule is a scannable "
                           f"phrase of <=12. Use semicolons, drop lead-in clauses.")
    ir = (narrative.get("investmentRead") or "").strip()
    if ir:
        if len(ir) > 110:
            out.append(f"[style] investmentRead is {len(ir)} chars; it must fit ONE rendered "
                       f"line (<=90, and only rarely to ~110). It wraps as written.")
        if len(re.findall(r"[.?!]\s+[A-Z]", ir)) >= 1:
            out.append("[style] investmentRead runs to a second sentence. It is a one-line "
                       "callout: state the open question directly and stop.")
    for field, text in _collect_narrative_strings(narrative):
        low = text.lower()
        for c in _BANNED_CONSTRUCTS:
            if c in low:
                out.append(f"[style] {field}: banned construct '{c}' — attribute and quantify "
                           f"the forward question instead of framing it editorially.")
        if _root(field) != "guidance":
            for m in re.findall(r"\d+\.\d{3,}%", text):
                out.append(f"[style] {field}: '{m}' — percentages render at ONE decimal place "
                           f"(two is fine where it matches the scorecard).")
        for m in re.findall(r"\$\d+(?:,\d{3})*\.\dB\b", text):
            if not m.endswith(".0B"):
                out.append(f"[style] {field}: '{m}' — $B renders at TWO decimals in prose, to "
                           f"match the scorecard (a round figure like $15.0B is fine).")
    guides = [t for f, t in _collect_narrative_strings(narrative)
              if re.split(r"[.\[]", f)[0] == "guidance"]
    for g in guides:
        if any(ch.isdigit() for ch in g) and not any(t in g for t in _GUIDE_TAGS):
            out.append(f"[style] guidance bullet carries figures but no status tag "
                       f"({'/'.join(_GUIDE_TAGS)}): {g[:60]!r}")
    for fvb in [t for f, t in _collect_narrative_strings(narrative)
                if re.split(r"[.\[]", f)[0] == "forwardView"]:
        if any(ch.isdigit() for ch in fvb) and "[E]" not in fvb:
            out.append(f"[style] forwardView bullet carries figures but no [E] marker: "
                       f"{fvb[:60]!r}. Every figure in the Forward View block is a consensus "
                       f"ESTIMATE, not a company guide — mark it.")
        if any(t in fvb for t in _GUIDE_TAGS):
            out.append(f"[style] forwardView bullet carries a guidance status tag: "
                       f"{fvb[:60]!r}. The block exists because the company does NOT guide; "
                       f"[RAISED]/[MAINTAINED] describe a guide that was never given.")
    cn = (narrative.get("companyName") or "")
    if "(" in cn:
        out.append(f"[style] companyName {cn!r} carries a parenthetical. The renderer appends "
                   f"' (TICKER) - PERIOD' itself; an alias only risks a second masthead line.")
    if ticker:
        body = " ".join(t for f, t in _collect_narrative_strings(narrative)
                        if re.split(r"[.\[]", f)[0] in
                        ("flashVerdict", "whatMatters", "variantView", "investmentRead"))
        if body and f"({ticker})" not in body:
            out.append(f"[style] no first-mention '({ticker})' anywhere in the narrative body. "
                       f"Follow the company name with the ticker in parentheses on first use.")
    return out


_SHARE_COUNT_PAT = re.compile(
    r"\b(shares?\s+outstanding|share\s+count|diluted\s+shares?|basic\s+shares?|"
    r"weighted[\s-]average\s+shares?|shares?\s+repurchased|share\s+repurchases?|"
    r"buyback)\b", re.I)

_SIGNED_PCT = r"(?<![%\d])[+\-\u2212]\s?\d+(\.\d+)?\s?%"
_SHARE_MOVE_PAT = re.compile(
    r"\b(shares?|stock|ADRs?|equity|the name)\b[^.;]{0,40}?" + _SIGNED_PCT
    + r"|" + _SIGNED_PCT + r"[^.;]{0,25}?\b(shares?|stock|ADRs?)\b"
    + r"|\b(shares?|stock)\b[^.;]{0,30}?\b(rose|fell|jumped|slid|dropped|gained|sank|rallied|tumbled)\b",
    re.I)


def _quotes_share_move(text):
    m = _SHARE_MOVE_PAT.search(text or "")
    if not m or _SHARE_COUNT_PAT.search(text or ""):
        return None
    return m

_PLACEHOLDER_PAT = re.compile(
    r"^\s*(test\.?|tbd\.?|n/?a\.?|todo\.?|placeholder\.?|next print\.?|"
    r"guidance revisions?\.?|no quantified guidance in the structured feed.*|"
    r"scorecard below grades.*|variance versus consensus is as tabulated\.?|"
    r"line-by-line variance vs consensus\.?|.{0,14})\s*$", re.I)


def _norm(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def _metric_aliases(name):
    n = _norm(name)
    n = re.sub(r"\(?\s*ex[\s-]*1x[^)]*\)?", " ", n)
    n = re.sub(r"\b(ex\s*sbc|adj|adjusted|fte|total|consolidated)\b", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    out = {n}
    parts = n.split()
    if len(parts) > 2:
        out.add(" ".join(parts[:2]))
        out.add(" ".join(parts[-2:]))
    _GENERIC_HEAD = {"core", "net", "gross", "average", "underlying", "reported", "same",
                     "comparable", "operating", "free", "basic", "diluted"}
    if parts and parts[0] not in _GENERIC_HEAD:
        out.add(parts[0])
    elif len(parts) > 1:
        out.add(" ".join(parts[:2]))
    return {a for a in out if len(a) >= 3}


_GENERIC_FIN = {"revenue", "revenues", "sales", "income", "margin", "ebitda", "ebit", "eps", "profit",
                "growth", "volume", "units", "orders", "bookings", "segment"}


def fact_coverage_lint(computed: dict, narrative: dict, strict: bool = False):
    fatal, warn = [], []
    body_fields = ("flashVerdict", "whatMatters", "variantView", "investmentRead")
    def _root(f):
        return re.split(r"[.\[]", f)[0]
    body = _norm(" ".join(t for f, t in _collect_narrative_strings(narrative)
                          if _root(f) in body_fields))
    guid = " ".join(t for f, t in _collect_narrative_strings(narrative)
                    if _root(f) == "guidance").strip()
    watch = [t for f, t in _collect_narrative_strings(narrative)
             if _root(f) == "nextWatchItems"]

    for field, text in _collect_narrative_strings(narrative):
        if _PLACEHOLDER_PAT.match(text or ""):
            fatal.append(f"[facts] {field} is placeholder or contentless text: {text!r}. "
                         f"Write the real finding or remove the bullet.")

    for field, text in _collect_narrative_strings(narrative):
        _m = _quotes_share_move(text)
        if _m:
            fatal.append(
                f"[facts] {field} quotes a share-price move ({_m.group(0)!r}), but the flash no "
                f"longer carries a market reaction (removed in v7.6.0 — no live price feed). "
                f"There is nothing on the page to check it against. Remove the clause; state "
                f"the result, not the market's response to it.")

    warns = " ".join(computed.get("warnings") or [])
    no_rows = "[no guidance rows]" in warns
    _guid_words = len(re.findall(r"[A-Za-z]{2,}", guid))
    _guid_figs = len(re.findall(r"\d", guid))
    _guid_placeholder = guid.strip().lower().rstrip(".") in (
        "", "-", "--", "n/a", "na", "none", "nil", "tbd", "no guidance", "not applicable")

    _gstat = computed.get("guidanceStatus") or {}
    _gstate = _gstat.get("state") or ("empty" if no_rows else "ok")
    _gsource = (_gstat.get("declared_source") or "mcp").strip().lower()
    _stale = _gstate == "stale" or "[guidance predates print]" in warns

    _slot_exempt = _gsource in ("none", "unavailable")
    if no_rows and not _slot_exempt and (_guid_placeholder or _guid_words < 3):
        fatal.append("[facts] get_guidance returned ZERO rows and narrative.guidance is empty "
                     "or near-empty. Say plainly, in your own words, that the company issues "
                     "no guidance (or that the fetch failed) — never leave the reader to infer "
                     "it from a bare section. Any wording is accepted; it just has to be there. "
                     "If the company genuinely does not guide, declare guidance_source='none' "
                     "and fill forwardView[]; if nothing could be retrieved, declare "
                     "'unavailable' and record the attempt on every rung.")
    if not no_rows and not _slot_exempt and _guid_figs == 0:
        fatal.append("[facts] get_guidance returned rows but narrative.guidance carries no "
                     "figures at all, so the guides Python holds are nowhere on the page. Read "
                     "them and summarise the actual ranges. (A sentence noting what was NOT "
                     "guided is fine and expected — it just cannot be the whole section.)")


    _VALID_SOURCES = ("mcp", "release", "transcript", "none", "unavailable")
    _attempts = []
    _prov = computed.get("provenance") or narrative.get("_provenance") or {}
    if isinstance(_prov, dict) and isinstance(_prov.get("document_attempts"), list):
        _attempts = [a for a in _prov["document_attempts"] if isinstance(a, dict)]
    _parsed_ok = [a for a in _attempts if str(a.get("result", "")).lower() == "success"]
    _doc_sourced = _gsource in ("release", "transcript")

    if _gsource not in _VALID_SOURCES:
        fatal.append(f"[facts] guidance_source={_gsource!r} is not one of "
                     f"{'/'.join(_VALID_SOURCES)}. It records WHERE the guides came from and "
                     f"every gate below reads it.")

    if _gsource == "none":
        if not _attempts:
            fatal.append(
                "[facts] guidance_source='none' claims a document was read and carried no "
                "guide, but provenance.document_attempts is empty — nothing records that "
                "anything was read. Parse the release (any size) or the transcript and record "
                "the attempt. If nothing could be parsed, the source is 'unavailable', NOT "
                "'none': one says the company does not guide, the other says we could not "
                "check, and they are not interchangeable.")
        elif not _parsed_ok:
            _res = ", ".join(sorted({str(a.get('result', '?')) for a in _attempts}))
            fatal.append(
                f"[facts] guidance_source='none' claims a document carried no guide, but every "
                f"recorded attempt failed ({_res}). That is 'unavailable', not 'none'. Never "
                f"report a failed parse as a company that does not guide.")

    if _gsource == "unavailable":
        if _guid_figs > 0:
            warn.append("[facts] guidance_source='unavailable' says nothing could be read, yet "
                        "narrative.guidance quotes figures. Name the source those came from.")
        if not any(str(a.get("result", "")).lower() in ("timeout", "refusal", "empty", "error",
                                                        "skipped") for a in _attempts):
            warn.append("[facts] guidance_source='unavailable' with no failed attempt recorded "
                        "in provenance.document_attempts. Record what was tried and how it "
                        "failed — the claim is only as good as its evidence.")

    if _doc_sourced and _attempts and not _parsed_ok:
        fatal.append(
            f"[facts] guidance_source={_gsource!r} says the guides were read off a parsed "
            f"document, but provenance.document_attempts records no successful parse. Either "
            f"the attempt log is wrong or the guides did not come from a document.")

    if len(_attempts) > 4:
        warn.append(f"[facts] {len(_attempts)} document parse attempts recorded; the budget is "
                    f"4 total (2 per document). A parse that has not returned is a failing "
                    f"parse — move to the next rung instead of retrying.")
    _by_path = {}
    for a in _attempts:
        _by_path[a.get("path", "?")] = _by_path.get(a.get("path", "?"), 0) + 1
    for _p, _n in _by_path.items():
        if _n > 2:
            warn.append(f"[facts] {_n} attempts on one document ({str(_p)[-48:]}). The cap is 2; "
                        f"after that the document is a dead end, not a slow one.")

    if (_gstate in ("empty", "stale") or no_rows or _stale) and _gsource == "mcp":
        _why = ("returned ZERO rows" if _gstate == "empty" or no_rows else
                f"returned only guides dated {_gstat.get('newest_guide_date') or 'earlier'}, "
                f"before the {_gstat.get('call_date') or 'earnings'} call")
        fatal.append(
            f"[facts] get_guidance {_why}, but input.json declares guidance_source='mcp'. The "
            f"structured feed cannot be the source of a guide it does not carry. Parse the "
            f"Earnings Release — a fixed Tier B slot, and the one document guaranteed to "
            f"carry the guidance range — then set guidance_source to 'release' (guides found) "
            f"or 'none' (release parsed, no guide in it). Do not resolve this by rewording "
            f"the section.")

    if _gsource == "none" and _guid_figs > 0:
        warn.append("[facts] guidance_source='none' declares the release carries no guide, yet "
                    "narrative.guidance quotes figures. One of the two is wrong — if the "
                    "figures came from the release, the source is 'release'.")

    if _doc_sourced and _guid_figs == 0:
        _doc = "Earnings Release" if _gsource == "release" else "Earnings Transcript"
        fatal.append(f"[facts] guidance_source={_gsource!r} declares the guides were read off "
                     f"the parsed {_doc}, but narrative.guidance carries no figures at all. "
                     f"Quote the ranges it gives, declare 'none' if it gives none, or "
                     f"'unavailable' if it could not be read.")

    if _gstate == "undated" and _gsource == "mcp":
        warn.append("[facts] guidance rows carry no usable date, or no call date resolved, so "
                    "the staleness check could not run. Check the quoted guides against the "
                    "Earnings Release before shipping — a superseded guide reads exactly like "
                    "a current one.")

    _slot_fatal, _slot_warn = _guidance_slot_gate(narrative, _gsource, _prov)
    fatal.extend(_slot_fatal)
    warn.extend(_slot_warn)

    rows = list(computed.get("financialMetrics") or []) + list(computed.get("operatingMetrics") or [])
    unmentioned = []
    _op = {id(r) for r in computed.get("operatingMetrics") or []}
    for r in rows:
        if r.get("beatMiss") not in ("Beat", "Miss"):
            continue
        if r.get("ltcode") == "COMPUTED":
            continue
        _al = _metric_aliases(r.get("name", ""))
        if id(r) in _op:
            _al = {a for a in _al if len(a.split()) > 1 or a not in _GENERIC_FIN} or _al
        if not any(re.search(r"\b" + re.escape(a) + r"\b", body) for a in _al):
            unmentioned.append(f"{r.get('name')} ({r.get('beatMiss')})")
    if unmentioned:
        msg = ("[facts] graded rows absent from the narrative body: "
               + "; ".join(unmentioned) + ". The scorecard says they beat or missed; the prose "
               "never mentions them.")
        (fatal if strict else warn).append(msg)

    for w in (computed.get("warnings") or []):
        m = re.match(r"(LARGE SURPRISE, VERIFY BASIS|OUTSIDE BROKER RANGE) — '([^']+)'", w)
        if m and not any(a in body for a in _metric_aliases(m.group(2))):
            warn.append(f"[facts] '{m.group(2)}' carries a {m.group(1)} flag but is not "
                        f"mentioned in the narrative. The reader sees the number without "
                        f"the caveat.")

    if watch and all(len(_norm(t).split()) <= 3 for t in watch):
        fatal.append("[facts] every nextWatchItems entry is a bare noun phrase. Watch items "
                     "must name what is being watched and against which number or guide.")
    return fatal, warn


def _guidance_slot_gate(narrative: dict, gsource: str, provenance: dict):
    fatal, warn = [], []
    g = [s for s in (narrative.get("guidance") or []) if str(s).strip()]
    fv = [s for s in (narrative.get("forwardView") or []) if str(s).strip()]
    src = (gsource or "mcp").strip().lower()

    if src in ("mcp", "release", "transcript"):
        if not g:
            fatal.append(
                f"[facts] narrative.guidance is empty but guidance_source={src!r} says guides "
                f"were retrieved. An empty guidance section is only allowed on the "
                f"'unavailable' path (nothing could be read) or the 'none' path (the company "
                f"does not guide, and forwardView carries the Street's numbers instead).")
        if fv:
            fatal.append(
                f"[facts] narrative.forwardView is populated but guidance_source={src!r}. The "
                f"Forward View block is the alternative to the Guidance Snapshot on the 'none' "
                f"path ONLY — a company that guides gets its guides, not the Street's "
                f"estimates. Drop forwardView, or correct guidance_source.")
    elif src == "none":
        if not fv:
            fatal.append(
                "[facts] guidance_source='none' says the documents were read and the company "
                "issues no guidance, but narrative.forwardView is empty. A non-guiding issuer "
                "gets a Forward View block instead of a Guidance Snapshot: next-quarter and FY "
                "consensus for revenue and adjusted EPS, each marked [E], taken from "
                "get_estimates. Leaving the slot blank hands the reader a page with no forward "
                "numbers on it at all.")
        if g:
            warn.append(
                "[facts] guidance_source='none' and narrative.guidance is populated. The "
                "Guidance Snapshot renders and the Forward View block does NOT — a company "
                "declared non-guiding should not be carrying guidance bullets. Move the "
                "content to forwardView, or correct guidance_source.")
    elif src == "unavailable":
        if fv:
            fatal.append(
                "[facts] guidance_source='unavailable' says nothing could be retrieved, yet "
                "narrative.forwardView is populated. Forward View is the 'none' path — a "
                "company proven not to guide. If the estimates were retrievable, the guidance "
                "routes were not all exhausted.")
        _attempts = (provenance or {}).get("guidance_attempts")
        _missing, _routes = run_math.guidance_attempt_receipt(_attempts)
        if _missing:
            fatal.append(
                "[facts] guidance_source='unavailable' with no recorded failed attempt on: "
                + ", ".join(_missing)
                + ". provenance.guidance_attempts must carry every rung of the fallback chain "
                  "and each must record a FAILURE, because an absent guidance block is exactly "
                  "what skipping the work looks like. Required shape: "
                  '{"feed": {"rows": <int>, "result": "empty|stale"}, '
                  '"release": {"attempts": <int>, "path": "...", "result": "timeout|refusal|'
                  'empty|error|absent"}, "transcript": {"attempts": <int>, "path": "...", '
                  '"result": "..."}}. Recorded so far: '
                + ("; ".join(_routes) if _routes else "nothing")
                + ". If a rung actually succeeded, the source is not 'unavailable'.")
    return fatal, warn


def _run_style_linters(narrative: dict, ticker: str = "") -> list:
    return _lint_banned_adjectives(narrative) + _lint_house_style(narrative, ticker)


def _fits_one_page(pages) -> bool:
    return pages is not None and pages <= 1


def _pdf_page_count(pdf_path):
    if not pdf_path or not os.path.isfile(pdf_path):
        return None
    try:
        from pypdf import PdfReader
        return len(PdfReader(pdf_path).pages)
    except Exception:
        pass
    import shutil as _sh
    if _sh.which("pdfinfo"):
        try:
            out = subprocess.run(["pdfinfo", pdf_path], capture_output=True, text=True, timeout=30).stdout
            for line in out.splitlines():
                if line.lower().startswith("pages:"):
                    return int(line.split(":", 1)[1].strip())
        except Exception:
            pass
    try:
        import re as _re2
        blob = open(pdf_path, "rb").read()
        n = len(_re2.findall(rb"/Type\s*/Page(?![sA-Za-z])", blob))
        return n or None
    except Exception:
        return None


def _convert_to_pdf(docx_path: str):
    import shutil
    abs = os.path.abspath(docx_path)
    out_dir = os.path.dirname(abs) or "."
    pdf_path = os.path.splitext(abs)[0] + ".pdf"
    _profile = os.path.join(_SCRATCH_DIR or out_dir, ".lo_profile_%d" % os.getpid())
    _pdf_before = os.path.getmtime(pdf_path) if os.path.isfile(pdf_path) else None
    def _is_fresh():
        if not os.path.isfile(pdf_path):
            return False
        return _pdf_before is None or os.path.getmtime(pdf_path) > _pdf_before
    for exe in ("soffice", "libreoffice"):
        if shutil.which(exe):
            for _attempt, _extra in enumerate(([], ["-env:UserInstallation=file://" + _profile])):
                try:
                    _cp = subprocess.run([exe, "--headless"] + _extra
                                   + ["--convert-to", "pdf", "--outdir", out_dir, abs],
                                   capture_output=True, text=True, timeout=180)
                    if _cp.returncode == 0 and _is_fresh():
                        _note = f"PDF written via {exe}"
                        if _attempt:
                            _note += " (retried with a private profile after a first failure)"
                        try:
                            shutil.rmtree(_profile, ignore_errors=True)
                        except Exception:
                            pass
                        return pdf_path, _note
                except Exception as e:
                    if _attempt:
                        shutil.rmtree(_profile, ignore_errors=True)
                        return None, f"PDF conversion via {exe} failed twice: {e}"
            shutil.rmtree(_profile, ignore_errors=True)
    try:
        from docx2pdf import convert as _d2p
        _d2p(abs, pdf_path)
        if _is_fresh():
            return pdf_path, "PDF written via docx2pdf"
    except Exception:
        pass
    if _pdf_before is not None and os.path.isfile(pdf_path):
        try:
            os.replace(pdf_path, pdf_path + ".stale")
        except OSError:
            pass
        return None, ("PDF conversion FAILED and a .pdf from an earlier run was found at the "
                      "output path; it has been renamed to *.pdf.stale so it cannot be "
                      "mistaken for this run's twin. DOCX delivered.")
    if shutil.which("soffice") or shutil.which("libreoffice"):
        return None, ("PDF conversion FAILED — LibreOffice is installed but produced no fresh "
                      "file (non-zero exit, both the default and a private profile). DOCX "
                      "delivered; page count is UNVERIFIED.")
    return None, "PDF conversion skipped — no LibreOffice/soffice or docx2pdf found; DOCX delivered"


def parse_disp(v):
    if v is None:
        return None
    t = str(v).strip()
    if t in ("", "N/A", "-", "--", "\u2014"):
        return None
    neg = bool(re.match(r"^\s*-", t) or re.match(r"^\s*[^\d\s+-]{1,8}\s*-\s*\d", t))
    m = re.search(r"([\d,]*\.?\d+)\s*(T|B|M|K)?", t.replace("%", "").replace("ppt", ""))
    if not m:
        return None
    x = float(m.group(1).replace(",", ""))
    x *= {"T": 1e12, "B": 1e9, "M": 1e6, "K": 1e3}.get((m.group(2) or "").upper(), 1)
    return -x if neg else x


LB_HINT = re.compile("|".join(run_math._LOWER_IS_BETTER_TERMS
                              + run_math._WRONG_SLOT_TERMS), re.I)


_EXCLUSION_CLAUSE_RE = re.compile(
    r"\(\s*(?:ex|excl|excluding|before|pre)\b[^)]*\)"
    r"|\b(?:ex|excl|excluding|before|pre)[-\s]+(?:1x\s+items?\s*&?\s*)?"
    r"(?:amorti[sz]\w*|depreciat\w*|d&a|dd&a|sbc|stock[-\s]comp\w*)",
    re.I)


def _strip_exclusions(name: str) -> str:
    return _EXCLUSION_CLAUSE_RE.sub(" ", str(name or "")).strip()


def lb_hint(name: str):
    return LB_HINT.search(_strip_exclusions(name))


def verdict_gate(computed):
    errors, warnings = [], []
    rows = list(computed.get("financialMetrics") or []) + list(computed.get("operatingMetrics") or [])
    for row in rows:
        tag = (row.get("beatMiss") or "").strip()
        if tag not in ("Beat", "Miss", "In-Line"):
            continue
        a, e = parse_disp(row.get("currentActual")), parse_disp(row.get("currentEstimate"))
        if a is None or e is None:
            continue
        name, lt = row.get("name") or "", row.get("ltcode") or ""
        lb = run_math._lower_is_better(name, lt)
        pct = run_math._registry_pct(lt)
        if pct is None:
            pct = str(row.get("currentActual", "")).strip().endswith("%")
        if pct:
            diff = a - e
            thr = run_math.PCT_BEAT_MISS_THRESHOLD_PPT
            raw = "Beat" if diff >= thr else ("Miss" if diff <= -thr else "In-Line")
        else:
            dp = (a - e) / abs(e) * 100.0 if e else 0.0
            thr = run_math.BEAT_MISS_THRESHOLD * 100
            raw = "Beat" if dp >= thr else ("Miss" if dp <= -thr else "In-Line")
        expected = raw if raw == "In-Line" else (
            ("Miss" if raw == "Beat" else "Beat") if lb else raw)
        if tag != expected:
            errors.append(
                f"{name} ({lt}): actual {row.get('currentActual')} vs estimate "
                f"{row.get('currentEstimate')}, lower_is_better={lb} -> expected "
                f"{expected} but row renders {tag}")
        if not lb and lb_hint(name):
            warnings.append(
                f"POLARITY UNDECLARED - '{name}' ({lt}) reads as a cost/ratio metric where "
                f"LOWER is better, but no inversion was applied, so its '{tag}' verdict may be "
                f"backwards. Verify this row by hand before shipping.")
        elif not run_math._polarity_declared(name, lt):
            warnings.append(
                f"POLARITY UNRESOLVED - '{name}' ({lt}) has no registry entry and matches no "
                f"polarity term, so it was graded HIGHER-is-better BY DEFAULT, not by "
                f"declaration. If a LOWER number is better for this metric then its '{tag}' "
                f"verdict is backwards. Decide it: flash.py set direction {lt} higher|lower.")
    return errors, warnings


def main(input_path: str, narrative_path: str, output_docx: str,
         strict_polarity: bool = False, require_consensus: bool = True,
         strict_facts: bool = False, allow_stale_period: bool = False,
         allow_render_defects: bool = False, allow_unit_sanity: bool = False,
         allow_copy_check: bool = False) -> int:
    if not os.path.isfile(input_path):
        return _err(f"input.json not found at {input_path}")
    if not os.path.isfile(narrative_path):
        return _err(f"narrative.json not found at {narrative_path}")
    with open(input_path, "r", encoding="utf-8") as f:
        inp = json.load(f)
    if isinstance(inp, dict) and inp.get("mcp_responses"):
        try:
            inp["mcp_responses"] = run_math.expand_short_keys(inp["mcp_responses"])
        except ValueError as e:
            return _err("short_key_ambiguous - input.json cannot be read:\n  %s" % e)
    with open(narrative_path, "r", encoding="utf-8") as f:
        narrative = json.load(f)

    ticker = (inp.get("ticker") or "").upper()
    period = inp.get("period") or ""
    prior_period = inp.get("prior_period") or ""
    if not ticker or not period:
        return _err("input.json must include 'ticker' and 'period'")

    kpi_map = run_math._unwrap_mcp_payload(inp.get("kpi_mapping")) or {}
    _no_kpis = bool(kpi_map.get("no_kpis_available")) if isinstance(kpi_map, dict) else False
    if not _no_kpis and (not kpi_map or not kpi_map.get("top_kpi_1_ltcode")):
        return _err(
            f"input.json is missing 'kpi_mapping'. In v4 the KPI list comes from the MCP: "
            f"call search_tickerid('{ticker}') then search_lineitem(ticker_id, queries='all', "
            f"filter='top_kpi'), and write the returned items into input.json['kpi_mapping'] as "
            f"top_kpi_1..N (name) + top_kpi_1..N_ltcode. The company_master.csv has been removed."
        )

    master_candidates = run_math.kpis_from_mapping(kpi_map)
    if not master_candidates and not _no_kpis:
        return _err(f"kpi_mapping for {ticker} has no top_kpi_1..N_ltcode entries. If the "
                    f"escalation ladder genuinely came back empty at every tier, set "
                    f"kpi_mapping.no_kpis_available=true to render the financial scorecard alone.")
    master_ltcodes = [c["ltcode"] for c in master_candidates]
    _lt_names = {c["ltcode"]: c.get("name") or c["ltcode"] for c in master_candidates}
    _lt_tiers = {c["ltcode"]: c.get("tier", "top_kpi") for c in master_candidates}
    gate_warnings = []

    import copy_check as _cc
    _cc_blocks, _cc_warns = _cc.check(inp)
    gate_warnings.extend(_cc_warns)
    if _cc_blocks and not allow_copy_check:
        _note(gate_warnings)
        _err("copy_check_failed - a value in input.json does not match the MCP response it came "
             "from. Re-copy the named rows from the tool output, then re-run:\n  "
             + "\n  ".join(_cc_blocks))
        return 7


    mcp_raw = inp.get("mcp_responses", {}) or {}
    mcp = {k: run_math._unwrap_mcp_payload(v) for k, v in mcp_raw.items()}

    eight_k_actuals = inp.get("eight_k_actuals") or {}
    if eight_k_actuals:
        mcp_resp = inp.setdefault("mcp_responses", {})
        ga = mcp_resp.get("get_actuals")
        if not isinstance(ga, dict):
            ga = {"status": "success", "data": []}
            mcp_resp["get_actuals"] = ga
        rows = ga.setdefault("data", [])
        if not isinstance(rows, list):
            rows = []
            ga["data"] = rows
        existing = {(r.get("LTCODE"), r.get("FISCALPERIOD")) for r in rows if isinstance(r, dict)}
        for lt, val in eight_k_actuals.items():
            if (lt, period) in existing or val is None:
                continue
            av = abs(float(val))
            if av >= 1e9:   raw = f"$ {val/1e9:.4f}B"
            elif av >= 1e6: raw = f"$ {val/1e6:.4f}M"
            else:           raw = f"$ {val:.4f}"
            rows.append({
                "TICKER_ID": "", "UIC": "", "LTCODE": lt,
                "LINEITEM": "", "FISCALPERIOD": period,
                "VALUE": raw, "SOURCE_DERIVED": "8K-fallback",
                "QUARTERENDDATE": "",
            })
        mcp = {k: run_math._unwrap_mcp_payload(v) for k, v in mcp_resp.items()}

    cur_actuals, _, _, _ = run_math.build_period_dict(mcp.get("get_actuals", {}), period)
    estimates, _, _, _ = run_math.build_period_dict(mcp.get("get_estimates", {}), period)

    missing_actuals = [lt for lt in master_ltcodes if lt not in cur_actuals]
    missing_estimates = [lt for lt in master_ltcodes if lt not in estimates]

    _incomplete = sorted(set(missing_actuals))
    if _incomplete:
        _why = {lt: ", ".join(
                    ([] if lt in cur_actuals else ["no actual"]) +
                    ([] if lt in estimates else ["no estimate"]))
                for lt in _incomplete}
        gate_warnings.append(
            "KPI dropped - no data: " + "; ".join(f"{lt} ({_why[lt]})" for lt in _incomplete))
        _keep = [lt for lt in master_ltcodes if lt not in _incomplete]
        if not _keep:
            _keep = None
    if _incomplete and _keep:
        _newmap = {"ticker": (kpi_map or {}).get("ticker", ticker)}
        for _i, _lt in enumerate(_keep, start=1):
            _newmap[f"top_kpi_{_i}"] = _lt_names.get(_lt, _lt)
            _newmap[f"top_kpi_{_i}_ltcode"] = _lt
            _newmap[f"top_kpi_{_i}_tier"] = _lt_tiers.get(_lt, "top_kpi")
        kpi_map = _newmap
        master_ltcodes = _keep

    _final = [c for c in master_candidates if c["ltcode"] not in set(_incomplete)]
    if not _final:
        gate_warnings.append(
            "NO OPERATING KPIs — the operating block is OMITTED entirely (no blank rows). "
            "No tagged or discoverable KPI "
            "for this ticker had a reported actual. This flash covers Key Financial Metrics "
            "only; say so in flashVerdict.net so the reader is not left guessing.")
    elif len(_final) < run_math.MIN_KPIS:
        gate_warnings.append(
            f"THIN KPI TABLE — only {len(_final)} operating KPI(s) available against a target "
            f"of {run_math.MIN_KPIS}. Escalate before shipping: search_lineitem with "
            f"filter='company_kpi', then a keyword sweep with no filter (see SKILL.md Step 1). "
            f"Rendered anyway rather than refusing.")

    missing_narrative = [k for k in REQUIRED_NARRATIVE_FIELDS
                         if k not in _CONDITIONAL_NARRATIVE_FIELDS and not narrative.get(k)]
    if missing_narrative:
        return _err(
            f"narrative.json is missing required fields: {missing_narrative}. "
            f"Required: {REQUIRED_NARRATIVE_FIELDS} "
            f"(of which {list(_CONDITIONAL_NARRATIVE_FIELDS)} are conditional on "
            f"guidance_source — see the guidance-slot table in SKILL.md)"
        )

    _broker_findings = _lint_broker_disclosure(narrative)
    if _broker_findings:
        _note(_broker_findings)
        return _err(
            "broker_disclosure_failed — broker counts and broker ranges may not appear in "
            "narrative prose:\n  " + "\n  ".join(_broker_findings)
            + "\n\nThis is a REMOVAL. Do not relocate the wording to a caption, a footnote or "
              "another field; the page carries the actual against consensus and nothing about "
              "the panel behind it. The counts and ranges stay in the consensus probe and in "
              "stderr, where they are analyst QC. There is no override flag.")

    _note(gate_warnings)
    narrative, flash_warnings = shape_narrative(narrative, flash_mode=FLASH_MODE)
    if flash_warnings:
        print("\n--- flash contract / de-dup (non-fatal) ---", file=sys.stderr)
        for w in flash_warnings:
            print(f"  {w}", file=sys.stderr)
        print("--------------------------------------------", file=sys.stderr)

    style_warnings = _run_style_linters(narrative, ticker)
    _note(style_warnings)
    if style_warnings:
        print("\n--- narrative style warnings (non-fatal) ---", file=sys.stderr)
        for w in style_warnings:
            print(f"  {w}", file=sys.stderr)
        print("--------------------------------------------", file=sys.stderr)

    inp["kpi_mapping"] = kpi_map
    inp.pop("kpi_override", None)

    if allow_stale_period:
        inp["allow_stale_period"] = True

    global _SCRATCH_DIR
    _run_tag = "%s_%d" % (
        os.path.splitext(os.path.basename(output_docx))[0][:40].replace(" ", "_"),
        os.getpid(),
    )
    out_dir = os.path.join(os.path.dirname(output_docx) or ".", "_pipeline_scratch", _run_tag)
    os.makedirs(out_dir, exist_ok=True)
    _SCRATCH_DIR = out_dir

    canonical_input_path = os.path.join(out_dir, "_canonical_input.json")
    with open(canonical_input_path, "w", encoding="utf-8") as f:
        json.dump(inp, f, indent=2)
    rc = run_math.main(canonical_input_path, out_dir, require_estimate=require_consensus,
                       allow_unit_sanity=allow_unit_sanity)
    if rc != 0:
        _cp = os.path.join(out_dir, "computed.json")
        if os.path.isfile(_cp):
            try:
                with open(_cp, "r", encoding="utf-8") as f:
                    _c = json.load(f)
                _note(_c.get("warnings"))
                if _c.get("stale_period"):
                    _f = _c.get("stale_facts") or {}
                    _payload = {
                        "status": "needs_user_decision",
                        "reason": "data_not_loaded",
                        "facts": _f,
                        "detail": _c.get("errors", []),
                        "options": [
                            {"id": "wait",
                             "label": "Wait for the data to load, then re-run",
                             "detail": "Ingestion typically completes within ~30 minutes of the "
                                       "call. Re-running then produces the flash for the period "
                                       "that just reported."},
                            {"id": "prior",
                             "label": "Run the prior period now, clearly labelled",
                             "detail": "Re-run with --allow-stale-period. The note renders for "
                                       "%s and MUST state in flashVerdict.net that it is the "
                                       "PRIOR period and that the latest print is not yet "
                                       "loaded." % (_f.get("resolved_period") or "the prior period")},
                        ],
                        "instruction": "ASK the user which option they want. Do NOT re-run with "
                                       "--allow-stale-period unless they choose it. Do NOT present "
                                       "this as a failure — it is a freshness check working "
                                       "correctly.",
                    }
                    print(json.dumps(_payload, indent=2))
                    return 1
                if _c.get("unit_check_failed") and not allow_unit_sanity:
                    return _err(
                        "UNIT SANITY CHECK FAILED — rendering blocked to avoid misstating a figure.\n"
                        + "\n".join(f"  - {e}" for e in _c.get("unit_errors", []))
                        + f"\nFIX: {_c.get('reason', '')}\n"
                        + "If this is a genuine per-unit metric the name pattern does not cover, "
                        + "--allow-unit-sanity downgrades this to a warning. Prefer adding the "
                        + "metric to _PER_UNIT_NAME_RE so the next run is right by default."
                    )
            except Exception:
                pass
        try:
            _cp = os.path.join(out_dir, "computed.json")
            if os.path.isfile(_cp):
                with open(_cp, "r", encoding="utf-8") as _f:
                    _note((json.load(_f) or {}).get("warnings"))
        except Exception:
            pass
        if rc == 5:
            _pm = _pp = None
            try:
                _cp5 = os.path.join(out_dir, "computed.json")
                if os.path.isfile(_cp5):
                    with open(_cp5, "r", encoding="utf-8") as _f5:
                        _c5 = json.load(_f5) or {}
                    if _c5.get("no_financial_rows"):
                        _pm, _pp = _c5.get("parse_matched"), _c5.get("parse_ok")
            except Exception:
                pass
            _head = ("EMPTY FINANCIAL SPINE — zero metric rows were built, which "
                     "_render_internal.js rejects outright ('financialMetrics must be a "
                     "non-empty array').\n")
            if _pm == 0:
                return _err(
                    _head +
                    "CAUSE: no get_actuals row matched FISCALPERIOD for the resolved period.\n"
                    "FIX: re-probe get_actuals for the same ltcode list with NO fiscal_period, "
                    "read the FISCALPERIOD labels actually present, and re-run with one of "
                    "those. This is the one exit-5 shape where re-probing the period IS the "
                    "fix. See the run_math stderr line above."
                )
            if _pm and _pp:
                return _err(
                    _head +
                    "CAUSE: %d get_actuals row(s) matched the period and %d of their VALUE "
                    "strings parsed, so parsing is NOT at fault and the period label is "
                    "correct.\n"
                    "FIX: the financial-spine ltcodes were not in LTCODE_LIST for "
                    "this fetch. resolve_fin_ltcode SELECTS from the payload and never "
                    "fetches, so a rung you did not request is unreachable. Re-fetch with the "
                    "full spine list from SKILL.md Step 1." % (_pm, _pp)
                )
            return _err(
                _head +
                "CAUSE: rows matched the resolved period but NOT ONE of their VALUE strings "
                "parsed. The PERIOD LABEL IS NOT THE PROBLEM — do not re-probe it.\n"
                "FIX: the usual cause is a currency token missing from _CUR_RE in "
                "run_math.py (e.g. a non-USD reporter); add it to the 3-letter alternation. "
                "The other is a VALUE column that is not a number at all. See the stderr "
                "line above for the unparsed sample strings."
            )
        return _err(f"run_math.py failed with exit code {rc}")

    computed_path = os.path.join(out_dir, "computed.json")
    if not os.path.isfile(computed_path):
        return _err(f"run_math.py did not produce computed.json at {computed_path}")
    with open(computed_path, "r", encoding="utf-8") as f:
        computed = json.load(f)
    _note(computed.get("warnings"))

    _tick_fatal = [w for w in (computed.get("warnings") or [])
                   if str(w).startswith(("[ticker unresolved]", "[ticker row stale]",
                                         "[ticker row implausible]",
                                         "[ticker resolution unrecorded]"))]
    if _tick_fatal:
        return _err("ticker_identity_failed - refusing to render a flash whose subject "
                    "company is not confirmed:\n  " + "\n  ".join(_tick_fatal))

    _parse_disp = parse_disp

    _recon = []
    for _row in list(computed.get("financialMetrics") or []) + list(computed.get("operatingMetrics") or []):
        _a, _e, _d = (_parse_disp(_row.get(k)) for k in ("currentActual", "currentEstimate", "deltaAbsolute"))
        if None in (_a, _e, _d):
            continue
        _scale = max(abs(_a), abs(_e), 1.0)
        if abs((_a - _e) - _d) > _scale * 1e-9 + 1e-6:
            _recon.append(
                f"{_row.get('name')}: {_row.get('currentActual')} - {_row.get('currentEstimate')} "
                f"= {(_a - _e):,.4g} but deltaAbsolute renders {_row.get('deltaAbsolute')}"
            )
    if _recon:
        return _err("reconciliation_failed - displayed rows do not close:\n  " + "\n  ".join(_recon))

    _verdict, polarity_warnings = verdict_gate(computed)
    if _verdict:
        return _err("verdict_failed - rendered Beat/Miss does not follow from the "
                    "rendered numbers:\n  " + "\n  ".join(_verdict))
    _undeclared = [w for w in polarity_warnings if w.startswith("POLARITY UNDECLARED")]
    if _undeclared and strict_polarity:
        return _err("polarity_undeclared (--strict-polarity) - refusing to render a "
                    "possibly inverted verdict:\n  " + "\n  ".join(_undeclared))

    if computed.get("kpi_selection_needed"):
        return _err(
            f"run_math.py exited with kpi_selection_needed: {computed.get('reason')}\n"
            f"kpi_mapping was absent or carried no top_kpi_1_ltcode. Run the escalation "
            f"ladder: search_lineitem filter='top_kpi', then 'company_kpi', then "
            f"'industry_kpi'. If ALL THREE are empty this is not an error — re-run with "
            f'kpi_mapping = {{"ticker": "<TICKER>", "no_kpis_available": true, '
            f'"tiers_checked": "top_kpi, company_kpi, industry_kpi"}} and the flash will '
            f"render the financial scorecard alone."
        )

    _fact_fatal, _fact_warn = fact_coverage_lint(computed, narrative, strict=strict_facts)
    _note(_fact_warn)
    if _fact_fatal:
        return _err("fact_coverage_failed — the narrative does not carry the facts Python "
                    "computed:\n  " + "\n  ".join(_fact_fatal))
    if _fact_warn:
        print("\n--- fact coverage (non-fatal) ---", file=sys.stderr)
        for w in _fact_warn:
            print(f"  {w}", file=sys.stderr)
        print("---------------------------------", file=sys.stderr)

    earnings_date = computed.get("earningsDate") or ""
    report_date = ""
    try:
        if earnings_date:
            d = datetime.strptime(earnings_date, "%Y-%m-%d")
            report_date = d.strftime("%B %-d, %Y") if os.name != "nt" else d.strftime("%B %#d, %Y")
    except Exception:
        report_date = earnings_date

    next_earnings_date = ""
    for row in run_math._rows(mcp.get("get_call_date", {})):
        fp = row.get("FISCALPERIOD") or row.get("fiscal_period")
        cd = row.get("CALL_DATE") or row.get("call_date")
        if fp and cd and fp != period and earnings_date and cd > earnings_date:
            if not next_earnings_date or cd < next_earnings_date:
                next_earnings_date = cd

    data = {
        "_computed_by": "run_math",
        "_built_by": "run_pipeline",
        "_built_at": datetime.now(timezone.utc).isoformat(),
        "companyName": narrative["companyName"],
        "ticker": ticker,
        "currentPeriod": period,
        "currentPeriodDisplay": period,
        "priorPeriodDisplay": prior_period,
        "earningsDate": earnings_date,
        "nextEarningsDate": next_earnings_date,
        "reportDate": report_date or earnings_date,
        "lastClosePrice":   computed["marketPrices"].get("lastClosePrice", "N/A"),
        "basis":            computed.get("basis") or "Adjusted (Non-GAAP)",
        "valuationLine":    computed.get("valuationLine", ""),
        "financialMetrics": computed["financialMetrics"],
        "operatingMetrics": computed["operatingMetrics"],
        "flashVerdict":          narrative.get("flashVerdict", ""),
        "investmentRead":        narrative.get("investmentRead", ""),
        "whatMatters":           narrative.get("whatMatters", []),
        "variantView":           narrative.get("variantView", {}),
        "guidance":              narrative.get("guidance", []),
        "forwardView":           narrative.get("forwardView", []),
        "nextWatchItems":        narrative.get("nextWatchItems", []),
        "dataPath":  inp.get("dataPath", "A"),
        "toolsUsed": inp.get("toolsUsed", []),
    }

    data_json_path = os.path.join(out_dir, "data.json")
    with open(data_json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    import shutil as _shr
    _want = (os.environ.get("EF_RENDERER") or "").strip().lower()
    def _node_can_render():
        if not _shr.which("node") or not os.path.isfile(os.path.join(HERE, "_render_internal.js")):
            return False
        if os.path.isfile(os.path.join(HERE, "vendor", "docx.cjs")):
            return True
        try:
            return subprocess.run(["node", "-e", "require('docx')"], cwd=HERE, capture_output=True,
                                  timeout=30).returncode == 0
        except Exception:
            return False
    _use_py = _want == "py" or (_want != "js" and not _node_can_render())
    generate_docx = os.path.join(HERE, "_render_internal.py" if _use_py else "_render_internal.js")
    if not os.path.isfile(generate_docx):
        return _err(f"renderer not found at {generate_docx}")
    _render_cmd = [sys.executable, generate_docx] if _use_py else ["node", generate_docx]

    os.makedirs(os.path.dirname(output_docx) or ".", exist_ok=True)
    base_env = {**os.environ, "EF_PIPELINE": "1"}

    def _render(density=None):
        env = dict(base_env)
        if density:
            env["EF_DENSITY"] = density
        try:
            return subprocess.run(
                _render_cmd + [
                 "--input", data_json_path,
                 "--output", output_docx,
                 "--computed", computed_path],
                capture_output=True, text=True, env=env,
                timeout=int(os.environ.get("EF_RENDER_TIMEOUT", "300"))
            )
        except subprocess.TimeoutExpired as _te:
            return subprocess.CompletedProcess(
                _te.cmd, 124, "",
                "TIMEOUT: _render_internal.js exceeded %ss. Set EF_RENDER_TIMEOUT higher if the "
                "note is genuinely enormous; otherwise the renderer is hung." % _te.timeout)

    import shutil as _sh
    have_soffice = bool(_sh.which("soffice") or _sh.which("libreoffice"))

    fit_density = None
    pages = None
    pdf_path, pdf_note = None, "PDF conversion skipped"
    if have_soffice:
        for density in ("comfortable", "tight", "ultra"):
            proc = _render(density)
            if proc.returncode != 0:
                return _err(
                    f"{os.path.basename(generate_docx)} failed (exit {proc.returncode}).\n"
                    f"stdout: {proc.stdout[:1000]}\n"
                    f"stderr: {proc.stderr[:1000]}"
                )
            pdf_path, pdf_note = _convert_to_pdf(output_docx)
            pages = _pdf_page_count(pdf_path)
            fit_density = density
            if _fits_one_page(pages):
                break
    else:
        proc = _render(None)
        if proc.returncode != 0:
            return _err(
                f"{os.path.basename(generate_docx)} failed (exit {proc.returncode}).\n"
                f"stdout: {proc.stdout[:1000]}\n"
                f"stderr: {proc.stderr[:1000]}"
            )
        pdf_path, pdf_note = _convert_to_pdf(output_docx)
        pages = _pdf_page_count(pdf_path)

    _TRIM_ORDER = ("nextWatchItems", "whatMatters", "guidance", "forwardView")
    _TRIM_FLOOR = {"nextWatchItems": 1, "whatMatters": 3, "guidance": 2, "forwardView": 4}
    _MAX_TRIMS = 8
    trimmed = []
    if have_soffice and pages and pages > 1:
        for _ in range(_MAX_TRIMS):
            _field = next((f for f in _TRIM_ORDER
                           if isinstance(data.get(f), list)
                           and len(data[f]) > _TRIM_FLOOR[f]), None)
            if _field is None:
                break
            _cut = data[_field].pop()
            trimmed.append(f"{_field}[{len(data[_field])}]: "
                           + (str(_cut)[:70] + "…" if len(str(_cut)) > 70 else str(_cut)))
            with open(data_json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            proc = _render("ultra")
            if proc.returncode != 0:
                return _err(
                    f"{os.path.basename(generate_docx)} failed during auto-trim (exit {proc.returncode}).\n"
                    f"stderr: {proc.stderr[:1000]}"
                )
            pdf_path, pdf_note = _convert_to_pdf(output_docx)
            pages = _pdf_page_count(pdf_path)
            fit_density = "ultra + trim"
            if _fits_one_page(pages):
                break

    try:
        import render_gate as _rg
        _gate = _rg.check(output_docx, pdf_path, computed_path)
    except Exception as _e:
        return _err(f"render_gate_unavailable — the post-render gate could not run "
                    f"({_e.__class__.__name__}: {_e}). Refusing to report success on a "
                    f"document nothing has checked.")
    render_warnings = []
    _seen_lt = {m.group(1) for w in polarity_warnings
                if (m := _re.search(r"\(([A-Z_]+[0-9]*)\)", w)) and "POLARITY UNRESOLVED" in w}
    for _n in (_gate.get("notes") or []):
        _m = _re.search(r"POLARITY UNRESOLVED\s+—\s+(\S+)", _n)
        if _m and _m.group(1) in _seen_lt:
            continue
        render_warnings.append(_n)
    if _gate["defects"]:
        _detail = "\n  ".join(_gate["defects"])
        if not allow_render_defects:
            _rej = os.path.join(out_dir, "rejected")
            _moved = []
            try:
                os.makedirs(_rej, exist_ok=True)
                for _f in (output_docx, pdf_path):
                    if _f and os.path.isfile(_f):
                        _dst = os.path.join(_rej, os.path.basename(_f))
                        _sh.move(_f, _dst)
                        _moved.append(_dst)
            except Exception:
                pass
            _trim_note = ""
            if trimmed and (_gate.get("pages") or 0) > 1:
                _long = sorted(
                    [(len(str(v)), f"variantView.{k}") for k, v in (data.get("variantView") or {}).items()]
                    + [(len(str(b)), f"whatMatters[{i}]") for i, b in enumerate(data.get("whatMatters") or [])]
                    + [(len(str(b)), f"guidance[{i}]") for i, b in enumerate(data.get("guidance") or [])]
                    + [(len(str(b)), f"forwardView[{i}]") for i, b in enumerate(data.get("forwardView") or [])]
                    + [(len(str(data.get("investmentRead") or "")), "investmentRead")],
                    reverse=True)[:4]
                _trim_note = (
                    f"\n\nAUTO-TRIM IS EXHAUSTED. {len(trimmed)} narrative item(s) were already "
                    f"cut and the note is still over one page, so it is now at the floor "
                    f"(whatMatters 3, nextWatchItems 1, guidance 2, forwardView 4) and nothing further can be "
                    f"removed without gutting it. The remaining length is in your prose, not in "
                    f"the item count. Longest fields: "
                    + ", ".join(f"{n} ({c} chars)" for c, n in _long)
                    + ". Note that variantView and investmentRead are NEVER trimmed — four "
                      "structural fields and a one-line callout — so an overlong variantView "
                      "cannot be fixed automatically. Shorten those and re-run.")
            return _err(
                "render_gate_failed — the RENDERED document does not hold up:\n  "
                + _detail
                + "\n\nThis is the page a client would read, not an intermediate file. "
                + ("Moved to: " + ", ".join(_moved) + "\n" if _moved else "")
                + "Fix the underlying row and re-run. --allow-render-defects downgrades this "
                  "to a warning if you must publish anyway."
                + _trim_note)
        render_warnings.append(
            "RENDER GATE (--allow-render-defects) — the rendered page carries "
            f"{len(_gate['defects'])} defect(s), downgraded to a warning on request: "
            + "; ".join(_gate["defects"]))
    if trimmed:
        render_warnings.append(
            f"AUTO-TRIM — the flash is a one-page format and this note did not fit at the "
            f"tightest density, so {len(trimmed)} narrative item(s) were cut, lowest-ranked "
            f"first: " + "; ".join(trimmed)
            + ". Nothing numeric was touched — the scorecard, guidance figures and market "
              "reaction are unchanged. Shorten your bullets on the next run to keep them.")
    if _gate.get("na_cells"):
        render_warnings.append(
            f"[rendered N/A] {_gate['na_cells']} N/A cell(s) on the rendered page. N/A means "
            f"MISSING DATA, always — each one is a real gap and must be explained or fixed "
            f"before the file goes out.")

    warnings = list(computed.get("warnings", []))
    warnings.extend(render_warnings)
    warnings.extend(flash_warnings)
    warnings.extend(polarity_warnings)
    warnings.extend(gate_warnings)
    warnings.extend(_fact_warn)
    warnings.extend(style_warnings)
    if pdf_path is None:
        warnings.append(pdf_note)
    if pages and pages > 1:
        warnings.append(
            f"[flash] output is still {pages} pages after the density ladder AND the "
            f"auto-trim floor (whatMatters 3, nextWatchItems 1, guidance 2, forwardView 4). The narrative "
            f"itself is too long — shorten the bullets rather than adding a page."
        )
    if pages is None:
        warnings.append(
            "[page count UNVERIFIED] The PDF page count could not be measured, so the "
            "one-page guarantee on this run is UNCHECKED — stage 9b's page test had nothing "
            "to read, and the density ladder ran to its last rung without ever confirming a "
            "fit. This is usually PDF conversion failing: LibreOffice holds a per-profile "
            "lock, so two flashes rendering at the same time will do it. Re-run this ticker "
            "on its own, or open the .docx and confirm one page BY EYE before it goes out. "
            "Do not read 'pages: null' as one page.")
    summary = {
        "status": "success",
        "skill_version": "v11.2.3",
        "renderer": "python" if _use_py else "node",
        "output_docx": os.path.abspath(output_docx),
        "output_pdf": pdf_path,
        "pdf_note": pdf_note,
        "pages": pages,
        "fit_density": fit_density,
        "trimmed_for_one_page": trimmed,
        "ticker": ticker,
        "period": period,
        "kpi_source": computed.get("kpi_source"),
        "operating_kpis": [m["name"] for m in computed["operatingMetrics"]],
        "operating_kpi_ltcodes": [m["ltcode"] for m in computed["operatingMetrics"]],
        "operating_kpi_tiers": [m.get("tier") or "unknown" for m in computed["operatingMetrics"]],
        "warnings": warnings,
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    _FLAGS = ("--strict-polarity", "--no-strict-polarity", "--require-consensus",
              "--strict-facts", "--allow-stale-period", "--allow-render-defects",
              "--allow-unit-sanity", "--keep-scratch", "--allow-copy-check")
    _argv = [a for a in sys.argv[1:] if a not in _FLAGS]
    _strict = "--no-strict-polarity" not in sys.argv[1:]
    _strictcons = True
    _strictfacts = "--strict-facts" in sys.argv[1:]
    _allowstale = "--allow-stale-period" in sys.argv[1:]
    _allowrender = "--allow-render-defects" in sys.argv[1:]
    _allowunit = "--allow-unit-sanity" in sys.argv[1:]
    if len(_argv) < 3:
        print("Usage: python run_pipeline.py <input.json> <narrative.json> <output.docx> "
              "[--no-strict-polarity] [--strict-facts] "
              "[--allow-stale-period] [--allow-render-defects] [--allow-unit-sanity] [--allow-copy-check] "
              "[--keep-scratch]",
              file=sys.stderr)
        sys.exit(2)
    _keepscratch = "--keep-scratch" in sys.argv[1:]
    _rc = 1
    try:
        _rc = main(_argv[0], _argv[1], _argv[2], strict_facts=_strictfacts,
                   strict_polarity=_strict, require_consensus=_strictcons,
                   allow_stale_period=_allowstale, allow_render_defects=_allowrender,
                   allow_unit_sanity=_allowunit,
                   allow_copy_check="--allow-copy-check" in sys.argv[1:])
    finally:
        _cleanup_scratch(_keepscratch, _rc != 0, _argv[2] if len(_argv) > 2 else None)
    sys.exit(_rc)
