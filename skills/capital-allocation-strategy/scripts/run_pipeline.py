#!/usr/bin/env python3
"""
run_pipeline.py - the ONLY valid entry point for the capital-allocation report.

Pipeline:
  1. load raw_data.json (MCP rows) + narrative.json (LLM prose)
  2. compute_capalloc.compute()  -> all numbers + ratings (deterministic)
  3. match the sector profile, inject scorecard weights, compute the weighted Overall,
     the leverage-metric label and the headline Capital Allocation Rating
  4. eval the narrative (required prose fields + banned marketing adjectives)
  5. merge computed + narrative + sector -> render_capalloc.render() -> .docx

Any LLM-supplied number is ignored: compute is re-run here from the raw MCP rows.

Usage:
  python run_pipeline.py raw_data.json narrative.json out.docx
"""
import json
import os
import subprocess
import shutil
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import compute_capalloc as CC
import render_capalloc as RR
from fetch_plan import match_sector

_hr = CC._xr

SKILL_VERSION = "v2.73.0"

BANNED = {"strong", "disappointing", "impressive", "significantly", "robust", "notable",
          "unmatched", "beloved", "award-winning", "premium", "compelling", "best-in-class",
          "world-class", "stellar", "phenomenal", "exceptional", "remarkable"}

REQUIRED_PROSE = ["overall_conclusion"]
REQUIRED_MNA = ["major_transactions", "value_creation_assessment"]
PLACEHOLDER_PHRASES = ("replace with", "representative paraphrase", "representative framing",
                       "representative capital", "verify in", "placeholder", "todo", "[insert",
                       "tbd", "to be confirmed", "xxx", "lorem")
CONVERSATIONAL = ("capex is the story", "the payoff is binary", "that is the call",
                  "cash is going to iron", "the real question is", "the whole story",
                  "lives or dies", "make no mistake", "the bottom line is", "at the end of the day",
                  "not holders", "the story is", "the debate is", "binary on",
                  "cosmetic", "waves the capital plan", "near the tape",
                  "let the numbers", "the takeaway is", "the call is")


def _extract_docx_text(path):
    import docx as _dx
    d = _dx.Document(path)
    return "\n".join([p.text for p in d.paragraphs] +
                      [c.text for t in d.tables for r in t.rows for c in r.cells])


_FIN_SPLIT = {"Asset Management", "Institutional Financial Services", "Specialty Finance"}


def load_sector(meta, codes=None):
    with open(os.path.join(ROOT, "assets", "capital_allocation_sectors.json")) as f:
        reg = json.load(f)
    key = meta.get("sector_key")
    if key and key in reg:
        return key, reg[key]
    row = {"industry": meta.get("industry", ""),
           "classification": (meta.get("classification")
                              or meta.get("insync_subindustry") or "")}
    if codes is not None and row["classification"].strip() in _FIN_SPLIT:
        import sector_router as _SRs
        _want = ("Banks" if any(c in codes for c in _SRs.TEMPLATES["BANKS"]["requires_any"])
                 else "_default")
        return _want, reg[_want]
    return match_sector(row, reg)



def _as_dict(v):
    return v if isinstance(v, dict) else {}


def _as_list(v):
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        return [v] if v.strip() else []
    if isinstance(v, dict):
        return [v]
    return [] if v is None else [v]


def _renorm_sentence(n_scored, requested, valid, unscored, adjustments, labels):
    missing = round((requested or 0) - (valid or 0), 2)
    adj_lost = round(sum((a.get("requested_weight") or 0) - (a.get("applied_weight") or 0)
                         for a in adjustments), 2)
    unscored_lost = round(missing - adj_lost, 2)
    if missing <= 0 and not unscored and not adjustments:
        return "No dimensions were removed; no renormalisation was applied."
    parts = []
    if unscored and unscored_lost:
        parts.append("%s with the unscored dimension(s) (%s)"
                     % (unscored_lost, ", ".join(labels.get(k, k) for k in unscored)))
    if adj_lost:
        parts.append("%s by reducing the weight of %s"
                     % (adj_lost, ", ".join(labels.get(a["dimension"], a["dimension"])
                                            for a in adjustments)))
    if not parts:
        return ("The overall score is the weighted average of the %d scored dimensions only, "
                "renormalised to 100%%." % n_scored)
    return ("The overall score is the weighted average of the %d scored dimensions only. "
            "%s of requested weight was removed - %s - and the remainder was renormalised "
            "to 100%%." % (n_scored, missing, "; ".join(parts)))


def weighted_overall(components, weights):
    num = den = 0.0
    for k, w in weights.items():
        if not w:
            continue
        sc = components.get(k)
        if sc is None:
            continue
        num += sc * w
        den += w
    return float(_hr(num / den, 1)) if den else None


BANDS = [(8.0, "Band 8.0-10.0", "8.0-10.0"), (6.5, "Band 6.5-7.9", "6.5-7.9"),
         (5.0, "Band 5.0-6.4", "5.0-6.4"), (0.0, "Band below 5.0", "<5.0")]

SCORE_DISCLAIMER = ("This score summarises historical disclosed inputs under the stated "
                    "methodology. It is not a security rating, a management-quality rating, an "
                    "expected-return forecast, or an investment recommendation.")


def rating_from(overall):
    if overall is None:
        return "Not published", "n/a"
    for lo, name, rng in BANDS:
        if overall >= lo:
            return name, rng
    return "Band below 5.0", "<5.0"


def score_reconciliation(components, weights, overall, band):
    scored = [(k, components.get(k)) for k, w in weights.items()
              if w and components.get(k) is not None]
    if not scored:
        return f"Overall {overall}/10."
    hi = sorted(scored, key=lambda kv: kv[1], reverse=True)[:2]
    lo = sorted(scored, key=lambda kv: kv[1])[:2]
    lbl = {"buybacks": "Buybacks", "dividends": "Dividends", "m_and_a": "M&A", "capex": "Capex",
           "balance_sheet": "Balance Sheet", "capital_discipline": "Capital Discipline",
           "value_creation": "Value Creation"}
    up = ", ".join(f"{lbl.get(k,k)} {v}" for k, v in hi)
    dn = ", ".join(f"{lbl.get(k,k)} {v}" for k, v in lo)
    return (f"Overall {overall}/10 = {band[0]} (band {band[1]}). "
            f"Lifted by {up}; held back by {dn}. Weighted by sector priority levers.")


def _flatten_strings(obj):
    out = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(_flatten_strings(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_flatten_strings(v))
    return out


def _obs_text(o):
    return o.get("text", "") if isinstance(o, dict) else str(o)


def eval_narrative(narrative):
    errs, warns = [], []
    for k in REQUIRED_PROSE:
        if not str(narrative.get(k, "")).strip():
            errs.append(f"missing required prose field: {k}")
    obs = _as_list(narrative.get("key_observations"))
    if not isinstance(obs, list) or len(obs) < 3:
        errs.append("key_observations must be a list of at least 3 items (Observation -> supporting number -> context)")
    else:
        for i, o in enumerate(obs[:3]):
            if not any(ch.isdigit() for ch in _obs_text(o)):
                errs.append(f"key_observations[{i}] contains no quantitative evidence - every observation must answer 'by how much?'")
    mna = _as_dict(narrative.get("m_and_a"))
    if not isinstance(mna, dict):
        errs.append("m_and_a must be an object with major_transactions + value_creation_assessment")
    else:
        if not (isinstance(mna.get("major_transactions"), list) and mna["major_transactions"]):
            errs.append("m_and_a.major_transactions must be a non-empty list (named deals; use ['None material in period'] if organic)")
        if not str(mna.get("value_creation_assessment", "")).strip() and not str(narrative.get("acq_commentary", "")).strip():
            errs.append("supply m_and_a.value_creation_assessment or acq_commentary (neutral, evidence-first)")
    mon = _as_list(narrative.get("monitor_items")) or _as_list(narrative.get("key_questions"))
    if not isinstance(mon, list) or len(mon) < 3:
        errs.append("monitor_items must be a list of at least 3 measurable variables to watch")
    if str(narrative.get("overall_conclusion", "")).strip() and not any(
            ch.isdigit() for ch in str(narrative.get("overall_conclusion", ""))):
        errs.append("overall_conclusion contains no quantitative evidence - every conclusion must be "
                    "supported by a figure (Observation -> supporting number -> context)")
    _pc = _as_list(narrative.get("peer_commentary"))
    _pc_items = [_pc] if isinstance(_pc, str) else [x for x in _pc if isinstance(x, str)]
    for i, pc in enumerate(_pc_items):
        if pc.strip() and not any(ch.isdigit() for ch in pc):
            errs.append(f"peer_commentary[{i}] contains no quantitative evidence - cite the measured "
                        f"difference rather than characterising it")
    _hv = str(narrative.get("headline_verdict", "") or "").strip()
    if _hv and len(_hv) > 120:
        errs.append(f"headline_verdict must be <=120 characters (got {len(_hv)}) - it is a one-line verdict, not a summary")
    if _hv and _hv.rstrip().endswith((".", "!")) is False and len(_hv) > 0:
        warns.append("headline_verdict reads better ending in a full stop")
    for txt in _flatten_strings(narrative):
        low = txt.lower()
        for ph in PLACEHOLDER_PHRASES:
            if ph in low:
                errs.append(f"placeholder language not allowed: '{ph}' found in a narrative field "
                            f"(\"{txt[:60]}...\") - supply the real figure/quote or omit the field")
                break
    blob = " ".join(x.lower() for x in _flatten_strings(narrative))
    for w in BANNED:
        if f" {w}" in f" {blob}" or blob.startswith(w):
            warns.append(f"banned marketing adjective present: '{w}' (let numbers carry sentiment)")
    return errs, warns


def allocation_confidence(computed, raw, narrative):
    reasons, score = [], 0
    span = computed["meta"].get("years_covered", 0) or 0
    monitor = computed.get("monitor", {}).get("available")
    peers = computed.get("peers", {})
    npeers = len(peers.get("rows", [])) if peers.get("available") else 0
    excl = len(peers.get("peer_warnings", []))
    fwd = bool(_as_dict(narrative.get("forward_outlook")).get("rows"))
    qc_ok = computed["qc"]["passed"] == computed["qc"]["total"]
    if span >= 5: score += 1
    else: reasons.append("<5y history")
    if monitor: score += 1
    else: reasons.append("no quarterly monitor")
    if npeers >= 2: score += 1
    else: reasons.append("thin/absent peer set")
    if fwd: score += 1
    else: reasons.append("no forward guidance/consensus")
    if qc_ok: score += 1
    else: reasons.append("QC flags")
    if excl: reasons.append(f"{excl} peer metric(s) excluded as outliers")
    if score >= 5 and excl == 0:
        conf = "High"
    elif score >= 3:
        conf = "Medium"
    else:
        conf = "Low"
    basis = "full data set, clean QC and peer validation" if (conf == "High" and not reasons) else "; ".join(reasons)
    return conf, basis


WORD_LIMIT = 80
CAPALLOC_KW = ("buyback", "repurchase", "dividend", "capex", "capital", "allocat", "leverage",
               "balance sheet", "return of capital", "m&a", "acquisition", "reinvest", "invest", "payout", "authoriz", "shareholder")
DENSITY_EXEMPT = ("key_observations", "overall_conclusion", "scope", "consistency_check",
                  "classification_evidence")
AI_TELLS = ("un-priced swing", "it is worth noting", "it's worth noting", "notably,", "importantly,",
            "underscore", "testament", "in an era", "navigate the", "landscape", "quietly",
            "when it comes to", "stands as", "a nuanced")


def density_warnings(narrative):
    warns = []

    def wc(txt):
        return len(str(txt).split())

    def walk(obj, path):
        top = path.split(".")[0].split("[")[0]
        if isinstance(obj, str):
            if top not in DENSITY_EXEMPT and wc(obj) > WORD_LIMIT:
                warns.append(f"density: '{path}' is {wc(obj)} words (>{WORD_LIMIT}) - tighten")
        elif isinstance(obj, list):
            for i, x in enumerate(obj):
                walk(x, f"{path}[{i}]")
        elif isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, f"{path}.{k}" if path else k)
    walk(narrative, "")
    blob = " ".join(_flatten_strings(narrative)).lower()
    for t in AI_TELLS:
        if t in blob:
            warns.append(f"style: '{t.strip()}' reads AI-generated - use plain neutral voice")
    for t in CONVERSATIONAL:
        if t in blob:
            warns.append(f"conversational/opinion language: '{t}' - describe the decision with a number, do not judge it")
    for i, q in enumerate(_as_list(narrative.get("management_commentary"))):
        txt = str(q.get("quote", "")).lower()
        if txt and not any(k in txt for k in CAPALLOC_KW):
            warns.append(f"commentary[{i}] is not capital-allocation-specific (reads demand/product) - replace with buyback/dividend/capex/M&A/leverage intent")
    return warns


_LEV_RX = re.compile(r"([-+]?\d{1,3}\.\d{1,2})\s*x\s*(?:ebitda|net debt)|"
                     r"(?:leverage|net debt\s*/\s*ebitda)\D{0,25}?([-+]?\d{1,3}\.\d{1,2})\s*x",
                     re.I)
_COV_RX = re.compile(
    r"([-+]?\d{1,3}(?:\.\d{1,2})?)\s*x\s+interest\s+cover(?:age)?"
    r"|interest\s+cover(?:age)?\s*(?:ratio)?\s*(?:of|was|is|at|stood at|:)?\s*"
    r"([-+]?\d{1,3}(?:\.\d{1,2})?)\s*x",
    re.I)


_FUND_CLAIM_RX = re.compile(
    r"\b(?:part[- ]?funded|funded|financed|paid for|bankrolled)\s+(?:from|by|with|through|out of)\s+"
    r"(?:the\s+)?(?:balance sheet|debt|borrowing|borrowings|leverage|cash reserves|the revolver|"
    r"new debt|additional debt|incremental debt)", re.I)
_FUND_DENIAL_RX = re.compile(
    r"cannot be (?:traced|attributed)|not reported|is required to attribute|no way to trace|"
    r"coincident|not attributed", re.I)


def narrative_funding_attribution(narrative, computed):
    errs = []
    _SKIP = {"prepared_date", "ticker", "company", "company_name", "sector", "analyst"}
    texts = []
    for _k, _v in sorted(narrative.items()):
        if _k in _SKIP:
            continue
        for _t in _flatten_strings(_v):
            texts.append((_k, _t))
    for fld, txt in texts:
        for sent in re.split(r"(?<=[.;])\s+", str(txt or "")):
            if _FUND_CLAIM_RX.search(sent) and not _FUND_DENIAL_RX.search(sent):
                errs.append(
                    "narrative '%s' states a funding source (\"%s\"). Gross debt raised and repaid are "
                    "not reported, so the funding of individual uses cannot be traced; the report says "
                    "so elsewhere. State the coincidence (net debt moved over the same window) or "
                    "remove the attribution."
                    % (fld, sent.strip()[:90]))
    return errs


def narrative_acquisition_conflicts(narrative, computed):
    errs = []
    ma = (computed.get("m_and_a") or {})
    total = str(ma.get("acq_total_5y") or "n/a")
    has_spend = not total.lower().startswith("n/a") and any(ch.isdigit() for ch in total)
    if not has_spend:
        return errs
    _deny = ("no transaction consideration", "not captured", "no consideration is captured",
             "no acquisition spend", "no material acquisitions occurred",
             "acquisition consideration is not available")
    blobs = []
    mn = _as_dict(narrative.get("m_and_a"))
    for k in ("value_creation_assessment", "strategic_rationale"):
        if mn.get(k):
            blobs.append(str(mn[k]))
    if narrative.get("acq_commentary"):
        blobs.append(str(narrative["acq_commentary"]))
    for d in (mn.get("major_transactions") or []):
        blobs.append(d.get("name", "") if isinstance(d, dict) else str(d))
    for b in blobs:
        low = b.lower()
        for phrase in _deny:
            if phrase in low:
                errs.append("narrative states %r while the engine computed acquisition spend of %s"
                            % (phrase, total))
                break
    return errs


def narrative_figure_conflicts(narrative, computed):
    reg = (computed.get("registry") or {}).get("metrics") or {}

    def _stored(mid, unit):
        for k, m in reg.items():
            if k.split("@", 1)[0] == mid and m.get("available") and m.get("value") is not None:
                return m["value"]
        return None

    checks = [("NET_DEBT_TO_EBITDA", _LEV_RX, "net debt / EBITDA", 0.02),
              ("INTEREST_COVERAGE", _COV_RX, "interest coverage", 0.05)]
    errs = []
    blob = " ".join(_flatten_strings(narrative))
    for mid, rx, label, tol in checks:
        exp = _stored(mid, "x")
        if exp is None:
            continue
        for m in rx.finditer(blob):
            gi = next((i for i in range(1, (m.re.groups or 0) + 1) if m.group(i)), None)
            if gi is None:
                continue
            try:
                got = float(m.group(gi))
            except ValueError:
                continue
            _ns = m.start(gi)
            lead = blob[max(0, _ns - 14):_ns].lower()

            if re.search(r"(against|versus|\bvs\.?)\s*$", lead):
                continue
            if re.match(r"\s*(x\s*)?(peer|group|sector)\s+(median|average)",
                        blob[m.end(gi):m.end(gi) + 24], re.I):
                continue

            if re.search(r"(~|about|roughly|around|near|circa|approximately)\s*$", lead):
                if _hr(got, 1) == _hr(exp, 1):
                    continue

            _stated_dp = len(m.group(gi).split(".")[1]) if "." in m.group(gi) else 0
            _within_tol = abs(got - exp) <= tol
            _exact_at_stated = _hr(got, _stated_dp) == _hr(exp, _stated_dp)
            if not (_within_tol or _exact_at_stated):
                errs.append(
                    "narrative states %s as %.2fx but the computed value is %.2fx. "
                    "Narrative prose must quote the engine's figures - correct the sentence "
                    "(context: \"%s\")."
                    % (label, got, exp, m.group(0).strip()))
    return errs


_ALLOC_RX = re.compile(
    r"(reinvestment|shareholder returns?|debt reduction)\D{0,40}?(\d{1,3}\.\d)%\s*of\s*"
    r"(?:the\s*)?(?:total\s*)?capital (?:deployed|allocation)", re.I)
_ALLOC_RX3 = re.compile(
    r"(?:against|versus|vs\.?|compared with|and)\s+(\d{1,3}\.\d)%\s+to\s+"
    r"(shareholders?|holders|reinvestment|debt reduction)", re.I)
_ALLOC_RX2 = re.compile(
    r"(reinvestment|shareholder returns?|debt reduction)\s+(?:absorbed|took|was|were|ran at)\s+"
    r"(\d{1,3}\.\d)%\s*of\s*(?:the\s*)?(?:total\s*)?capital", re.I)


def _dim_reason(key, computed):
    ma = computed.get("m_and_a") or {}
    ev = str(((computed.get("scorecard") or {}).get("evidence") or {}).get(key, ""))
    _na = (str(ma.get("assessment") or "").lower() == "not applicable" if key == "m_and_a"
           else ev.lower().startswith("not applicable"))
    if not _na:
        return "unscored - required input unavailable"
    if key == "m_and_a":
        tot, pct = str(ma.get("acq_total_5y") or "n/a"), str(ma.get("acq_pct_of_ocf") or "n/a")
        if not tot.lower().startswith("n/a") and any(c.isdigit() for c in tot):
            return ("not separately scored - acquisition spending of %s represented %s of "
                    "cumulative operating cash flow, below the methodology's 15%% materiality "
                    "threshold" % (tot, pct))
    return "not applicable - no material transaction in the period"


def _clip(txt, n):
    t = str(txt or "").strip()
    if len(t) <= n:
        return t
    cut = t[:n]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > n * 0.6 else cut).rstrip(" ,;:") + "..."


_DIM_LABEL = {"buybacks": "Buybacks", "dividends": "Dividends", "capex": "Capex",
              "m_and_a": "M&A", "balance_sheet": "Balance sheet",
              "capital_discipline": "Capital discipline", "value_creation": "Value creation"}

_CTRL_IMPACT = {
    "Balance sheet reconciles (debt - cash - investments = net debt)":
        "Balance sheet and Capital Discipline scores",
    "Allocation mix sums to 100%": "Allocation style and all weighted scores",
    "Shareholder-return calculations validated": "Buybacks, Dividends and Capital Discipline",
    "No conflicting metrics found": "All sections quoting the conflicting metric",
    "Internal arithmetic checks passed": "Every weighted score",
    "Peer comparison calculations validated": "Peer comparison section only",
    "Missing data explained": "Disclosure only",
}


def narrative_allocation_conflicts(narrative, computed):
    cl = computed.get("classification") or {}
    canon = {}
    for _key, _fld in (("reinvestment", "reinvestment_pct"),
                       ("shareholder returns", "shareholder_return_pct"),
                       ("shareholder return", "shareholder_return_pct"),
                       ("debt reduction", "debt_reduction_pct")):
        _v = str(cl.get(_fld, "")).strip().rstrip("%")
        try:
            canon[_key] = float(_v)
        except ValueError:
            pass
    if not canon:
        return []
    errs, seen = [], set()
    blob = " ".join(_flatten_strings(narrative))
    for m in _ALLOC_RX3.finditer(blob):
        _b = m.group(2).lower()
        bucket = ("shareholder returns" if _b.startswith(("shareholder", "holder"))
                  else "reinvestment" if _b.startswith("reinvest") else "debt reduction")
        exp = canon.get(bucket)
        if exp is None:
            continue
        got = float(m.group(1))
        if abs(got - exp) > 0.15 and (bucket, got) not in seen:
            seen.add((bucket, got))
            errs.append("narrative states %s at %.1f%% of capital deployed but the canonical "
                        "allocation bridge shows %.1f%%. Prose must restate the bridge, not "
                        "recompute it (context: \"%s\")." % (bucket, got, exp, m.group(0).strip()))
    for rx in (_ALLOC_RX, _ALLOC_RX2):
        for m in rx.finditer(blob):
            bucket = m.group(1).lower().rstrip("s") + ("s" if "return" in m.group(1).lower() else "")
            bucket = ("shareholder returns" if "return" in bucket else bucket)
            exp = canon.get(bucket)
            if exp is None:
                continue
            got = float(m.group(2))
            if abs(got - exp) > 0.15 and (bucket, got) not in seen:
                seen.add((bucket, got))
                errs.append("narrative states %s at %.1f%% of capital deployed but the canonical "
                            "allocation bridge shows %.1f%%. Prose must restate the bridge, not "
                            "recompute it (context: \"%s\")."
                            % (bucket, got, exp, m.group(0).strip()))
    return errs


_FCFPAY_RX = re.compile(r"(\d{1,3}\.\d)%\s*of\s*(?:its\s*)?(?:cumulative\s*)?free cash flow", re.I)
_PERIOD_WORDS = ("cumulative", "latest year", "latest fiscal", "fy20", "ltm", "over the period",
                 "five-year", "5-year")


def narrative_payout_conflicts(narrative, computed):
    reg = (computed.get("registry") or {}).get("metrics") or {}
    exp = {}
    for k, m in reg.items():
        if k.startswith("CAPITAL_RETURNED_PCT_FCF@") and m.get("available") and m.get("value") is not None:
            exp[k.split("@", 1)[1].split(":")[0]] = float(_hr(m["value"], 1))
    if not exp:
        return []
    errs = []
    for txt in _flatten_strings(narrative):
        for m in _FCFPAY_RX.finditer(txt):
            got = float(_hr(m.group(1), 1))
            ctx = txt[max(0, m.start() - 90):m.end() + 40].lower()
            has_period = any(w in ctx for w in _PERIOD_WORDS)
            if got not in exp.values():
                errs.append("narrative states %.1f%% of free cash flow, which matches no computed "
                            "period (available: %s). Use the engine's figure."
                            % (got, ", ".join("%s=%.1f%%" % (k, v) for k, v in sorted(exp.items()))))
            elif not has_period:
                _match = [k for k, v in exp.items() if v == got]
                errs.append("narrative states %.1f%% of free cash flow without naming the period "
                            "(this is the %s figure). Every payout ratio must state its period, "
                            "because the latest-year and cumulative measures differ materially."
                            % (got, "/".join(_match)))
    return errs


def _err_exit(status, *errors):
    print(json.dumps({"status": status, "errors": list(errors)}, indent=2))
    sys.exit(5)


def _load_json(path, what):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        _err_exit("input_error", "%s not found at %r. Check the path passed on the command line."
                  % (what, path))
    except IsADirectoryError:
        _err_exit("input_error", "%s path %r is a directory, not a file." % (what, path))
    except UnicodeDecodeError as e:
        _err_exit("input_error", "%s at %r is not valid UTF-8 (%s)." % (what, path, e))
    except json.JSONDecodeError as e:
        _err_exit("input_error",
                  "%s at %r is not valid JSON: %s (line %d, column %d)."
                  % (what, path, e.msg, e.lineno, e.colno),
                  "A truncated or partially pasted file is the usual cause. Re-paste the whole "
                  "MCP response, including the closing brace, and re-run.")


def _assert_paths(summary):
    missing = [k for k in ("output", "pdf", "docx", "manifest")
               if summary.get(k) and not os.path.exists(summary[k])]
    if missing:
        summary.setdefault("warnings", []).append(
            "ADVISORY: the run summary named %s but no file is present there."
            % ", ".join("%s=%s" % (k, summary[k]) for k in missing))
    return summary


def main():
    if len(sys.argv) < 4:
        _err_exit("usage_error",
                  "Usage: python run_pipeline.py raw_data.json narrative.json out.docx")
    raw = _load_json(sys.argv[1], "raw_data.json")
    narrative = _load_json(sys.argv[2], "narrative.json")
    out_path = sys.argv[3]

    peers = raw.get("peers") or []
    if (len(peers) < 2) and not os.environ.get("CAPALLOC_ALLOW_NO_PEERS"):
        print(json.dumps({"status": "peer_error", "errors": [
            "%s This report requires an ACTUAL peer set (at least 2 real comparables "
            % ("No peer data supplied." if not peers else
               "Only %d peer supplied; a median cannot be formed from one company." % len(peers)),
            "fetched from InSync); placeholder or illustrative peers are not permitted. Fetch each "
            "peer's annual actuals + market_cap into raw_data.json 'peers', or set "
            "CAPALLOC_ALLOW_NO_PEERS=1 to explicitly waive (section 12 will then state peers were omitted)."]}))
        sys.exit(4)

    _EXACT_MATCH = ("exact", "exact_ticker", "exact_symbol", "identifier")
    _meta = raw.get("meta") or {}
    _mt = str(_meta.get("match_type") or "").strip().lower()
    if not _mt and (os.environ.get("CAPALLOC_ENFORCE") or "block").lower() == "block":
        print(json.dumps({"status": "identity_error", "errors": [
            "meta.match_type is missing. It is required: this report is built on an exact "
            "identifier match only, and an absent match type records nothing about how the "
            "issuer was resolved, so the one control that can catch a report about the wrong "
            "company cannot run. Set it from the search_tickerid result (one of %s), or run "
            "with CAPALLOC_ENFORCE=warn to proceed with the gap disclosed in warnings[]."
            % ", ".join(_EXACT_MATCH)]}, indent=2))
        sys.exit(6)
    if _mt and _mt not in _EXACT_MATCH:
        print(json.dumps({"status": "identity_error", "errors": [
            "meta.match_type is %r. This report is built on an exact identifier match only: a "
            "similarity-scored resolution returns a different issuer at status success, and the "
            "whole document would be internally consistent and about the wrong company. Re-resolve "
            "with search_tickerid and use the exact match, or correct meta.ticker_id by hand."
            % _meta.get("match_type")]}, indent=2))
        sys.exit(6)

    _pre_skey, _pre_prof = load_sector(
        raw.get("meta", {}),
        {r.get("LTCODE") for r in CC.unwrap(raw.get("actuals_annual")) if r.get("LTCODE")})
    raw.setdefault("meta", {}).setdefault("sector_key", _pre_skey)

    computed = CC.compute(raw)
    if computed.get("status") != "ok":
        print(json.dumps(computed)); sys.exit(2)

    skey, prof = load_sector(computed["meta"])
    weights = dict(prof.get("scorecard_weights", {}) or {})
    _dlabels = (computed.get("value_creation", {}) or {}).get("dimension_labels") or {}

    def _dlab(k):
        return _dlabels.get(k) or _DIM_LABEL.get(k, k)

    _dvx = computed.get("dividends") or {}
    if _dvx.get("not_applicable") and weights.get("dividends") and weights.get("buybacks"):
        computed.setdefault("value_creation", {}).setdefault("weight_merges", []).append(
            {"from": "dividends", "into": "buybacks"})
    _max = computed.get("m_and_a") or {}
    if _max.get("not_applicable") and weights.get("m_and_a") and weights.get("capex"):
        computed.setdefault("value_creation", {}).setdefault("weight_merges", []).append(
            {"from": "m_and_a", "into": "capex"})
    _merged = {}
    for _mg in (computed.get("value_creation", {}).get("weight_merges") or []):
        _f, _i = _mg.get("from"), _mg.get("into")
        if _f in weights and _i in weights:
            _merged[_f] = {"into": _i, "amount": weights.get(_f) or 0}
            weights[_i] = (weights.get(_i) or 0) + (weights.get(_f) or 0)
            weights[_f] = 0

    for _dim, _blk, _st, _rsn in (("capex", "capex", "capex_state", "capex_state_reason"),
                                  ("balance_sheet", "balance_sheet", "bs_state",
                                   "bs_state_reason")):
        _wf = (computed.get(_blk) or {}).get("weight_factor")
        if _wf is not None and _wf != 1.0 and weights.get(_dim):
            _orig_w = weights[_dim]
            weights[_dim] = round(_orig_w * _wf, 3)
            computed.setdefault("weight_adjustments", []).append({
                "dimension": _dim,
                "state": (computed.get(_blk) or {}).get(_st),
                "requested_weight": _orig_w,
                "applied_weight": weights[_dim],
                "rule": "Provisional dimensions carry half weight; incomplete dimensions carry "
                        "none. (approved by the methodology owner, 23 August 2026).",
                "reason": (computed.get(_blk) or {}).get(_rsn, ""),
            })
    for _k, _v in (computed["scorecard"].get("components") or {}).items():
        if _v is None and _k in weights:
            weights[_k] = 0
    computed["scorecard"]["weights"] = weights

    _vcx = computed.get("value_creation") or {}
    _OUT_REQ = [("Return on invested capital or return on equity",
                 not str(_vcx.get("roic", "n/a")).startswith("n/a")
                 or not str(_vcx.get("roe", "n/a")).startswith("n/a")),
                ("Earnings per share growth", not str(_vcx.get("eps_cagr", "n/a")).startswith("n/a")),
                ("Free cash flow per share growth",
                 not str(_vcx.get("fcf_per_share_cagr", "n/a")).startswith("n/a")),
                ("Share count change", not str(_vcx.get("share_count_change", "n/a")).startswith("n/a")),
                ("Capital returned", not str(_vcx.get("capital_returned_period", "n/a")).startswith("n/a"))]
    _out_have = [n for n, ok in _OUT_REQ if ok]
    _out_miss = [n for n, ok in _OUT_REQ if not ok]
    _out_cov = len(_out_have) / float(len(_OUT_REQ))

    _mandatory = []
    _sme = (computed.get("value_creation") or {}).get("sector_module_error")
    if _sme:
        _mandatory.append(
            "the %s sector lens did not run (%s), so the measures this sector is judged on are "
            "absent and the score would be the generic lens under a sector heading"
            % ((computed.get("meta") or {}).get("sector_key") or "active", _sme))
    _cls = computed.get("classification") or {}
    if _cls.get("reconciled") is False:
        _mandatory.append("allocation mix does not reconcile (%s)"
                          % ", ".join(_cls.get("reconciliation_failures") or ["unspecified"]))
    _qc = computed.get("qc") or {}
    if _qc.get("total") and _qc.get("passed") is not None and _qc["passed"] < _qc["total"]:
        _qcf = [c for c in (_qc.get("checks") or []) if not c.get("pass")]
        _mandatory.append(
            "internal arithmetic checks %s/%s%s"
            % (_qc["passed"], _qc["total"],
               " - " + "; ".join("%s (%s)" % (c.get("check", "unnamed check"),
                                              c.get("detail", "no detail"))
                                 for c in _qcf) if _qcf else ""))

    _val = computed.get("validation") or {}
    _failed_ctrls = [c for c in (_val.get("checks") or []) if not c.get("pass")]
    for _c in _failed_ctrls:
        _mandatory.append("integrity control failed - %s (%s)"
                          % (_c.get("check", "unnamed control"),
                             str(_c.get("detail", ""))[:110]))
    computed["failed_controls"] = [{
        "control": _c.get("check"),
        "detail": _c.get("detail"),
        "affected": _CTRL_IMPACT.get(_c.get("check"), "Overall descriptive score"),
        "severity": "Mandatory",
        "score_withheld": True,
    } for _c in _failed_ctrls]
    _ma = computed.get("m_and_a") or {}
    _SENT = ("none material", "no material", "n/a", "none", "no transaction", "no acquisition",
             "no deal", "not captured", "none identified", "none disclosed", "not disclosed",
             "no consideration", "not reported")
    _named_deals = []
    for _d in _as_list(_as_dict(narrative.get("m_and_a")).get("major_transactions")):
        _nm = (_d.get("name", "") if isinstance(_d, dict) else str(_d)).strip()
        _low = _nm.lower()
        _has_value = bool(isinstance(_d, dict) and str(_d.get("value", "")).strip())
        _is_sentinel = (not _has_value) and any(_low.startswith(x) for x in _SENT)
        if _nm and not _is_sentinel:
            _named_deals.append(_nm)
    _acq_missing = str(_ma.get("acq_total_5y", "n/a")).lower().startswith("n/a")
    if _named_deals and _acq_missing:
        _mandatory.append("material acquisition consideration is not captured in the allocation "
                          "bridge (%s), so the allocation mix is incomplete" % _clip(_named_deals[0], 60))
        computed["m_and_a"]["score"] = None
        computed["scorecard"]["components"]["m_and_a"] = None
        computed["m_and_a"]["status_text"] = (
            "M&A score: Withheld pending complete transaction consideration and post-deal "
            "outcome data.")
    elif str(_ma.get("assessment")) == "Unscored":
        _traces, _corr_failed = [], None
        try:
            import data_coverage as _DC2
            _traces = _DC2.corroborate(CC.unwrap(raw.get("actuals_annual")))
        except Exception as _ce:
            _corr_failed = "%s: %s" % (type(_ce).__name__, _ce)
        if _corr_failed:
            _mandatory.append(
                "the acquisition line is not reported and the corroboration control could not "
                "run (%s), so the allocation mix cannot be shown to be complete" % _corr_failed)
        if _traces:
            _mandatory.append("the acquisition line is not reported, yet " + _traces[0]
                              + ", so the allocation mix cannot be shown to be complete")
        else:
            try:
                _basis = _DC2.corroboration_basis(CC.unwrap(raw.get("actuals_annual")))
            except Exception:
                _basis = {"complete": False, "checked": [], "unavailable": ["goodwill",
                                                                            "intangible assets"]}
            if _basis.get("complete"):
                _tail = ("Both independent traces of a business combination - the goodwill and "
                         "intangible-asset series - were checked and neither moved, so the "
                         "absence is corroborated.")
            elif _basis.get("checked"):
                _tail = ("Of the two independent traces, only %s returned a usable series; it "
                         "did not move. %s carried no data, so the absence is only partly "
                         "corroborated."
                         % (" and ".join(_basis["checked"]),
                            " and ".join(_basis["unavailable"]).capitalize()))
            else:
                _tail = ("Neither independent trace (goodwill, intangible assets) returned data, "
                         "so the absence could not be corroborated in either direction - it is "
                         "not established that no acquisition occurred.")
            computed.setdefault("m_and_a", {})["absence_note"] = (
                "The acquisition line is not reported in the source data. %s The mix below "
                "excludes acquisition spending that the feed does not carry." % _tail)
    _skey_pub = (computed.get("meta") or {}).get("sector_key")
    _cl_pub = computed.get("classification") or {}
    if _skey_pub == "Insurance" and str(_cl_pub.get("reinvestment_pct", "")).lower().startswith("n/a"):
        _mandatory.append("the insurer capital-generation bridge is incomplete (statutory capital "
                          "unavailable and reinvestment not measurable), so a total allocation "
                          "style cannot be derived from measured cash deployment alone")
    _bs = computed.get("balance_sheet") or {}
    if _bs.get("net_debt_reconciled") is False:
        _mandatory.append("net debt does not reconcile to total debt less cash and short-term investments")

    if _out_cov >= 0.8 and not _mandatory:
        _out_state, _out_why = "Scored", ""
    elif _out_cov >= 0.8:
        _out_state = "Provisional"
        _out_why = ("outcome inputs are %.0f%% complete, but a mandatory control has failed, so the "
                    "outcome is an observation rather than a published result" % (_out_cov * 100))
    elif _out_cov >= 0.6:
        _out_state = "Provisional"
        _out_why = "one or more outcome inputs are unavailable"
    else:
        _out_state, _out_why = "Unscored", "too few outcome inputs are available"
    computed["outcome_coverage"] = {
        "required": [n for n, _ in _OUT_REQ],
        "available": _out_have,
        "missing": _out_miss,
        "coverage": "%.1f%%" % (_out_cov * 100),
        "state": _out_state,
        "state_reason": _out_why,
        "tsr": ("included" if _vcx.get("tsr_available") else
                "excluded, as it is optional here and no price history was supplied"),
        "note": ("Total shareholder return is optional in this framework and is excluded from "
                 "outcome coverage when a price history is not supplied, rather than counted as a "
                 "missing input." if not _vcx.get("tsr_available") else ""),
    }

    _adj = []
    if _out_state == "Provisional" and weights.get("value_creation"):
        _adj.append(("value_creation", 0.5, "Provisional",
                     "Outcome coverage is %s; %s." % (computed["outcome_coverage"]["coverage"],
                                                      _out_why or "outcome inputs incomplete")))
    _cds = (computed.get("scorecard") or {}).get("capital_discipline_state")
    if _cds == "Provisional" and weights.get("capital_discipline"):
        _adj.append(("capital_discipline", 0.5, "Provisional",
                     (computed.get("scorecard") or {}).get("capital_discipline_state_reason", "")))
    for _dim, _f, _st, _why in _adj:
        _orig = weights[_dim]
        weights[_dim] = round(_orig * _f, 3)
        computed.setdefault("weight_adjustments", []).append({
            "dimension": _dim, "state": _st, "requested_weight": _orig,
            "applied_weight": weights[_dim],
            "rule": "Provisional dimensions carry half weight (approved 23 August 2026).",
            "reason": _why})

    overall = weighted_overall(computed["scorecard"]["components"], weights)
    computed["scorecard"]["overall"] = overall
    _comp = computed["scorecard"].get("components") or {}
    _req = sum(v for v in weights.values() if v) or 0.0
    _valid = sum(w for k, w in weights.items() if w and _comp.get(k) is not None)
    _requested_total = sum(v for v in (prof.get("scorecard_weights") or {}).values() if v) or 0.0
    _cov = (_valid / _requested_total) if _requested_total else 0.0
    _scored = sorted(k for k, w in weights.items() if w and _comp.get(k) is not None)
    _unscored = sorted(k for k in (prof.get("scorecard_weights") or {}) if _comp.get(k) is None)
    _status = ("publish" if _cov >= 0.80 else "provisional" if _cov >= 0.60 else "suppress")
    APPROVED_RULES = [
        "Score-publication thresholds (>=80% publish / 60-79% provisional / <60% suppress): "
        "approved by the methodology owner, 23 August 2026.",
        "Provisional dimensions carry half their sector weight; incomplete dimensions carry none: "
        "approved by the methodology owner, 23 August 2026.",
    ]
    _unapproved = []
    if _status == "suppress":
        _mandatory.append("effective score coverage %.1f%% is below the 60%% minimum" % (_cov * 100))


    computed["score_coverage"] = {
        "weight_merges": [{"from": _dlab(_k), "into": _dlab(_v["into"]),
                           "amount": _v["amount"]} for _k, _v in _merged.items()],
        "requested_weight": round(_requested_total, 2),
        "valid_weight": round(_valid, 2),
        "unscored_weight": round(_requested_total - _valid, 2),
        "retained_coverage": "%.1f%%" % (_cov * 100),
        "scored_dimensions": [_DIM_LABEL.get(k, k) for k in _scored],
        "unscored_dimensions": [_DIM_LABEL.get(k, k) for k in _unscored],
        "publication_status": _status,
        "unapproved_rules": _unapproved,
        "approved_rules": ([APPROVED_RULES[0]]
                           + ([APPROVED_RULES[1]] if computed.get("weight_adjustments") else [])),
        "thresholds": ">=80% publish | 60-79% provisional | <60% suppress "
                      "(approved by the methodology owner, 23 August 2026)",
        "decomposition": ([{"dimension": _DIM_LABEL.get(k, k),
                            "requested": (prof.get("scorecard_weights") or {}).get(k),
                            "applied": 0,
                            "reason": _dim_reason(k, computed)}
                           for k in _unscored
                           if (prof.get("scorecard_weights") or {}).get(k)
                           and k not in _merged]
                          + [{"dimension": _DIM_LABEL.get(a["dimension"], a["dimension"]),
                              "requested": a["requested_weight"],
                              "applied": a["applied_weight"],
                              "reason": "%s - partial weight applied" % str(a.get("state", "")).lower()}
                             for a in (computed.get("weight_adjustments") or [])]),
        "renormalisation": _renorm_sentence(
            len(_scored), _requested_total, _valid, _unscored,
            computed.get("weight_adjustments") or [], _DIM_LABEL),
    }

    _integrity_failed = bool(_failed_ctrls) or bool(
        _qc.get("total") and _qc.get("passed") is not None and _qc["passed"] < _qc["total"])
    _gate_only = bool(_mandatory) and not _integrity_failed
    computed["publication"] = {
        "withheld": bool(_mandatory),
        "reasons": _mandatory,
        "cause": ("data_integrity" if _integrity_failed
                  else "publication_gate" if _gate_only else None),
        "statement": (
            "Descriptive capital-allocation score: Withheld. Reason: the figures did not "
            "reconcile, so no overall score is published." if _integrity_failed else
            "Descriptive capital-allocation score: Withheld. The figures reconcile, but the "
            "evidence base is incomplete on a point that the score depends on." if _gate_only else ""),
        "disclaimer": SCORE_DISCLAIMER,
    }
    if _mandatory:
        overall = None
        computed["scorecard"]["overall"] = None
        computed["classification"]["label"] = "Withheld"
        computed["classification"]["acquisition_pct"] = (
            "Not applicable"
            if str(((computed.get("scorecard") or {}).get("evidence") or {})
                   .get("m_and_a", "")).lower().startswith("not applicable")
            else "Withheld")
        _recon_cause = _integrity_failed or any(
            ("reconcil" in str(_m).lower() or "tie" in str(_m).lower()) for _m in _mandatory)
        computed["classification"]["basis"] = (
            "Allocation style withheld: " + "; ".join(_mandatory) + ". "
            + ("A style label derived from figures that do not reconcile would misstate the mix."
               if _recon_cause else
               "Too little of the sector weighting could be scored for a style label to be "
               "representative of the whole mix."))

    _ev = (computed.get("scorecard") or {}).get("evidence") or {}
    _cp = (computed.get("scorecard") or {}).get("components") or {}
    for _k, _txt in list(_ev.items()):
        _t = str(_txt or "")
        if _cp.get(_k) is None and re.match(r"\s*Scored\s+None\s*/\s*10", _t):
            _tail = _t.split("because", 1)[1].strip() if "because" in _t else ""
            _ev[_k] = ("Unscored - the required input is not available."
                       + ((" Reported figures: " + _tail) if _tail else ""))
        elif re.search(r"Scored\s+None\s*/\s*10", _t):
            _ev[_k] = re.sub(r"Scored\s+None\s*/\s*10", "Unscored", _t)

    rating, band_range = rating_from(overall)
    reconciliation = score_reconciliation(computed["scorecard"]["components"], weights,
                                          overall, (rating, band_range))
    computed["scorecard"]["overall_band"] = band_range
    computed["scorecard"]["reconciliation"] = reconciliation
    _lev_label = None
    _lev_resolved = None
    try:
        _lev_metric = str(prof.get("leverage_metric", "") or "")
        _lev_key = ("NET_DEBT_TO_EBITDA" if "ebitda" in _lev_metric.lower()
                    else "DEBT_TO_TOTAL_CAPITAL" if "total capital" in _lev_metric.lower()
                    else None)
        if _lev_key:
            for _k, _m in ((computed.get("registry") or {}).get("metrics") or {}).items():
                if _k.split("@", 1)[0] == _lev_key and _m.get("label"):
                    _lev_resolved = _m["label"]
                    _lev_label = "Leverage (%s)" % _m["label"]
                    break
    except Exception:
        _lev_label = _lev_resolved = None
    computed["balance_sheet"]["leverage_metric_label"] = (
        _lev_label or f"Leverage ({prof.get('leverage_metric','Net Debt / EBITDA')})")
    computed["balance_sheet"]["leverage_metric_resolved"] = _lev_resolved

    errs, warns = eval_narrative(narrative)
    _deals = [d for d in ((_as_dict(narrative.get("m_and_a")) or {}).get("major_transactions") or [])
              if isinstance(d, dict)]
    if len(_deals) > 3:
        warns.append("ADVISORY: %d named transactions were supplied and the acquisitions table "
                     "lists three; the remainder are noted on the page but not itemised. Supply "
                     "the three that carry the spend if the ordering matters."
                     % len(_deals))
    if not _mt:
        warns.append("ADVISORY: meta.match_type is absent, so the identifier resolution behind this "
                     "report cannot be verified. Record the match type returned by search_tickerid "
                     "- a similarity-scored match returns a different issuer at status success.")
    errs += narrative_figure_conflicts(narrative, computed)
    errs += narrative_acquisition_conflicts(narrative, computed)
    errs += narrative_funding_attribution(narrative, computed)
    errs += narrative_allocation_conflicts(narrative, computed)
    errs += narrative_payout_conflicts(narrative, computed)
    _scc = str((computed.get("value_creation") or {}).get("share_count_change", "")).lower()
    if _scc.startswith("n/a"):
        for _t in _flatten_strings(narrative):
            _m = re.search(r"share count (?:rose|fell|declined|increased|change[d]?)[^.]{0,30}?"
                           r"([\d.]+)%", _t, re.I)
            if _m:
                errs.append("narrative states a share-count change of %s%% but the engine could "
                            "not compute one (the diluted share series does not span the window). "
                            "Remove the claim or state the shorter period." % _m.group(1))
    if errs:
        print(json.dumps({"status": "narrative_error", "errors": errs})); sys.exit(3)
    warns += density_warnings(narrative)
    conf, conf_basis = allocation_confidence(computed, raw, narrative)

    merged = {
        "computed": computed,
        "narrative": narrative,
        "sector": {
            "key": skey, "label": prof.get("label"),
            "priority_levers": prof.get("priority_levers", []),
            "leverage_metric": prof.get("leverage_metric"),
            "roic_framework": prof.get("roic_framework"),
        },
        "weights": weights, "overall": overall, "rating": rating,
        "band": band_range, "reconciliation": reconciliation,
        "allocation_confidence": conf, "allocation_confidence_basis": conf_basis,
        "skill_version": SKILL_VERSION,
    }
    try:
        import data_coverage as _DCV
        _rows_all = CC.unwrap(raw.get("actuals_annual"))
        _eng = (computed.get("meta") or {}).get("sector_key") or ""
        _cov = _DCV.coverage_report(_rows_all, engine=_eng)
        _corr = _DCV.corroborate(_rows_all)
        if _corr:
            _cov["corroboration_conflicts"] = _corr
        _cov["corroboration_basis"] = _DCV.corroboration_basis(_rows_all)
        computed["data_coverage"] = _cov
        merged["data_coverage"] = _cov
    except Exception as _e:
        computed["data_coverage"] = merged["data_coverage"] = {
            "error": str(_e),
            "control_unavailable": "corroboration/coverage could not run: %s: %s"
                                   % (type(_e).__name__, _e)}

    if os.environ.get("CAPALLOC_DUMP"):
        json.dump(merged, open(os.path.join(os.path.dirname(out_path) or ".", "merged.json"), "w"), indent=2)

    _mode = (os.environ.get("CAPALLOC_ENFORCE") or "block").lower().strip()
    if _mode not in ("block", "warn", "off"):
        print(json.dumps({"status": "error", "errors": [
            "CAPALLOC_ENFORCE=%r is not one of block, warn, off. Refusing rather than falling "
            "back to a weaker mode than the one asked for: only 'block' blocks, so an "
            "unrecognised value would silently downgrade enforcement to warn."
            % os.environ.get("CAPALLOC_ENFORCE")]}))
        sys.exit(2)
    _routing, _enf = None, []
    _ext = os.path.splitext(out_path)[1].lower()
    if _ext not in (".docx", ".pdf", ""):
        print(json.dumps({"status": "error", "errors": [
            "output path %r has extension %r; the renderer composes a Word document, so the "
            "output must be named .docx (or .pdf when CAPALLOC_FORMAT converts it). Refusing "
            "rather than writing a .docx under a name that misdescribes it." % (out_path, _ext)]}))
        sys.exit(2)
    try:
        import sector_router as _SR
        _codes = sorted({r.get("LTCODE") for r in CC.unwrap(raw.get("actuals_annual"))
                         if r.get("LTCODE")})
        _rmeta = dict(raw.get("meta") or {})
        _rmeta.update({k: v for k, v in (computed.get("meta") or {}).items() if v is not None})
        if not _rmeta.get("insync_subindustry") and _rmeta.get("classification"):
            _rmeta["insync_subindustry"] = _rmeta["classification"]
        if not _rmeta.get("classification") and _rmeta.get("insync_subindustry"):
            _rmeta["classification"] = _rmeta["insync_subindustry"]
        _routing = _SR.route(_rmeta, available_ltcodes=_codes)
    except Exception as _e:
        _enf.append("sector routing: %s" % str(_e).split(".")[0])
    merged["routing"] = _routing
    computed["routing"] = _routing
    try:
        import publication_gates as _PGpre
        _g5pre = _PGpre.check_coverage(computed.get("registry"),
                                       list((_routing or {}).get("required") or []))
        _authpre = (computed.get("score_coverage") or {}).get("publication_status")
        _g5s = str(_g5pre.get("status") or "").replace("suppress_score", "suppress")
        if _g5s and _authpre and _g5s != _authpre and computed.get("score_coverage"):
            computed["score_coverage"]["basis_disagreement"] = {
                "published_basis": _authpre,
                "published_coverage": (computed.get("score_coverage") or {}).get(
                    "retained_coverage", "n/a"),
                "second_basis": _g5s,
                "second_coverage": "%.0f%%" % ((_g5pre.get("coverage") or 0) * 100),
                "second_missing": list(_g5pre.get("missing") or []),
            }
    except Exception as _e:
        pass
    if _mode != "off":
        RR.render(merged, out_path)
        try:
            import publication_gates as _PG, subprocess as _sp
            _txt = _extract_docx_text(out_path)
            _reg = computed.get("registry")
            _req = list((_routing or {}).get("required") or [])
            _m = _PG.run_gates(_txt, registry=_reg, routing=_routing, required=_req,
                               strict=False)
            merged["gate_manifest"] = _m
            _g5 = (_m.get("G5_score_coverage") or {})
            _auth = (computed.get("score_coverage") or {}).get("publication_status")
            if _g5.get("status") and _auth and _g5["status"].replace("suppress_score",
                                                                    "suppress") != _auth:
                warns.append(
                    "Coverage measures disagree: the scorecard ladder reads %s (%s of weighted "
                    "dimensions) while the template-required set reads %s (%.0f%%, missing %s). "
                    "The scorecard ladder is the published basis; this is recorded so the gap is "
                    "visible rather than silently resolved."
                    % (_auth, (computed.get("score_coverage") or {}).get("retained_coverage",
                                                                         "n/a"),
                       _g5["status"], (_g5.get("coverage") or 0) * 100,
                       ", ".join(_g5.get("missing") or []) or "none"))
            _failed = [g for g in (_m.get("failed_gates") or []) if g != "G5_score_coverage"]
            if _failed:
                _enf.append("publication gates: " + ", ".join(_failed))
        except Exception as _e:
            _enf.append("gate run error: %s" % _e)
        _dc = merged.get("data_coverage") or {}
        if _dc.get("corroboration_conflicts"):
            _enf.append("data corroboration: " + "; ".join(_dc["corroboration_conflicts"])[:200])
        if _dc.get("control_unavailable"):
            _enf.append(_dc["control_unavailable"][:200])
        if _enf:
            merged["enforcement"] = _enf
            if _mode == "block":
                try:
                    os.remove(out_path)
                except OSError:
                    pass
                print(json.dumps({"status": "blocked", "errors": _enf}))
                sys.exit(3)
            print(json.dumps({"status": "warn", "warnings": _enf}), file=sys.stderr)
    else:
        RR.render(merged, out_path)

    SECTIONS = ["Executive summary", "Allocation mix and classification",
                "Deployment trend and mix shift", "Shareholder returns",
                "Reinvestment: capital expenditure and acquisitions", "Balance sheet and leverage",
                "Forward indicators as reported", "Peer comparison", "Outcome metrics",
                "What to monitor", "Scorecard"]
    included, excluded = [], []
    _mon = computed.get("monitor") or {}
    _dep = _mon.get("depth") or {}
    if _mon.get("available"):
        included.append("Quarterly Monitor + LTM shift")
    elif _dep.get("short"):
        excluded.append("Quarterly Monitor + LTM shift (EXCLUDED: actuals_quarterly returned %d "
                        "of %d quarters - truncated fetch, re-fetch with an explicit limit)"
                        % (_dep.get("quarters", 0), _dep.get("expected", 8)))
    else:
        excluded.append("Quarterly Monitor + LTM shift (EXCLUDED: no actuals_quarterly supplied)")
    fo = _as_dict(narrative.get("forward_outlook"))
    (included if fo.get("rows") else excluded).append(
        "Forward Outlook" if fo.get("rows") else "Forward Outlook (EXCLUDED: no guidance/consensus supplied)")
    (included if narrative.get("management_commentary") else excluded).append(
        "Management commentary" if narrative.get("management_commentary")
        else "Management commentary (EXCLUDED: none supplied)")
    manifest = {
        "skill": "capital-allocation-strategy", "skill_version": SKILL_VERSION,
        "ticker": computed["meta"]["ticker"], "sector": prof.get("label"),
        "sections_rendered": SECTIONS,
        "inputs_present": {
            "actuals_annual": bool(raw.get("actuals_annual")),
            "actuals_quarterly": bool(raw.get("actuals_quarterly")),
            "peers": len(peers), "tsr": bool(raw.get("tsr")),
            "forward_outlook": bool(fo.get("rows")),
            "management_commentary": len(_as_list(narrative.get("management_commentary"))),
        },
        "optional_included": included, "optional_excluded": excluded,
        "ratings": {"classification": computed["classification"]["label"],
                    "overall": overall, "rating": rating,
                    "value_creation_verdict": computed["value_creation"]["verdict"]},
        "qc": f"{computed['qc']['passed']}/{computed['qc']['total']}",
        "enforcement": _mode,
        "gate_manifest": merged.get("gate_manifest"),
        "score_coverage": computed.get("score_coverage"),
        "measures_evaluated": computed.get("measures_evaluated"),
        "weight_adjustments": computed.get("weight_adjustments"),
        "outcome_coverage": computed.get("outcome_coverage"),
        "publication": computed.get("publication"),
        "warnings": computed["warnings"] + warns,
    }
    try:
        mpath = (out_path[:-5] if out_path.lower().endswith(".docx") else out_path) + ".manifest.json"
        with open(mpath, "w") as mf:
            json.dump(manifest, mf, indent=2)
    except Exception:
        pass

    _fmt = (os.environ.get("CAPALLOC_FORMAT") or "both").lower()
    _pdf_path, _pdf_note = None, None
    if _fmt in ("pdf", "both") and out_path.lower().endswith(".docx"):
        _soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if _soffice:
            try:
                subprocess.run([_soffice, "--headless", "--convert-to", "pdf",
                                "--outdir", os.path.dirname(os.path.abspath(out_path)) or ".",
                                os.path.abspath(out_path)],
                               capture_output=True, timeout=180, check=False)
                _cand = os.path.abspath(out_path)[:-5] + ".pdf"
                if os.path.exists(_cand):
                    _pdf_path = _cand
                    if _fmt == "pdf":
                        try:
                            os.remove(out_path)
                        except OSError:
                            pass
                else:
                    _pdf_note = "PDF conversion produced no file; the .docx is the deliverable."
            except Exception as _e:
                _pdf_note = "PDF conversion failed (%s); the .docx is the deliverable." % type(_e).__name__
        else:
            _pdf_note = ("No PDF converter found (LibreOffice is required); the .docx is the "
                         "deliverable. Set CAPALLOC_FORMAT=docx to silence this.")

    print(json.dumps(_assert_paths({
        "status": "ok", "skill_version": SKILL_VERSION,
        "output_format": ("pdf" if _pdf_path and _fmt == "pdf" else
                          "pdf+docx" if _pdf_path else "docx"),
        "pdf": _pdf_path, "format_note": _pdf_note,
        "ticker": computed["meta"]["ticker"], "sector": prof.get("label"),
        "classification": computed["classification"]["label"],
        "overall": overall, "rating": rating,
        "allocation_confidence": conf,
        "scorecard": computed["scorecard"]["components"],
        "qc": f"{computed['qc']['passed']}/{computed['qc']['total']}",
        "gate_manifest": merged.get("gate_manifest"),
        "score_coverage": computed.get("score_coverage"),
        "measures_evaluated": computed.get("measures_evaluated"),
        "weight_adjustments": computed.get("weight_adjustments"),
        "outcome_coverage": computed.get("outcome_coverage"),
        "publication": computed.get("publication"),
        "warnings": computed["warnings"] + warns,
        "output": _pdf_path or out_path,
        "docx": None if (_pdf_path and _fmt == "pdf") else out_path,
    }), indent=2))


if __name__ == "__main__":
    main()
