#!/usr/bin/env python3
"""
inflection-monitor quality gates.

Runs after render.py and exits non-zero with the failing checks named. The point
is that a run either passes every gate or it does not ship: a note whose numbers
cannot be traced back to the engine is worse than no note, because it looks
exactly as authoritative as one whose numbers can.

Usage
-----
  python format_lint.py --computed out/X_inflection_computed.json \\
                        --html out/X_Inflection_Monitor_1Q27.html \\
                        --narrative narrative.json --chat out/chat.txt \\
                        [--pdf out/....pdf]

--narrative and --chat each enable checks that do not otherwise
run, so omitting them does not make a run cleaner, only less examined.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import html as html_lib
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from binding import required_binding_words

GATE_COUNT = 119
try:
    from render import GUIDE_ROWS_ON_PAGE, SECT_MARK
except ImportError:
    GUIDE_ROWS_ON_PAGE, SECT_MARK = 14, "<!--SECT-->"

BANNED = [
    r"attractive", r"compelling", r"undervalued", r"overvalued",
    r"investors should", r"we recommend", r"price target", r"target price",
    r"overweight", r"underweight", r"upside potential", r"downside potential",
    r"\bbuy\b", r"\bsell\b", r"outperform", r"underperform",
    r"\b(a|our|the)\s+hold\b(?!-)", r"\bhold\s+rating\b", r"\brated?\s+hold\b",
    r"conviction", r"our view is", r"we would", r"\bcheap\b", r"expensive",
]
BANNED_RE = [(p, re.compile(p, re.I)) for p in BANNED]
COVERAGE_RE = re.compile(
    r"\b(?:\d+(?:\s*(?:-|to)\s*\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|fifteen|twenty|thirty|single)[\s-]+(?:brokers?|analysts?)\b"
    r"|\b(?:broker|analyst)[\s-]*counts?\b|\bnumber of (?:brokers|analysts)\b"
    r"|\bthin(?:ly)?[\s-]+(?:coverage|covered)\b|\bcoverage\s+is\s+thin\b"
    r"|\b(?:low|limited|sparse)[\s-]+(?:broker[\s-]+|analyst[\s-]+)?coverage\b", re.I)

PUNCT = [
    ("em dash", re.compile(r"—")),
    ("en dash used as a range", re.compile(r"\d\s*–\s*\d")),
    ('"+/-" instead of the plus-minus sign', re.compile(r"\+/-")),
]

WRONG_FRAME = [
    (r"\bbeat\b", "beat"), (r"\bmiss(ed|es)?\b", "miss"),
    (r"\bsurprise\b", "surprise"), (r"\bconsensus\b", "consensus"),
    (r"\bthe street\b", "the Street"), (r"\bin-?line\b", "in-line"),
    (r"(?<!management )(?<!accounting )(?<!actuarial )(?<!company's )"
     r"(?<!an )(?<!a )(?<!its )\bestimates?\b", "estimate"), (r"\bwhisper\b", "whisper"),
]
WRONG_FRAME_RE = [(re.compile(p, re.I), label) for p, label in WRONG_FRAME]

OVERREACH = [
    (r"growth is being bought", "growth is being bought"),
    (r"\bbuying growth\b", "buying growth"),
    (r"channel stuff", "channel stuffing"),
    (r"\bmanipulat", "manipulation"),
    (r"\bmasking\b", "masking"),
    (r"\bpapering over\b", "papering over"),
    (r"source: not stated", "Source: not stated"),
    (r"\bnot stated\b", "not stated"),
    (r"unreported by source", "unreported by source"),
    (r"not disclosed by this company", "not disclosed by this company"),
    (r"the most useful row is the contradiction", "stale contradiction sentence"),
    (r"\bshed(ding)?\b|\bpruned?\b|\bwalk(ed|ing)? away from\b|"
     r"\bdeliberately\b|\bintentionally\b|\bby design\b|"
     r"\b(chose|elected|opted) to\b|\bengineered\b|\borchestrated\b|"
     r"\bstrategic(ally)? (exit|reduc|trimm?)|\bmanagement (decided|chose)\b",
     "a movement attributed to a management decision the data cannot show"),
]
OVERREACH_RE = [(re.compile(p, re.I), label) for p, label in OVERREACH]

DIR_WORDS = {
    "accelerating": re.compile(
        r"accelerat|reaccelerat|pick(ing|ed|s)?\s+up|speed(ing|ed)?\s+up|"
        r"stepp?(ed|ing)?\s+up|gather(ed|ing|s)?\s+pace|quicken|"
        r"faster than (it|the|last)|improv|steepen|inflect(ing|ed)? up|"
        r"rebound|carried the quarter|did the work", re.I),
    "decelerating": re.compile(
        r"decelerat|slow(ing|ed|s|down)?|cool(ing|ed)?|fad(ing|ed)?|soften|"
        r"deteriorat|eas(ing|ed)|narrow(ing|ed)?|roll(ing)? over|"
        r"lost pace|los(ing|es) pace|slipp?(ed|ing)?|went backwards?|"
        r"came off|stepped down|gave way", re.I),
}

DRIVER_CLAIM = re.compile(
    r"\b(driven by|attributable to|because of|on the back of|reflects?\s+the\s+|"
    r"points? (?:at|to)|locates? the quarter in|explained by|the result of|"
    r"comes? from|down to)\b", re.I)
DRIVER_TERM = re.compile(
    r"\b(price|pricing|volume|volumes|mix|rate recovery|rate case|"
    r"cost inflation|utilisation|utilization|demand|share gain|"
    r"market share|weather)\b", re.I)
LOCUS_CLAIM = re.compile(
    r"\b(arising|arises|originat\w+|locat\w+|sits?|coming|comes)\b[^.]{0,80}?"
    r"\b(?:in|from|within)\b[^.]{0,40}?"
    r"\b(operating line|operating result|below the (?:revenue )?line|"
    r"beneath it|below it|above the line|core \w+ business|top line)\b", re.I)
LOCUS_RATHER = re.compile(
    r"\brather than\b|\bnot (?:in|from|below|above)\b|\binstead of\b|"
    r"\bpurely\b|\bnot merely\b", re.I)
DRIVER_NEGATION = re.compile(
    r"\b(not attributable|no disclosed decomposition|does not (?:assert|separate|claim)|"
    r"cannot separate|unresolved|not established|does not support|"
    r"the note does not|no decomposition)\b", re.I)

TAG = re.compile(r"<[^>]+>")
NUMTOK = re.compile(r"-?\d[\d,]*\.?\d*")
UNRESOLVED = re.compile(r"\{\{[a-zA-Z0-9_.]+\}\}")


QUOTE_BLOCK = re.compile(r'(?is)<div class="quote(?: [^"]*)?">.*?</div>\s*</div>')

REFERENCE_LABELS = {
    "provenanceandmethod",
    "provenance:guidancerowsasreturned",
    "provenance:documentsbehindquotedmaterial",
    "sources,guidanceandmethodology",
}

SRC_CELL = re.compile(r'(?is)<td class="src">.*?</td>')
SRC_SPAN = re.compile(r'(?is)<span class="src-text">.*?</span>')


def visible_text(html: str) -> str:
    body = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    body = re.sub(r"(?is)<svg\b.*?</svg>", " ", body)
    body = re.sub(r'(?is)\stitle="[^"]*"', " ", body)
    text = TAG.sub(" ", body)
    return html_lib.unescape(text)


def own_voice_text(html: str) -> str:
    return visible_text(SRC_SPAN.sub(" ", SRC_CELL.sub(" ", QUOTE_BLOCK.sub(" ", html))))


def pdf_pages(path):
    try:
        with open(path, "rb") as fh:
            blob = fh.read()
    except OSError:
        return None
    counts = [int(m) for m in re.findall(rb"/Count\s+(\d+)", blob)]
    if counts:
        return max(counts)
    n = len(re.findall(rb"/Type\s*/Page[^s]", blob))
    return n or None


def pdf_pages_text(path):
    import subprocess
    try:
        out = subprocess.run(["pdftotext", "-layout", path, "-"],
                             capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    return out.stdout.decode("utf-8", "replace").split("\f")


def numeric_tokens(text: str):
    return {t.replace(",", "").rstrip(".") for t in NUMTOK.findall(text)}


def load_narrative(path):
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh) or {}
    flat = {}
    for k, v in raw.items():
        if isinstance(v, (list, tuple)):
            flat[k] = " ".join(
                (b if (b := str(x).strip()).endswith((".", "?", "!")) else b + ".")
                for x in v if str(x).strip())
        else:
            flat[k] = v
    return flat, raw


def engine_tokens(p) -> set:
    toks = set()

    SCALE_CUTS = (1e3, 1e6, 1e9, 1e12)

    def add(v, dps=(0, 1, 2, 3, 4)):
        if v is None:
            return
        if isinstance(v, str):
            for m in NUMTOK.findall(v):
                toks.add(m.replace(",", "").rstrip("."))
            return
        try:
            f = float(v)
        except (TypeError, ValueError):
            return
        for dp in dps:
            toks.add(f"{f:.{dp}f}".replace(",", ""))
            toks.add(f"{abs(f):.{dp}f}".replace(",", ""))
        toks.add(str(int(f)) if float(f).is_integer() else str(f))
        for cut in SCALE_CUTS:
            if abs(f) >= cut:
                for dp in (1, 2):
                    toks.add(f"{f / cut:.{dp}f}".replace(",", ""))
                    toks.add(f"{abs(f) / cut:.{dp}f}".replace(",", ""))

    for key in ("accelerating", "decelerating", "deep_dive", "primary_context"):
        for r in p.get(key, []):
            add(r.get("name"))
            add(r.get("ltcode"))
            add(r.get("display_value"))
            add(r.get("prior_year_value"))
            add(r.get("absolute_change"))
            add(r.get("unit_label"))
            add(r.get("basis_qualifier"))
            for f in ("latest_value", "latest_growth", "prev_growth",
                      "latest_accel", "z", "z_display", "streak", "quarters",
                      "economic_impact_display"):
                add(r.get(f))
            _a = r.get("assessment") or {}
            _em = _a.get("economic_materiality") or {}
            add(_em.get("impact")); add(_em.get("share_of_revenue"))
            for arr in ("growth_series", "accel_series", "value_series"):
                for v in r.get(arr) or []:
                    add(v)
            for v in r.get("period_series") or []:
                add(v)
    for key in ("accelerating", "decelerating", "deep_dive", "primary_context"):
        for r in p.get(key, []):
            fb = r.get("forward_bar") or {}
            for f in ("next_base_value", "level_to_hold_growth",
                      "level_to_hold_display",
                      "growth_if_level_flat", "comp_contribution"):
                add(fb.get(f))
            add(fb.get("next_base_period"))
            add(r.get("streak_start"))
            add(r.get("lead_quarters"))
    gc = p.get("guidance_check") or {}
    add(gc.get("next_quarter"))
    for _k in ("headline_moves", "accelerating", "decelerating", "deep_dive", "primary_context"):
        for _r in p.get(_k) or []:
            _at = _r.get("adjusted_twin") or {}
            add(_at.get("display")); add(_at.get("growth_display")); add(_at.get("latest_growth"))
    for o in p.get("one_time_items") or []:
        for f in ("display", "name", "latest_display", "year_ago_display", "year_ago_period",
                  "revenue_bps", "year_ago_revenue_bps"):
            add(o.get(f))
    for _k in ("headline_moves", "accelerating", "decelerating", "deep_dive", "primary_context"):
        for _r in p.get(_k) or []:
            add(_r.get("period_evidence"))
    for _r in ((p.get("cash_conversion") or {}).get("rows") or []):
        add(_r.get("period_evidence"))
    _qs = p.get("quarter_strip") or {}
    for c in _qs.get("cells") or []:
        for f in ("display", "name", "display_value", "latest_value", "latest_growth",
                  "prev_growth", "prev_period", "year_ago_period", "year_ago_rate"):
            add(c.get(f))
        add((c.get("exclusion") or {}).get("excl_display_value"))
        add((c.get("exclusion") or {}).get("item"))
    add(_dmy_lint(p.get("earnings_date"))); add(_dmy_lint(p.get("latest_quarter_end")))
    _no = p.get("non_operating") or {}
    add(_no.get("value_display")); add(_no.get("adjusted_value_display"))
    _qg = _qs.get("guide") or {}
    for f in ("display", "name", "target_period", "first_display", "latest_display",
              "first_source_period", "latest_source_period", "first_text", "changed_in"):
        add(_qg.get(f))
    for ex in p.get("company_exclusions") or []:
        for f in ("base_display", "item", "period", "prior_period", "base_display_value",
                  "excl_display_value", "gap_display", "prior_gap_display",
                  "base_growth_display", "excl_growth_display", "item_per_share_display",
                  "prior_item_per_share_display"):
            add(ex.get(f))
    for r in gc.get("revisions", []):
        add(r.get("display")); add(r.get("target_period"))
        for f in ("first_display", "first_text", "latest_display", "latest_text",
                  "first_source_period", "latest_source_period", "midpoint_change_pct",
                  "midpoint_change_bps", "changed_in", "year_first_display",
                  "year_first_source_period"):
            add(r.get(f))
    for r in gc.get("other_horizon", []):
        add(r.get("guide_display_text"))
    for key in ("scored", "forward_uncompared"):
        for r in gc.get(key, []):
            add(r.get("name")); add(r.get("display")); add(r.get("guide_text"))
            add(r.get("target_period")); add(r.get("source_period"))
            for f in ("guide_midpoint", "guide_low", "guide_high",
                      "guide_implied_growth", "guide_implied_accel",
                      "guide_display_text", "continuation_threshold",
                      "latest_growth", "latest_accel", "z"):
                add(r.get(f))
    for r in gc.get("other_horizon", []):
        add(r.get("name")); add(r.get("display")); add(r.get("target_period"))
        add(r.get("guide_text")); add(r.get("guide_display_text"))
        add(r.get("guide_midpoint"))
    for key in ("delivered", "delivered_uncompared"):
        for r in gc.get(key, []):
            add(r.get("name")); add(r.get("display")); add(r.get("guide_text"))
            add(r.get("target_period")); add(r.get("source_period"))
            for f in ("guide_low", "guide_high", "guide_midpoint", "actual", "gap",
                      "gap_display_value", "latest_growth",
                      "guide_display_text", "actual_display", "versus_display"):
                add(r.get(f))
    for n in gc.get("outlook_notes", []):
        add(n.get("text")); add(n.get("label")); add(n.get("target_period"))
        add(n.get("source_period"))
    for h in p.get("heatmap", []):
        for c in h.get("cells", []):
            add(c.get("growth"))
    cc = p.get("cash_conversion") or {}
    add(cc.get("revenue_growth")); add(cc.get("revenue_accel"))
    add(cc.get("conclusion"))
    for r in cc.get("rows", []):
        add(r.get("latest_growth")); add(r.get("gap_versus_revenue")); add(r.get("display"))
        add(r.get("level")); add(r.get("prior_level"))
        add(r.get("level_display")); add(r.get("prior_level_display"))
    for r in p.get("monitor", []):
        add(r.get("current_fact")); add(r.get("next_proof")); add(r.get("variable"))
        add(r.get("level_to_hold")); add(r.get("growth_if_flat")); add(r.get("next_base_period"))
    for r in p.get("headline_moves", []):
        add(r.get("display")); add(r.get("tier"))
        add(r.get("display_value")); add(r.get("unit_label"))
        for f in ("latest_value", "latest_growth", "prev_growth", "latest_accel",
                  "z", "z_display", "streak", "quarters", "priority"):
            add(r.get(f))
    for key in ("accelerating", "decelerating", "deep_dive", "primary_context"):
        for r in p.get(key, []):
            add(r.get("display")); add(r.get("tier"))
            for a in (r.get("attribution") or []):
                add(a)
    for key in ("headline_moves", "deep_dive", "primary_context"):
        for r in p.get(key, []):
            t = r.get("reported_twin") or {}
            add(t.get("latest")); add(t.get("latest_display"))
            add(t.get("gap_bps_now")); add(t.get("gap_bps_max")); add(t.get("display"))
            _yc = r.get("year_ago_comparison") or {}
            for _f in ("year_ago_period", "year_ago_growth_display", "two_year_period",
                       "two_year_change_display", "two_year_level_display"):
                add(_yc.get(_f))
    for _e in (p.get("adjustment_quality") or []):
        for _x in (_e, _e.get("also") or {}):
            add(_x.get("size_display")); add(_x.get("label")); add(_x.get("quarter"))
            add(_x.get("page")); add(_x.get("move_display"))
            add(_x.get("ex_level_display")); add(_x.get("ex_move_display"))
    for key in ("accelerating", "decelerating", "headline_moves", "deep_dive", "primary_context"):
        for r in p.get(key, []):
            _sb = r.get("small_base") or {}
            for _f in ("base_period", "base_display", "typical_display"):
                add(_sb.get(_f))
            _pm = r.get("promote_match") or {}
            for _sc in (p.get("source_corrections") or []):
                for _f in ("target_period", "source_period", "total_as_filed", "cc_as_filed",
                           "fx_text", "ma_text"):
                    add(_sc.get(_f))
            for _f in ("increase_display", "promote_display", "period"):
                add(_pm.get(_f))
            c = r.get("calculated_twin") or {}
            add(c.get("latest_display")); add(c.get("gap_bps_now")); add(c.get("gap_bps_max"))
            add(c.get("display"))
    for key in ("headline_moves", "accelerating", "decelerating", "deep_dive",
                "primary_context"):
        for r in p.get(key, []):
            b = r.get("bridge") or {}
            add(b.get("display")); add(b.get("total")); add(b.get("total_display"))
            for bp in b.get("parts") or []:
                add(bp.get("value")); add(bp.get("display"))
    for r in p.get("provenance", []):
        add(r.get("name")); add(r.get("ltcode")); add(r.get("uic"))
        add(r.get("quarters")); add(r.get("first_period")); add(r.get("last_period"))
        for k, v in (r.get("source_mix") or {}).items():
            add(k); add(v)

    nr = p.get("not_retrieved") or {}
    for m in nr.get("missing") or []:
        add(m.get("ltcode")); add(m.get("name"))
    add(nr.get("planned")); add(nr.get("retrieved")); add(nr.get("missing_count"))
    toks.add(str(max(len(nr.get("missing") or []) - 10, 0)))
    for r in p.get("context_implications") or []:
        add(r.get("metric")); add(r.get("z_display")); add(r.get("latest_accel"))
    for v in (p.get("implication_counts_context") or {}).values():
        add(v)
    for v in (p.get("implication_counts") or {}).values():
        add(v)

    sr = p.get("source_records") or {}
    _gr = len(sr.get("guidance_rows") or [])
    toks.add(str(_gr))
    toks.add(str(max(_gr - GUIDE_ROWS_ON_PAGE, 0)))
    for g in sr.get("guidance_rows") or []:
        for f in ("raw_guide", "metric", "source_name", "ltcode", "target_period",
                  "source_period", "document", "parsed_midpoint",
                  "guide_implied_growth", "continuation_threshold"):
            add(g.get(f))
    for d in sr.get("documents") or []:
        for f in ("document", "document_id", "date", "given_with", "page", "used_for"):
            add(d.get(f))
    for r in p.get("quiet_headline", []):
        add(r.get("name")); add(r.get("prev_period"))
        for f in ("latest_value", "latest_growth", "latest_accel", "z_display", "streak", "prev_growth"):
            add(r.get(f))
    for h in p.get("heatmap", []):
        add(h.get("name"))
        for c in h["cells"]:
            add(c.get("z"))
            add(c.get("accel"))
            add(c.get("period"))
    for d in list(p.get("divergences", [])) + list(p.get("confirmations") or []):
        add(d.get("headline"))
        for m in d["metrics"]:
            add(m.get("name"))
            add(m.get("z"))
            add(m.get("accel"))
            add(m.get("growth")); add(m.get("prev_growth")); add(m.get("prev_period"))
    for st in [p.get("primary_story") or {}] + list(p.get("supporting_stories") or []):
        add(st.get("headline"))
        for m in st.get("metrics") or []:
            add(m.get("name")); add(m.get("display")); add(m.get("z")); add(m.get("accel"))
            add(m.get("growth")); add(m.get("prev_growth")); add(m.get("prev_period"))
    for e in p.get("excluded", []):
        add(e.get("name"))
        add(e.get("reason"))
    add(p.get("company"))
    for v in p.get("coverage", {}).values():
        add(v)
    for v in p.get("constants", {}).values():
        add(v)
        if isinstance(v, float):
            toks.add(f"{v:.0%}".rstrip("%"))
    add(p.get("latest_quarter"))
    for v in (p.get("scan_period") or {}).values():
        add(v)
    for band in (2.5, 1.5, 0.75, 0.0):
        add(band)
    add(p.get("generated_utc"))
    add(p.get("engine_version"))
    add(p.get("report_date"))
    for e in p.get("excluded", []):
        add(e.get("quarters"))
    toks.add(str(len(p.get("excluded", []))))
    for _it in p.get("non_operating_window") or []:
        add(_it.get("period")); add(_it.get("value_display")); add(_it.get("display"))
        _sh = _it.get("share_of_operating_profit_pct")
        if _sh is not None:
            toks.add("%.1f" % float(_sh))
    for _ec in p.get("exclusion_categories") or []:
        if isinstance(_ec, dict) and isinstance(_ec.get("count"), int):
            toks.add(str(_ec["count"]))
    for d in p.get("divergences", []):
        toks.add(str(len(d["metrics"])))
        add((d.get("bridge") or {}).get("text"))
        for _lm in (d.get("metrics") or []):
            if isinstance(_lm.get("bridge"), str):
                add(_lm["bridge"])
        add((d.get("cash_context") or {}).get("text"))
    for _k in ("rate_kpis", "deep_dive", "headline_moves", "accelerating", "decelerating",
               "primary_context", "quiet_headline"):
        for r in p.get(_k) or []:
            for f in ("latest_value", "latest_growth", "prev_growth", "latest_accel",
                      "z", "z_display", "streak", "quarters"):
                add(r.get(f))
            for v in (r.get("rate_view") or {}).values():
                add(v)
            for arr in ("value_series", "period_series"):
                for v in r.get(arr) or []:
                    add(v)
    add(p.get("latest_quarter_end"))
    for e in p.get("label_evidence") or []:
        add(e.get("text"))
    for _bk in ("forward_uncompared", "scored", "other_horizon", "delivered"):
        for _g in (p.get("guidance_check") or {}).get(_bk) or []:
            add(_g.get("latest_growth"))
    for _k in ("headline_moves", "accelerating", "decelerating", "primary_context", "deep_dive"):
        for r in p.get(_k) or []:
            _t = r.get("exclusion_twin") or {}
            for f in ("growth_display", "prev_growth_display", "level_display", "prev_period",
                      "latest_growth", "prev_growth", "item", "display"):
                add(_t.get(f))
    reasons = {}
    for e in p.get("excluded", []):
        reasons[e["reason"]] = reasons.get(e["reason"], 0) + 1
    for n in reasons.values():
        toks.add(str(n))
    return {t for t in toks if t}


def _dmy_lint(d):
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(d or ""))
    if not m:
        return None
    return "%d %s %s" % (int(m.group(3)), "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()[int(m.group(2)) - 1], m.group(1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--computed", required=True)
    ap.add_argument("--html", required=True)
    ap.add_argument("--narrative")
    ap.add_argument("--pdf")
    ap.add_argument("--chat", help="the chat summary text emitted by compute.py chat")
    ap.add_argument("--commentary", help="the commentary map passed to render.py")
    ap.add_argument("--max-pages", type=int, default=4)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--json-out", help="also write the JSON result here (for qc_summary), "
                                       "printing the text summary as usual")
    args = ap.parse_args()

    with open(args.computed, encoding="utf-8") as fh:
        p = json.load(fh)
    with open(args.html, encoding="utf-8") as fh:
        doc = fh.read()
    text = visible_text(doc)
    page_flat = re.sub(r"\s+", " ", text).lower()
    prov_print_mode = (re.search(r"insync-provenance'? content='?\"?(\w+)", doc)
                       or re.search(r"(?!)", ""))
    prov_print_mode = prov_print_mode.group(1) if prov_print_mode else "standard"
    own = own_voice_text(doc)

    failures, warnings = [], []
    for _flag in ("narrative", "commentary", "chat"):
        _pth = getattr(args, _flag, None)
        if _pth and not os.path.exists(_pth):
            failures.append("--%s %s does not exist, so the checks that read it did not run"
                            % (_flag, _pth))

    for pat, rx in BANNED_RE:
        m = rx.search(own)
        if m:
            failures.append(f"advisory language: {m.group(0)!r} (pattern {pat})")
    _cov = COVERAGE_RE.search(own)
    if _cov:
        failures.append(f"broker count or coverage-depth language: {_cov.group(0)!r}")

    for label, rx in PUNCT:
        if rx.search(own):
            failures.append(f"house punctuation: {label}")

    narr_raw = {}
    narr = {}

    if UNRESOLVED.search(doc):
        failures.append(f"unresolved placeholder(s): "
                        f"{', '.join(sorted(set(UNRESOLVED.findall(doc)))[:5])}")

    if args.narrative and os.path.exists(args.narrative):
        narr, narr_raw = load_narrative(args.narrative)
        for k, v in narr.items():
            if k.startswith("_") or not isinstance(v, str):
                continue
            stripped = re.sub(r"\{\{[a-zA-Z0-9_.]+\}\}", "", v)
            if re.search(r"\d", stripped):
                failures.append(f"narrative section {k!r} contains a literal digit; "
                                f"figures must arrive as placeholders")

    if args.narrative and os.path.exists(args.narrative):
        narr, narr_raw = load_narrative(args.narrative)
        for k, v in narr.items():
            if k.startswith("_") or not isinstance(v, str) or not v.strip():
                continue
            for rx, label in WRONG_FRAME_RE:
                if rx.search(v):
                    failures.append(
                        f"narrative section {k!r} uses {label!r}: this skill measures "
                        f"reported history against itself, never against consensus")
        unresolved_any = any(
            re.search(r"unresolved|no disclosed decomposition|no disclosed breakdown",
                      " ".join(str(a) for a in (r.get("attribution") or [])), re.I)
            for key in ("accelerating", "decelerating", "primary_context", "deep_dive")
            for r in (p.get(key) or []))
        for k, v in narr.items():
            if k.startswith("_") or not isinstance(v, str) or k.startswith("dd"):
                continue
            if not unresolved_any:
                continue
            for sent in re.split(r"(?<=[.!?])\s+", v):
                if (DRIVER_CLAIM.search(sent) and DRIVER_TERM.search(sent)
                        and not DRIVER_NEGATION.search(sent)):
                    failures.append(
                        f"narrative section {k!r} names a driver while the engine "
                        f"recorded the driver as unresolved on at least one displayed "
                        f"row: {sent.strip()[:110]!r}")
                    break
                if (LOCUS_CLAIM.search(sent) and LOCUS_RATHER.search(sent)
                        and not DRIVER_NEGATION.search(sent)):
                    failures.append(
                        f"narrative section {k!r} places a move in one part of the "
                        f"income statement and rules out another, which is an "
                        f"attribution the engine recorded as unresolved: "
                        f"{sent.strip()[:110]!r}")
                    break
        SELF_REF = re.compile(r"\bthe note\b|\bthis note\b|\bthe scan\b|"
                              r"\bthis run\b|\bthe engine records\b|\bthe prose\b", re.I)
        dd_texts = [(k, narr.get(k) or "") for k in ("dd1", "dd2", "dd3")
                    if (narr.get(k) or "").strip()]
        for k, v in dd_texts:
            m = SELF_REF.search(v)
            if m:
                failures.append(
                    f"narrative {k!r} talks about the document rather than the "
                    f"business: {m.group(0)!r}. An analyst writes about the company "
                    f"and the data; where something cannot be established, say so "
                    f"in the active voice without naming the note")
        if len(dd_texts) >= 3:
            firsts = []
            for _k, _raw_v in dd_texts:
                v = re.sub(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}",
                           lambda m: str((p.get("placeholders") or {})
                                         .get(m.group(1), " ")), _raw_v)
                if not v.strip():
                    continue
                ws = re.findall(r"[a-z']+",
                                re.split(r"(?<=[.!?])\s+", v.strip())[0].lower())
                ws = [w for w in ws if w not in ("the", "a", "an", "its", "this",
                                                 "that", "these", "those")]
                firsts.append(ws[0] if ws else "")
            if len(set(firsts)) == 1:
                warnings.append("all three dive paragraphs open on the same word "
                                "(%r); vary the construction" % firsts[0])

        dd = p.get("deep_dive") or []
        for i, row in enumerate(dd[:5], 1):
            dd_text = narr.get(f"dd{i}") or ""
            if not dd_text.strip():
                continue
            want = row.get("streak_dir")
            rx = DIR_WORDS.get(want)
            if rx and row.get("lower_is_better"):
                other = "decelerating" if want == "accelerating" else "accelerating"
                rx = re.compile(rx.pattern + "|" + DIR_WORDS[other].pattern, re.I)
            attribs = " ".join(str(a) for a in (row.get("attribution") or []))
            if re.search(r"unresolved|no disclosed decomposition|no disclosed breakdown", attribs, re.I):
                for sent in re.split(r"(?<=[.!?])\s+", dd_text):
                    if (DRIVER_CLAIM.search(sent) and DRIVER_TERM.search(sent)
                            and not DRIVER_NEGATION.search(sent)):
                        failures.append(
                            f"narrative dd{i} attributes {row.get('display') or row['name']!r} "
                            f"to a named driver while the engine recorded the driver as "
                            f"unresolved: {sent.strip()[:110]!r}. The engine has no "
                            f"decomposition, so the note may describe the move and may say "
                            f"the driver is unresolved, but may not name one")
                        break
                for sent in re.split(r"(?<=[.!?])\s+", dd_text):
                    if (LOCUS_CLAIM.search(sent) and LOCUS_RATHER.search(sent)
                            and not DRIVER_NEGATION.search(sent)):
                        failures.append(
                            f"narrative dd{i} places the move in "
                            f"{row.get('display') or row['name']!r} in one part of the "
                            f"income statement and rules out another while the engine "
                            f"recorded the driver as unresolved: {sent.strip()[:110]!r}")
                        break
            first = re.split(r"(?<=[.!?])\s+", dd_text.strip())[0] if dd_text.strip() else ""
            lead = re.split(r",|\bwhile\b|\bbut\b", first, maxsplit=1)[0]
            if len(re.findall(r"[\w'-]+", lead)) <= 12 and re.search(
                    r"\b(is|are|was|were|has|have)\s+"
                    r"(accelerat\w*|decelerat\w*|slow\w*|ris\w*|fall\w*)", lead, re.I):
                failures.append(
                    f"narrative dd{i} leads with the direction: {lead.strip()[:64]!r}. "
                    f"The verdict chip and the measurement strip above it already "
                    f"state the metric and which way it moved; open with what a "
                    f"reader does not already have")
            PACE_OF_FALL = (r"(?:(?:pace|rate) of (?:decline|fall|decrease|contraction|erosion)"
                            r"|(?:decline|contraction) (?:pace|rate))"
                            r"(?:\s+\w+){0,3}?\s+")
            PACE_OF_RISE = (r"(?:(?:pace|rate) of (?:growth|increase|rise|expansion|improvement)"
                            r"|(?:growth|increase|expansion|improvement) (?:pace|rate))"
                            r"(?:\s+\w+){0,3}?\s+")
            SLOWED = r"(?:eas\w+|slow\w+|stopp?\w*|narrow\w+|moderat\w+|cool\w+|fell|fad\w+)"
            SPED = r"(?:steepen\w+|worsen\w+|accelerat\w+|deepen\w+|widen\w+|quicken\w+|rose|pick\w+ up)"
            PACE_FORM = {
                "accelerating": re.compile(PACE_OF_FALL + SLOWED + r"|" + PACE_OF_RISE + SPED, re.I),
                "decelerating": re.compile(PACE_OF_RISE + SLOWED + r"|" + PACE_OF_FALL + SPED, re.I),
            }
            alt = PACE_FORM.get(want)
            if rx and not rx.search(dd_text) and not (alt and alt.search(dd_text)):
                failures.append(
                    f"narrative dd{i} never states that {row['name']!r} is {want}; "
                    f"either the prose is written against the wrong row or the "
                    f"direction is left for the reader to infer")

    HEDGED = re.compile(
        r"requires confirmation|cannot be (established|determined|shown)|"
        r"not established|is not shown|does not establish|the extent to which|"
        r"this scan does not|not sufficient to determine|"
        r"do(es)? not establish which", re.I)
    for _sent in re.split(r"(?<=[.!?])\s+", own):
        if HEDGED.search(_sent):
            continue
        for rx, label in OVERREACH_RE:
            if rx.search(_sent):
                failures.append(
                    f"overreach or unsourced provenance: {label!r}. The note may report "
                    f"what a figure did, not assert why anyone did it, and every displayed "
                    f"metric must carry a source class")

    caps = [("accelerating", 5), ("decelerating", 5), ("deep_dive", 5),
            ("heatmap", 8), ("headline_moves", 5), ("monitor", 5)]
    for key, cap in caps:
        n = len(p.get(key) or [])
        if n > cap:
            failures.append(f"{key}: {n} rows exceeds the display cap of {cap}")
    ranked_total = len(p.get("accelerating") or []) + len(p.get("decelerating") or [])
    if ranked_total > 10:
        failures.append(f"ranked tables carry {ranked_total} rows; the cap is 10")

    valid_imp = {"Favourable", "Unfavourable", "Mixed", "Ambiguous"}
    valid_conf = {"High", "Moderate", "Low", "Withheld"}
    for key in ("accelerating", "decelerating", "deep_dive", "headline_moves"):
        for r in p.get(key) or []:
            if r.get("implication") not in valid_imp:
                failures.append(f"{key}: {r.get('ltcode')} has no business implication")
            if r.get("confidence") not in valid_conf:
                failures.append(f"{key}: {r.get('ltcode')} has no confidence grade")
            if not r.get("display"):
                failures.append(f"{key}: {r.get('ltcode')} has no reader-facing name")
            if r.get("unit") == "bps" and r.get("latest_growth") is not None:
                pass
    if p.get("direction") not in {"Accelerating", "Decelerating", "Mixed",
                                  "No material inflection"}:
        failures.append("no overall direction was set")

    allowed_verdicts = {"continues acceleration", "continues at a slower rate",
                        "stabilises", "turns negative", "reverses to growth",
                        "not comparable", "unparsed"}
    for r in (p.get("guidance_check") or {}).get("scored", []):
        if r.get("verdict") not in allowed_verdicts:
            failures.append(f"guidance verdict {r.get('verdict')!r} is not a continuation "
                            f"classification")

    for r in p.get("provenance") or []:
        for f in ("ltcode", "name", "display", "quarters", "first_period", "last_period"):
            if not str(r.get(f) or "").strip():
                failures.append(f"provenance for {r.get('ltcode')} is missing {f}")
        if not (r.get("source_mix") or r.get("class")):
            failures.append(f"provenance for {r.get('ltcode')} has no source class")

    allowed = engine_tokens(p)
    if args.commentary and os.path.exists(args.commentary):
        with open(args.commentary, encoding="utf-8") as fh:
            comm = json.load(fh) or {}
        REQ = ("text", "speaker", "role", "quarter", "document", "document_date",
               "document_id", "attributed_to_metric")
        for code, v in comm.items():
            if not isinstance(v, dict):
                failures.append(f"commentary for {code} is not an attributed object; a "
                                f"quotation without speaker, role, document and date is "
                                f"dropped by the renderer and should not be supplied")
                continue
            miss = [f for f in REQ if not str(v.get(f) or "").strip()]
            if miss:
                warnings.append(f"commentary for {code} lacks {', '.join(miss)}; the "
                                f"renderer withholds it")
        for v in comm.values():
            fields = v.values() if isinstance(v, dict) else [v]
            for blob in fields:
                if isinstance(blob, str):
                    allowed |= {m.replace(",", "").rstrip(".")
                                for m in NUMTOK.findall(blob)}
    for _tok in NUMTOK.findall(str(p.get("ticker") or "")):
        allowed.add(_tok.replace(",", "").rstrip("."))
    page = numeric_tokens(text)
    orphans = sorted(t for t in page - allowed if len(t.replace("-", "")) > 0)
    orphans = [t for t in orphans if t not in {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9"}]
    if orphans:
        failures.append(f"{len(orphans)} numeric token(s) on the page do not trace to "
                        f"computed.json: {', '.join(orphans[:12])}")

    if args.chat and os.path.exists(args.chat):
        with open(args.chat, encoding="utf-8") as fh:
            chat = fh.read()
        chat_orphans = sorted(t for t in numeric_tokens(chat) - allowed
                              if t not in set("0123456789"))
        if chat_orphans:
            failures.append(
                f"{len(chat_orphans)} numeric token(s) in the chat summary do not trace "
                f"to computed.json: {', '.join(chat_orphans[:10])}. Emit it with "
                f"`compute.py chat` and relay it verbatim rather than composing it")

    _pc = p.get("published_counts")
    if _pc is None:
        failures.append("published_counts is missing; every count on the page must come "
                        "from one tally (gate 114)")
    else:
        if _pc.get("accelerating", 0) + _pc.get("decelerating", 0) != _pc.get("total"):
            failures.append("published counts do not add up: %s accelerating + %s decelerating "
                            "!= %s (gate 114)" % (_pc.get("accelerating"), _pc.get("decelerating"),
                                                  _pc.get("total")))
        if sum((_pc.get("implication") or {}).values()) != _pc.get("total"):
            failures.append("published implication counts do not add up to %s (gate 114)"
                            % _pc.get("total"))
        _cov_tot = (p.get("coverage") or {}).get("material_total")
        if _cov_tot is not None and _cov_tot != _pc.get("total"):
            failures.append("coverage.material_total %s != published total %s (gate 114)"
                            % (_cov_tot, _pc.get("total")))
        _vis114 = re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", doc)))
        for _m in re.finditer(r"Of the (\d+) lines[^:]*: (\d+) accelerating, (\d+) slowing", _vis114):
            if (int(_m.group(1)), int(_m.group(2)), int(_m.group(3))) != \
                    (_pc.get("total"), _pc.get("accelerating"), _pc.get("decelerating")):
                failures.append("the page's count %r disagrees with the published tally "
                                "(gate 114)" % _m.group(0))
        _mi = re.search(r"By what the move means: ([^.]*)\.", _vis114)
        if _mi:
            _parts = dict((w.capitalize(), int(n)) for n, w in
                          re.findall(r"(\d+) (favourable|unfavourable|mixed|ambiguous)", _mi.group(1)))
            _want = {k: v for k, v in (_pc.get("implication") or {}).items() if v}
            if _parts != _want:
                failures.append("the page's implication counts %r disagree with the published "
                                "tally %r (gate 114)" % (_parts, _want))
        if args.chat and os.path.exists(args.chat):
            with open(args.chat, encoding="utf-8") as fh:
                _chat114 = fh.read()
            _mc = re.search(r"(\d+) accelerating and (\d+) decelerating cleared", _chat114)
            if _mc and (int(_mc.group(1)), int(_mc.group(2))) != \
                    (_pc.get("accelerating"), _pc.get("decelerating")):
                failures.append("the chat summary's counts %r disagree with the published "
                                "tally (gate 114)" % _mc.group(0))

    for key, sign in (("accelerating", 1), ("decelerating", -1)):
        rows = p.get(key, [])
        zs = [r["z"] for r in rows]
        if any(z is None for z in zs):
            failures.append(f"{key}: a ranked row has no z-score")
        pr = [r.get("priority") for r in rows]
        if any(x is None for x in pr):
            failures.append(f"{key}: a ranked row has no priority score")
        elif pr != sorted(pr, reverse=True):
            failures.append(f"{key}: rows are not ordered by the published priority score")
        for r in rows:
            comp = r.get("priority_components") or {}
            for f in ("unusualness", "tier_weight", "materiality", "confidence_weight"):
                if f not in comp:
                    failures.append(f"{key}: {r.get('ltcode')} does not record its "
                                    f"{f} component, so its rank cannot be reconstructed")
        if any(sign * (r["z"] or 0) <= 0 for r in rows):
            failures.append(f"{key}: contains a row on the wrong side of zero")
        codes = [r["ltcode"] for r in rows]
        if len(codes) != len(set(codes)):
            failures.append(f"{key}: duplicate line items")
        for r in rows:
            flags = r.get("flags") or []
            if "base_unstable" in flags:
                failures.append(f"{key}: unstable-base row {r['ltcode']} was ranked")
            if "stale" in flags:
                failures.append(
                    f"{key}: {r['ltcode']} has no observation in the scan quarter and "
                    f"cannot describe what changed this quarter")
            if "step_series" in flags:
                failures.append(
                    f"{key}: {r['ltcode']} is a step series, not a continuous measure")
            if r.get("latest_period") and p.get("latest_quarter") and \
                    str(r["latest_period"]).upper() != str(p["latest_quarter"]).upper():
                failures.append(
                    f"{key}: {r['ltcode']} last reports {r['latest_period']} but the scan "
                    f"quarter is {p['latest_quarter']}")
            if r["quarters"] < p["constants"]["min_quarters_to_rank"]:
                failures.append(f"{key}: {r['ltcode']} ranked with too little history")

    acc_codes = {r["ltcode"] for r in p.get("accelerating", [])}
    dec_codes = {r["ltcode"] for r in p.get("decelerating", [])}
    if acc_codes & dec_codes:
        failures.append("a line item appears in both ranked tables")

    _model_wrote = " ".join(
        str(x) for v in (narr_raw or {}).values()
        for x in (v if isinstance(v, (list, tuple)) else [v]))
    _CUR_CHARS = "$£€¥₹₩₽"
    _hit = [c for c in _CUR_CHARS if c in _model_wrote]
    if _hit:
        failures.append(
            "a currency symbol (%s) appears in the model's own prose; figures "
            "reach the page as {{placeholders}} and the engine supplies the "
            "symbol" % ", ".join(_hit))

    flat = re.sub(r"\s+", " ", text)
    for needed in ["Executive summary", "In Growth Pace",
                   "Accelerating", "Decelerating", "Inflection Map",
                   "What Is Driving The Change", "Cross-Metric Signals",
                   "Management Guidance", "What To Monitor Next",
                   "not investment advice", "InSync MCP"]:
        if needed.lower() not in flat.lower():
            failures.append(f"missing required section or disclosure: {needed!r}")
    if not any(t in flat.lower() for t in
               ("sources, guidance and methodology", "provenance and method")):
        failures.append("no sources-and-method section is present under any of its "
                        "titles")

    if args.pdf:
        if not os.path.exists(args.pdf):
            warnings.append("PDF was not produced; the PDF is the only deliverable, so this "
                            "run has nothing to ship until a PDF engine renders it")
        elif os.path.getsize(args.pdf) < 2000:
            failures.append("PDF exists but is implausibly small")
        else:
            pages = pdf_pages(args.pdf)
            _mode = re.search(r"insync-provenance'? content='?\"?(\w+)", doc)
            _budget = args.max_pages + (1 if (_mode and _mode.group(1) == "full") else 0)
            if pages and pages > _budget:
                failures.append(
                    f"the note runs to {pages} pages against a {_budget}-page budget; "
                    f"this is a monitor, not a report")

    cov = p["coverage"]
    if cov["series_ranked"] == 0:
        failures.append("no series cleared the scoring gates; nothing to report")
    if cov["series_ranked"] + cov["series_excluded"] != cov["series_scanned"]:
        failures.append("coverage counts do not reconcile")

    prov_codes = {r.get("ltcode") for r in p.get("provenance") or []}
    displayed = {}
    for key in ("headline_moves", "accelerating", "decelerating", "deep_dive",
                "quiet_headline", "heatmap", "monitor"):
        for r in p.get(key) or []:
            if r.get("ltcode"):
                displayed.setdefault(r["ltcode"], key)
    for r in ((p.get("cash_conversion") or {}).get("rows") or []):
        if r.get("ltcode"):
            displayed.setdefault(r["ltcode"], "cash_conversion")
    for d in p.get("divergences") or []:
        for m in d.get("metrics") or []:
            if m.get("ltcode"):
                displayed.setdefault(m["ltcode"], "divergences")
    gcx = p.get("guidance_check") or {}
    for bucket in ("scored", "other_horizon", "forward_uncompared",
                   "delivered", "delivered_uncompared", "outlook_notes"):
        for g in gcx.get(bucket) or []:
            if g.get("ltcode"):
                displayed.setdefault(g["ltcode"], "guidance_check")
    guide_traced = {str(r.get("ltcode") or "")
                    for r in ((p.get("source_records") or {}).get("guidance_rows") or [])}
    _guide_has_series = {}
    for bucket in ("scored", "other_horizon", "forward_uncompared",
                   "delivered", "delivered_uncompared", "outlook_notes"):
        for g in gcx.get(bucket) or []:
            c = g.get("ltcode")
            if not c:
                continue
            _guide_has_series[c] = _guide_has_series.get(c, False) or bool(
                g.get("in_scan") or g.get("actual") is not None
                or g.get("latest_growth") is not None
                or g.get("matched_level_display"))
    _resolved = {}
    for bucket in ("scored", "other_horizon", "forward_uncompared",
                   "delivered", "delivered_uncompared"):
        for g in gcx.get(bucket) or []:
            if g.get("ltcode") and g.get("resolved_ltcode"):
                _resolved[g["ltcode"]] = g["resolved_ltcode"]

    def _stem(c):
        return re.sub(r"-(?:Y|M|BY|MY|YY)$", "", str(c or ""))
    missing = sorted(c for c in displayed
                     if c not in prov_codes
                     and not (displayed[c] == "guidance_check" and c in guide_traced
                              and (not _guide_has_series.get(c)
                                   or _resolved.get(c) in prov_codes
                                   or (_stem(c) != c and _stem(c) in prov_codes))))
    if missing:
        failures.append(
            f"{len(missing)} displayed metric(s) absent from provenance: "
            + ", ".join(f"{c} (in {displayed[c]})" for c in missing[:8]))

    rd = p.get("ranking_disclosure") or {}
    if rd:
        if not rd.get("weights") or not rd.get("rows"):
            failures.append("ranking_disclosure is present but carries no weights or rows")
        seen_pri = {}
        for key in ("headline_moves", "accelerating", "decelerating",
                    "primary_context", "deep_dive"):
            for r in (p.get(key) or []):
                if r.get("priority") is None:
                    continue
                prev = seen_pri.get(r["ltcode"])
                if prev is not None and abs(prev[0] - r["priority"]) > 1e-9:
                    failures.append(
                        "%s carries priority %.4f in %s and %.4f in %s; one line "
                        "must have one priority everywhere it appears"
                        % (r["ltcode"], prev[0], prev[1], r["priority"], key))
                seen_pri.setdefault(r["ltcode"], (r["priority"], key))
        shown = {c: v[0] for c, v in seen_pri.items()}
        bad, unchecked = [], []
        for code, v in (rd.get("rows") or {}).items():
            terms = v.get("terms") or {}
            if not terms:
                unchecked.append(code)
                continue
            total = sum(float(t.get("weighted") or 0.0) for t in terms.values())
            tol = float(v.get("rounding_tolerance") or 0.0011)
            for label, target in (("disclosure", v.get("stored_priority")),
                                  ("page", shown.get(code))):
                if target is None:
                    continue
                if abs(total - float(target)) > tol:
                    bad.append("%s (%s: terms sum to %.4f, %s carries %.4f)"
                               % (code, label, total, label, float(target)))
        if bad:
            failures.append("published ranking components do not reproduce the "
                            "priority that ordered the page: " + "; ".join(sorted(bad)[:6]))
        if unchecked:
            failures.append("ranking_disclosure rows carry no terms and cannot be "
                            "recomputed: " + ", ".join(sorted(unchecked)[:6]))
    else:
        failures.append("ranking_disclosure missing; the ranking must be recomputable "
                        "from the JSON alone")

    mz = (p.get("constants") or {}).get("material_abs_z", 0.75)
    for key in ("accelerating", "decelerating"):
        for r in p.get(key) or []:
            if r.get("z_display") is not None and abs(r["z_display"]) < mz:
                failures.append(
                    f"{r.get('display') or r.get('name')} sits in the {key} table at "
                    f"|z| {abs(r['z_display']):.2f}, below the {mz} bar; a row that has not "
                    f"cleared the statistical test is primary context, not an inflection")
    cov = p["coverage"]
    if cov.get("material_total") is not None:
        expected = (cov.get("material_accelerating", 0) + cov.get("material_decelerating", 0)
                    + cov.get("material_rate", 0))
        if cov["material_total"] != expected:
            failures.append("material_total does not equal the accelerating plus rate plus "
                            "decelerating counts")
    for r in p.get("primary_context") or []:
        if r.get("z_display") is not None and abs(r["z_display"]) >= mz:
            warnings.append(
                f"{r.get('display') or r.get('name')} is in primary context at |z| "
                f"{abs(r['z_display']):.2f}, at or above the bar; it should rank as an "
                f"inflection")

    bad_units = []
    for key in ("headline_moves", "accelerating", "decelerating", "primary_context",
                "deep_dive"):
        for r in p.get(key) or []:
            st = r.get("unit_status")
            if st is None:
                bad_units.append(f"{r.get('display') or r.get('name')} (no unit recorded)")
            elif st == "unvalidated":
                bad_units.append(f"{r.get('display') or r.get('name')} (unit not validated)")
    if bad_units:
        failures.append("displayed value(s) without a validated unit: "
                        + "; ".join(sorted(set(bad_units))[:6]))
    conflicted = sorted({(r.get("display") or r.get("name"))
                         for key in ("accelerating", "decelerating", "primary_context")
                         for r in (p.get(key) or [])
                         if r.get("unit_status") == "conflicted"})
    if conflicted:
        warnings.append("unit conflict between the line-item name and the source value, "
                        "shown as unresolved on the page: " + ", ".join(conflicted[:4]))

    sr = p.get("sector_routing") or {}
    if not sr.get("template"):
        warnings.append("no sector template resolved; the note uses general rules")
    elif sr.get("overrode_classification") and not sr.get("evidence_detail"):
        failures.append("the sector template overrode the returned classification without "
                        "recording the evidence that justified it")

    if not (p.get("divergences") or []):
        disp = [r for key in ("accelerating", "decelerating")
                for r in (p.get(key) or [])]
        ups = [r for r in disp if (r.get("latest_accel") or 0) > 0 and r.get("tier") == 1]
        dns = [r for r in disp if (r.get("latest_accel") or 0) < 0 and r.get("tier") == 1]
        if ups and dns:
            warnings.append(
                f"no divergence rule fired, yet {len(ups)} primary line(s) accelerated and "
                f"{len(dns)} decelerated. Check whether this sector has a rule for the pair")

    if args.narrative and os.path.exists(args.narrative):
        for k, v in (narr or {}).items():
            if k.startswith("_") or not isinstance(v, str) or not v.strip():
                continue
            resolved = re.sub(r"\{\{([a-zA-Z0-9_.]+)\}\}",
                              lambda m: str((p.get("placeholders") or {}).get(
                                  m.group(1), m.group(0))), v)
            probe = " ".join(resolved.split()[:8])
            if not probe or probe.lower() in " ".join(text.split()).lower():
                continue
            m_dd = re.match(r"^dd(\d+)$", k)
            if m_dd and int(m_dd.group(1)) > len(p.get("deep_dive") or []):
                warnings.append(
                    f"narrative section {k!r} was supplied and not used: the note shows "
                    f"{len(p.get('deep_dive') or [])} deep dives")
                continue
            failures.append(
                f"narrative section {k!r} was supplied and does not appear on the "
                f"page; the contract must not accept prose the renderer discards")

    if narr and isinstance(narr, dict):
        dives = p.get("deep_dive") or []
        BASE_CLAIM = re.compile(
            r"\b(base effect|easy (?:comp|base)|hard (?:comp|base)|"
            r"soft (?:comp|base)|comparison base|prior-year base is doing|"
            r"year-ago (?:comparison|base) (?:rather than|is doing|explains)|"
            r"flattered by the (?:base|comparison)|base-sensitive)\b", re.I)
        for i, row in enumerate(dives[:5], 1):
            text = narr.get(f"dd{i}") or ""
            if BASE_CLAIM.search(text) and not row.get("comp_flag"):
                failures.append(
                    f"narrative dd{i} asserts a base or comparison effect on "
                    f"{row.get('display') or row.get('name')!r}, and the engine "
                    f"recorded comp_flag as null for that line")

        NO_DVG = re.compile(
            r"no (?:cross-metric )?divergence(?: rule)?(?: was)? (?:fired|found)|"
            r"no divergence rule fired|found no pair|no qualifying divergence|"
            r"no pair pulling in opposite", re.I)
        dvg_n = len(p.get("divergences") or [])
        dtext = narr.get("divergence") or ""
        if NO_DVG.search(dtext) and dvg_n:
            failures.append(
                f"narrative 'divergence' says no rule fired while the engine "
                f"recorded {dvg_n}; the page prints both")

        IMP_CLAIM = re.compile(
            r"implication (?:reads|is)\s+(favourable|unfavourable|mixed|ambiguous)",
            re.I)
        for i, row in enumerate(dives[:5], 1):
            for m in IMP_CLAIM.finditer(narr.get(f"dd{i}") or ""):
                said, actual = m.group(1).lower(), str(row.get("implication") or "").lower()
                if said != actual:
                    failures.append(
                        f"narrative dd{i} says the implication reads {said!r} while "
                        f"{row.get('display') or row.get('name')!r} carries "
                        f"{actual!r} on the page")

        g = p.get("guidance_check") or {}
        has_guide = bool(g.get("scored") or g.get("other_horizon"))
        gtext = narr.get("guidance") or ""
        if re.search(r"\bno (?:management )?guidance\b|nothing to score against|"
                     r"no next-quarter guide", gtext, re.I) and has_guide:
            failures.append(
                "narrative 'guidance' reports no guidance while the engine scored "
                f"{len(g.get('scored') or [])} guide row(s)")

        RESTATES_NO_GUIDE = re.compile(
            r"no (?:management |company )?guidance\b|no (?:next[- ]quarter |forward )?"
            r"guide\b|nothing to score against|no outlook (?:was )?(?:returned|"
            r"issued|given)|guidance (?:was )?not (?:returned|issued|given)", re.I)
        RESTATES_NO_DVG = re.compile(
            r"no (?:cross-metric )?divergence|no (?:qualifying )?(?:rule|pair)\b|"
            r"found no pair|no pair pulling", re.I)
        vis = " ".join(visible_text(doc).split())
        pairs = [
            ("guidance", "No management guidance targeting", gtext,
             RESTATES_NO_GUIDE, "that no guide was returned"),
            ("divergence", "No qualifying divergence between", dtext,
             RESTATES_NO_DVG, "that no divergence rule fired"),
        ]
        for key, probe, prose_text, restates, what in pairs:
            if not prose_text.strip() or probe.lower() not in vis.lower():
                continue
            if restates.search(prose_text):
                failures.append(
                    f"narrative {key!r} restates {what}, which the engine's own "
                    f"note directly above it has already said; the paragraph must "
                    f"say what stands in for the missing test, not repeat that it "
                    f"is missing")

    disp_all = {}
    for key in ("headline_moves", "accelerating", "decelerating",
                "primary_context", "deep_dive"):
        for r in (p.get(key) or []):
            disp_all.setdefault(r["ltcode"], []).append((r, key))
    disp_rows = {c: v[0] for c, v in disp_all.items()}

    MODELLED = re.compile(r"sell[- ]?side|model source|consensus|estimate|broker", re.I)
    modelled_shown = sorted(
        {r.get("display") or r.get("name") or r.get("ltcode")
         for key in ("headline_moves", "accelerating", "decelerating",
                     "primary_context", "deep_dive")
         for r in (p.get(key) or [])
         if any(MODELLED.search(s or "") for s in (r.get("source_mix") or {}))
         or r.get("provenance") == "modelled"})
    if modelled_shown:
        failures.append(
            "third-party modelled data is displayed as a reported actual: "
            + ", ".join(modelled_shown[:6])
            + ". An acceleration computed on modelled data measures how analysts "
              "changed their models, not what the business did")

    conflicted = [(c, copies[0][0]) for c, copies in disp_all.items()
                  if any(r.get("unit_status") == "conflicted" for r, _k in copies)]
    if conflicted:
        withheld_n = doc.count("level withheld, unit unresolved")
        if withheld_n < len(conflicted):
            failures.append(
                "%d displayed row(s) carry a conflicted unit and the page shows only "
                "%d withheld marker(s): %s. A level whose unit the engine could not "
                "resolve must be withheld, not printed in whichever unit won"
                % (len(conflicted), withheld_n,
                   ", ".join((r.get("display") or c) for c, r in conflicted[:4])))

    dive_codes = {r.get("ltcode") for r in (p.get("deep_dive") or [])}
    mon = p.get("monitor") or []
    seen_topup = False
    for r in mon:
        if r.get("ltcode") in dive_codes:
            if seen_topup:
                failures.append(
                    "monitor lists the top-up line %r before the written line %r. "
                    "The table exists so that what the note analysed is what it "
                    "tells you to watch"
                    % (mon[0].get("variable"), r.get("variable")))
                break
        else:
            seen_topup = True

    for key in ("deep_dive", "monitor"):
        rows = p.get(key) or []
        if key == "monitor":
            rows = [r for r in rows if r.get("ltcode") in dive_codes] \
                   + [r for r in rows if r.get("ltcode") not in dive_codes]
        pri = []
        for r in rows:
            q = r.get("slot_priority")
            if q is None:
                q = r.get("priority")
            if q is None and r.get("ltcode") in disp_rows:
                src = disp_rows[r["ltcode"]][0]
                q = src.get("slot_priority", src.get("priority"))
            if q is not None:
                pri.append((r.get("display") or r.get("variable") or r.get("ltcode"), q))
        if key == "monitor":
            n_dive = sum(1 for r in rows if r.get("ltcode") in dive_codes)
            blocks = [pri[:n_dive], pri[n_dive:]]
        else:
            blocks = [pri]
        bad = any(len(b) > 1 and any(b[i][1] < b[i + 1][1] - 1e-9
                                     for i in range(len(b) - 1)) for b in blocks)
        if bad:
            failures.append(
                "%s is not ordered by the published priority: %s. The lines that get "
                "a paragraph are the ones a decision turns on, not the ones that "
                "moved most strangely" % (key, " then ".join("%s %.3f" % x for x in pri)))

    tally = p.get("implication_counts") or {}
    bal = str(p.get("implication_balance") or "")
    if tally and bal:
        fav, unf = tally.get("Favourable", 0), tally.get("Unfavourable", 0)
        low = bal.lower()
        if low.startswith("unfavourable") and fav > unf:
            failures.append("implication_balance reads unfavourable while favourable "
                            "rows (%d) outnumber unfavourable (%d)" % (fav, unf))
        elif low.startswith("favourable") and unf > fav:
            failures.append("implication_balance reads favourable while unfavourable "
                            "rows (%d) outnumber favourable (%d)" % (unf, fav))
        elif low.startswith(("favourable", "unfavourable")) and fav == 0 and unf == 0:
            failures.append("implication_balance takes a side with no favourable or "
                            "unfavourable rows to take it from")

    twins, codes = [], sorted(disp_rows)
    for i, a_code in enumerate(codes):
        a = disp_rows[a_code][0].get("value_series") or []
        for b_code in codes[i + 1:]:
            b = disp_rows[b_code][0].get("value_series") or []
            pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
            if len(pairs) < 8 or len(pairs) < min(len(a), len(b)):
                continue
            if all(abs(x - y) <= 0.0015 * max(abs(x), abs(y), 1e-9) for x, y in pairs):
                twins.append("%s and %s" % (
                    disp_rows[a_code][0].get("display") or a_code,
                    disp_rows[b_code][0].get("display") or b_code))
    if twins:
        failures.append(
            "two displayed rows are the same measurement to within a tenth of a "
            "percent on every shared quarter: " + "; ".join(twins[:4])
            + ". One concept must occupy one slot")

    retrieved = p.get("retrieved_codes")
    if retrieved:
        accounted = set(p.get("scored_codes") or []) | set(p.get("ranked_codes") or [])
        accounted |= {e["ltcode"] for e in (p.get("excluded") or [])}
        accounted |= {r["ltcode"] for key in
                      ("headline_moves", "accelerating", "decelerating",
                       "primary_context", "deep_dive", "quiet_headline", "provenance")
                      for r in (p.get(key) or [])}
        lost = sorted(set(retrieved) - accounted)
        if lost:
            failures.append(
                "%d code(s) were retrieved by the scan and appear in no table and "
                "no exclusion: %s. A line cannot leave the universe silently"
                % (len(lost), ", ".join(lost[:8])))

    for r in (p.get("deep_dive") or []):
        lab = r.get("unit_label")
        if (not lab or lab in ("currency", "%", "count")
                or r.get("unit_status") == "conflicted"):
            continue
        fb = r.get("forward_bar") or {}
        if fb.get("level_to_hold_growth") is None:
            continue
        disp, div = r.get("display_value"), (r.get("unit_divisor") or 1.0)
        want = fb["level_to_hold_growth"] / div
        if disp and want and not (0.1 <= abs(want / disp) <= 10):
            failures.append(
                "%s shows a level of %.2f %s and a continuation threshold of %.2f "
                "in the same unit; a threshold an order of magnitude from its own "
                "level is a scale error, not a forecast"
                % (r.get("display") or r["ltcode"], disp, lab, want))
        if lab not in visible_text(doc):
            failures.append(
                "%s is displayed in %r and that unit never appears on the page"
                % (r.get("display") or r["ltcode"], lab))

    STOP = {"the", "a", "an", "its", "this", "that", "and", "of", "in", "on",
            "for", "total", "net", "consolidated", "adjusted", "per", "share"}
    BASIS_STRIP = re.compile(r"\([^)]*\)|%|\bcents?\b|\bbps\b|\busd\b|\bpp\b", re.I)

    def _content(text):
        text = BASIS_STRIP.sub(" ", (text or "").lower())
        return {w for w in re.findall(r"[a-z]{3,}", text) if w not in STOP}

    def _subject(text):
        first = re.split(r"(?<=[.!?])\s+", (text or "").strip())[0]
        first = re.split(r",|\bwhile\b|\bbut\b|\bso\b", first, maxsplit=1)[0]
        cut = re.split(r"\b(is|are|was|were|has|have|had|did|does|do|moved|"
                       r"went|came|ran|rose|fell|held|stepped|picked|slipped|"
                       r"deteriorat\w*|improv\w*|widen\w*|narrow\w*)\b",
                       first, maxsplit=1, flags=re.I)[0]
        return " ".join(re.findall(r"[\w'-]+", cut)[:9])

    dd = p.get("deep_dive") or []
    if len(dd) > 1:
        named = [(r, _content(r.get("display") or r.get("name") or ""))
                 for r in (v[0][0] for v in disp_all.values())]
        for i, row in enumerate(dd[:5], 1):
            subj = _content(_subject(narr.get(f"dd{i}") or ""))
            if not subj:
                continue
            own = _content(row.get("display") or row.get("name") or "")
            own_hit = len(own & subj)
            best, best_n = None, 0
            for other, toks in named:
                if other.get("ltcode") == row.get("ltcode"):
                    continue
                if len(toks) >= 2 and toks <= subj and len(toks) > best_n:
                    best, best_n = other, len(toks)
            whole = _content(narr.get(f"dd{i}") or "")
            own = required_binding_words(row) or own
            whole = whole | set(re.findall(r"[a-z][a-z0-9&]+", (narr.get(f"dd{i}") or "").lower()))
            _named_by_token = bool(
                re.search(r"\{\{\s*dd%d\.name\s*\}\}" % i,
                          str((narr_raw or {}).get(f"dd{i}") or "")))
            if own and not (own & whole) and not _named_by_token:
                failures.append(
                    "narrative dd%d never refers to %r anywhere in the paragraph; "
                    "a paragraph that never touches the line it sits under is "
                    "unbound - the reader joins it up and no check can. One shared "
                    "word satisfies this, in any sentence, so it does not ask for "
                    "the label back in the opening"
                    % (i, row.get("display") or row.get("name")))
            if best is not None and best_n > own_hit:
                failures.append(
                    "narrative dd%d opens on %r, which names the published row %r "
                    "more specifically than the row it sits under (%r). The "
                    "paragraph is written against the wrong line"
                    % (i, _subject(narr.get(f"dd{i}") or "")[:56],
                       best.get("display") or best.get("name"),
                       row.get("display") or row.get("name")))

    def _yoy(r):
        g = r.get("latest_growth")
        if g is None:
            return "no year-over-year rate"
        return "%+.1f %s" % (g, "bps" if (r.get("unit") == "bps") else "pp"
                             if r.get("unit") == "pp" else "%")

    LEVEL_FALL = re.compile(
        r"\b(fell|falling|declin\w+|drop\w+|shrank|shrunk|shrink\w*|contract\w+|"
        r"went backwards?|lower than|below (where|what)|down from|reduced|lost ground|"
        r"came off|slipp?ed|receded|worse\w*|weaken\w+|eroded|compress\w+)\b", re.I)
    LEVEL_RISE = re.compile(
        r"\b(rose|rising|grew|growing|climb\w+|increas\w+|expand\w+|"
        r"higher than|above (where|what)|up from|gained|advanced|"
        r"improv\w+|better|strengthen\w*)\b", re.I)
    PACE_ONLY = re.compile(
        r"\b(accelerat\w+|decelerat\w+|pace|momentum|"
        r"rate of (change|growth|decline|increase|improvement)|"
        r"(growth|decline|increase|improvement) rate|"
        r"growth rate|slow(ed|ing|s|down)?|quicken\w*|gathered pace|stepped up|"
        r"picked up)\b", re.I)
    PACE_SUBJECT = re.compile(
        r"\b(growth|pace|momentum|rate|acceleration|expansion)\b"
        r"(?:\s+\w+){0,2}\s*$", re.I)

    def _about_the_rate(sent, match):
        return bool(PACE_SUBJECT.search(sent[:match.start()]))

    PAST_MARKER = re.compile(
        r"\b(had been|has been|have been|previously|until|quarters of|"
        r"last year|a year ago|earlier|prior)\b", re.I)
    PRONOUN_LEAD = re.compile(
        r"^\s*(it|they|this|that|these|those|the line|the ratio|the metric|"
        r"the same)\b", re.I)

    def _about_this_row(sent, match, own_row):
        clause = re.split(r",|\bwhile\b|\bbut\b|\band\b|\bso\b",
                          sent[:match.end()])[-1]
        if PRONOUN_LEAD.match(clause):
            return True
        own = _content(own_row.get("display") or own_row.get("name") or "")
        return bool(own & _content(clause))

    def _exempt(sent, match, row):
        return (_about_the_rate(sent, match)
                or not _about_this_row(sent, match, row)
                or bool(PAST_MARKER.search(sent[:match.start()])))

    for i, row in enumerate((p.get("deep_dive") or [])[:5], 1):
        text = narr.get(f"dd{i}") or ""
        if not text.strip():
            continue
        lvl = row.get("level_direction")
        mom = row.get("momentum_direction")
        name = row.get("display") or row.get("name")
        GOODNESS = re.compile(r"\b(improv\w+|better|worse\w*|deteriorat\w+|"
                              r"strengthen\w*|weaken\w+)\b", re.I)
        for sent in re.split(r"(?<=[.!?])\s+", text):
            probe = GOODNESS.sub(" ", sent) if row.get("lower_is_better") else sent
            m_fall = LEVEL_FALL.search(probe)
            m_rise = LEVEL_RISE.search(probe)
            if lvl == "up" and m_fall and not _exempt(probe, m_fall, row):
                failures.append(
                    "narrative dd%d says %s went down while its level went UP "
                    "(%s year over year): %r. Only the rate of change fell; a "
                    "reader takes this as a fall in the line itself"
                    % (i, name, _yoy(row), sent.strip()[:100]))
                break
            if lvl == "down" and m_rise and not _exempt(probe, m_rise, row):
                failures.append(
                    "narrative dd%d says %s went up while its level went DOWN "
                    "(%s year over year): %r"
                    % (i, name, _yoy(row), sent.strip()[:100]))
                break
        if row.get("level_vs_momentum") == "diverging":
            said_level = bool(LEVEL_FALL.search(text) or LEVEL_RISE.search(text)
                              or re.search(r"\b(level|still (up|down|grow\w+)|"
                                           r"absolute|outright)\b", text, re.I))
            said_pace = bool(PACE_ONLY.search(text))
            if not (said_level and said_pace):
                missing = "the level direction" if not said_level else "the momentum direction"
                failures.append(
                    "narrative dd%d omits %s on %s, whose level and rate point "
                    "OPPOSITE ways (level %s, rate %s). One sentence about \"the "
                    "move\" is ambiguous here whichever words it uses; say both"
                    % (i, missing, name, lvl, mom))

    fams = {}
    for r in (p.get("deep_dive") or []):
        f = r.get("family")
        if f:
            fams.setdefault(f, []).append(r.get("display") or r.get("name") or r["ltcode"])
    dupes = {f: v for f, v in fams.items() if len(v) > 1}
    if dupes:
        failures.append(
            "two dive paragraphs describe the same concept family: "
            + "; ".join("%s (%s)" % (f, ", ".join(v)) for f, v in sorted(dupes.items()))
            + ". One written paragraph per concept; a short section is the honest "
              "output when the quarter produced fewer findings")

    if args.pdf and os.path.exists(args.pdf):
        labels = [re.sub(r"\s+", " ", m).strip() for m in
                  re.findall(r'<div class="label">(.*?)</div>', doc, re.S)]
        labels = [re.sub(r"<[^>]+>", "", l) for l in labels]
        heads = []
        for tm in re.finditer(r"<thead>(.*?)</thead>", doc, re.S):
            cells = [re.sub(r"<[^>]+>", "", c).strip()
                     for c in re.findall(r"<th[^>]*>(.*?)</th>", tm.group(1), re.S)]
            cells = [re.sub(r"\s+", " ", c) for c in cells if c.strip()]
            if len(cells) >= 3:
                heads.append(cells)
        pages = pdf_pages_text(args.pdf)

        def _lines(txt):
            return [re.sub(r"\s+", " ", l).strip() for l in txt.splitlines()
                    if l.strip()]

        def _key(x):
            return re.sub(r"\s+", "", x).lower()

        def _is_heading(x):
            return not x.rstrip().endswith((".", ",", ";", ":"))

        seen_label_pages = {}
        for pi, ptxt in enumerate(pages, 1):
            ls = _lines(ptxt)
            if not ls:
                continue
            flat = " ".join(ls).lower()
            for l in labels:
                if (_is_heading(ls[-1]) and _key(ls[-1]) == _key(l)
                        and pi < len(pages)):
                    failures.append(
                        "page %d ends on the heading %r with its content on the "
                        "next page. A heading separated from its section reads as "
                        "a section with nothing in it" % (pi, l))
            lowset = {_key(x) for x in ls if _is_heading(x)}
            for l in labels:
                if _key(l) in lowset:
                    seen_label_pages.setdefault(l, []).append(pi)
            if pi > 1:
                lows = [x.lower().rstrip(".") for x in ls]
                lkeys = {_key(l) for l in labels}
                label_at = next((k for k, x in enumerate(ls)
                                 if _is_heading(x) and _key(x) in lkeys), None)
                for cells in heads:
                    want = [c.lower() for c in cells]
                    hdr_at = next(
                        (k for k in range(min(len(lows), 6))
                         if sum(1 for c in want if c in lows[k]) >= max(3, len(want) - 1)),
                        None)
                    if hdr_at is None:
                        continue
                    if label_at is not None and label_at < hdr_at:
                        continue
                    prev = {_key(x) for x in _lines(pages[pi - 2]) if _is_heading(x)}
                    owning = [l for l in labels if _key(l) in prev]
                    failures.append(
                        "page %d opens on the column headers %r with no heading "
                        "above them%s. A table split from its heading has lost the "
                        "only thing that says what it is"
                        % (pi, " / ".join(cells[:4]),
                           " (heading %r is on page %d)" % (owning[-1], pi - 1)
                           if owning else ""))
                    break
        for l, ps in seen_label_pages.items():
            if len(set(ps)) > 1:
                failures.append(
                    "heading %r appears on pages %s. One heading, one place"
                    % (l, ", ".join(str(x) for x in sorted(set(ps)))))
        analytic = {_key(l) for l in labels if _key(l) not in REFERENCE_LABELS}
        for pi, ptxt in enumerate(pages, 1):
            if pi <= 3:
                continue
            spill = [x for x in _lines(ptxt) if _key(x) in analytic]
            if spill:
                failures.append(
                    "analytical section %r begins on page %d. Pages one to three "
                    "carry the analysis and page four carries provenance and "
                    "method; a fourth page of analysis is a report"
                    % (spill[0], pi))
                break

        last = _lines(pages[-1]) if pages else []
        for j, l in enumerate(last):
            if re.search(r"\bgenerated\b.*\d{4}-\d{2}-\d{2}", l, re.I):
                after = [x for x in last[j + 1:] if len(x) > 3]
                if after:
                    failures.append(
                        "content appears after the generation stamp: %r. The stamp "
                        "closes the document" % after[0][:80])
                break

    for tm in re.finditer(r"<table.*?</table>", doc, re.S):
        t = tm.group(0)
        body = re.findall(r"<tbody>(.*?)</tbody>", t, re.S)
        if body and not re.search(r"<tr", body[0]):
            hdr = [re.sub(r"<[^>]+>", "", c).strip()
                   for c in re.findall(r"<th[^>]*>(.*?)</th>", t, re.S)]
            failures.append(
                "a table prints the header %r with no rows beneath it. An empty "
                "table is a label, and a label for nothing is worse than silence "
                "- say the panel returned nothing in the note instead"
                % " / ".join(x for x in hdr[:4] if x))

    _precision_text = visible_text(doc)
    for _x in (p.get("excluded") or []):
        if _x.get("unparsed") and _x.get("reason"):
            _precision_text = _precision_text.replace(str(_x["reason"]), " ")
    seen_dp = {}
    for m in re.finditer(r"([-+]?\d[\d,]*(?:\.(\d+))?)\s*"
                         r"(%|pp|bps|cents|USD/[A-Za-z]+|[A-Z][A-Za-z]*/d|days)\b",
                         _precision_text):
        seen_dp.setdefault(m.group(3), {}).setdefault(len(m.group(2) or ""), m.group(0))
    for unit, dps in sorted(seen_dp.items()):
        if len(dps) > 1:
            failures.append(
                "%r is printed at more than one precision on the page: %s. One unit, "
                "one number of decimals; the same quantity shown two ways reads as "
                "two figures" % (unit, ", ".join(repr(v) for v in dps.values())))

    _pm = re.search(r"insync-provenance'? content='?\"?(\w+)", doc)
    if (_pm.group(1) if _pm else "standard") != "full":
        leaked = sorted(set(re.findall(
            r"\b(?:IS|BS|CF|KM|SD)_[PS]_\d+(?:-M)?\b|\bI_\d+-\d+(?:-M)?\b",
            visible_text(doc))))
        if leaked:
            failures.append(
                "%d internal identifier(s) appear on the page: %s. Source codes "
                "belong in the JSON audit trail, not in a note a portfolio "
                "manager reads; where a code names an excluded or unretrieved "
                "line, print the line's name"
                % (len(leaked), ", ".join(leaked[:6])))

    _KNOWN = {"sec fillings", "sec filings", "other filings", "other filing",
              "financial release", "presentations", "calculated",
              "sell-side/other model sources", "document class not returned"}
    for r in (p.get("provenance") or []):
        for k in (r.get("source_mix") or {}):
            raw = str(k).strip().lower()
            if raw not in _KNOWN:
                warnings.append(
                    "provider source label %r has no print form and is shown raw; "
                    "add it to SOURCE_LABEL" % str(k))
                break

    NARR_BLOCKS = ("setup", "dd1", "dd2", "dd3", "dd4", "dd5",
                   "divergence", "guidance", "watch")
    for key in NARR_BLOCKS:
        raw = narr_raw.get(key)
        if raw is None:
            continue
        for j, txt in enumerate((raw if isinstance(raw, list) else [raw]), 1):
            if not isinstance(txt, str) or not txt.strip():
                continue
            if not re.search(r"\{\{[^}]+\}\}", txt):
                where = key if not isinstance(raw, list) else "%s[%d]" % (key, j)
                failures.append(
                    "narrative %s states a change with no figure: %r. Every "
                    "observation carries its own evidence - the level, the "
                    "comparison and the change - as {{placeholders}} the engine "
                    "substitutes" % (where, txt.strip()[:90]))

    INTERPRET = [
        (r"\bdid the work\b", "did the work"),
        (r"\bcarried the quarter\b", "carried the quarter"),
        (r"\bis the (story|finding|call|answer|point)\b", "nominates the finding"),
        (r"\bthe real question\b", "the real question"),
        (r"\bthe debate\b", "the debate"),
        (r"\bwhat (the|this) .{0,24}\btests?\b", "declares what a print tests"),
        (r"\bis the bridge\b", "is the bridge"),
        (r"\b(investors|the market|the street) (will|would|should|are)\b",
         "speaks for investors"),
        (r"\blooked past\b", "looked past"),
        (r"\btower(ed|s)? over\b", "towered over"),
        (r"\bedged past\b", "edged past"),
        (r"\bcosmetic\b", "cosmetic"),
        (r"\bbinary\b", "binary"),
        (r"\bwhat matters (most|here|now)\b", "what matters most"),
        (r"\bthe question is\b", "the question is"),
    ]
    INTERPRET = [(re.compile(a, re.I), b) for a, b in INTERPRET]
    for key in NARR_BLOCKS:
        raw = narr_raw.get(key)
        if raw is None:
            continue
        for txt in (raw if isinstance(raw, list) else [raw]):
            if not isinstance(txt, str):
                continue
            for rx, label in INTERPRET:
                m = rx.search(txt)
                if m:
                    failures.append(
                        "narrative %s interprets rather than reports (%s): %r. "
                        "State the move, the figure and the arithmetic link; the "
                        "reader decides which finding matters"
                        % (key, label, m.group(0)))
                    break

    dd_texts = [(k, narr_raw.get(k)) for k in ("dd1", "dd2", "dd3", "dd4", "dd5")
                if isinstance(narr_raw.get(k), str) and narr_raw.get(k).strip()]
    if len(dd_texts) >= 2:
        SCAFFOLD = ("year over year", "a year ago", "last quarter", "of this line",
                    "against a base", "the level repeating", "consecutive quarter",
                    "not disclosed", "nothing disclosed")

        def _shingles(txt):
            txt = re.sub(r"\{\{([a-zA-Z0-9_.]+)\}\}",
                         lambda m: str((p.get("placeholders") or {}).get(
                             m.group(1), " ")), txt)
            w = re.findall(r"[a-z][a-z'-]+", txt.lower())
            sh = {" ".join(w[i:i + 5]) for i in range(len(w) - 4)}
            return {x for x in sh if not any(f in x for f in SCAFFOLD)}
        worst = 0
        pair = None
        for i, (ka, va) in enumerate(dd_texts):
            for kb, vb in dd_texts[i + 1:]:
                shared = _shingles(va) & _shingles(vb)
                if len(shared) > worst:
                    worst, pair = len(shared), (ka, kb, sorted(shared)[:2])
        if worst >= 3:
            warnings.append(
                "dive paragraphs %s and %s share %d five-word phrases (%s); the "
                "numbers differ but the sentence does not. Vary the construction "
                "so the section does not read as one template filled in"
                % (pair[0], pair[1], worst, "; ".join(repr(x) for x in pair[2])))

    def _recon(label, lvl, prior, growth, unit):
        if None in (lvl, prior, growth) or not prior:
            return
        if unit == "bps":
            want = (lvl - prior) * 100.0
            tol = max(2.0, abs(want) * 0.02)
        else:
            want = (lvl / prior - 1.0) * 100.0
            tol = max(0.2, abs(want) * 0.02)
        if abs(want - growth) > tol:
            failures.append(
                "%s does not reconcile: level %.4f against a year-ago %.4f implies "
                "%+.1f, and the page publishes %+.1f. A level, its comparison and "
                "its change must be on one basis"
                % (label, lvl, prior, want, growth))

    for r in ((p.get("cash_conversion") or {}).get("rows") or []):
        _recon("cash panel row %r" % (r.get("display") or r.get("label")),
               r.get("level"), r.get("prior_level"), r.get("latest_growth"),
               r.get("unit"))
    for i, r in enumerate((p.get("deep_dive") or [])[:5], 1):
        if r.get("unit_status") == "conflicted" or r.get("flow_rate_mode"):
            continue
        if r.get("kind") == "pre_growth":
            continue
        _recon("dd%d %r" % (i, r.get("display") or r.get("name")),
               r.get("display_value"), r.get("prior_year_value"),
               r.get("latest_growth"), r.get("unit"))

    _tmpl = (p.get("sector_routing") or {}).get("template")
    for d in (p.get("divergences") or []):
        if not d.get("owner_template"):
            failures.append(
                "divergence rule %r published with no owning template. Every "
                "rule is either owned by named templates or declared universal; "
                "neither is the same as silence" % (d.get("rule"),))
    if _tmpl:
        for d in (p.get("divergences") or []):
            own = d.get("owner_template")
            if own and "*" not in own and _tmpl not in own:
                failures.append(
                    "divergence rule %r belongs to the %s template and fired on a "
                    "note routed to %r: %r. A sector rule matching outside its "
                    "sector is a pattern leak, not a finding"
                    % (d.get("rule"), " or ".join(own), _tmpl, d.get("headline")))

    led = p.get("implication_ledger")
    if led:
        cov = p.get("coverage") or {}
        mat = [x for x in led if x.get("population") == "material"]
        ctx = [x for x in led if x.get("population") == "primary_context"]
        if cov.get("material_total") is not None and len(mat) != cov["material_total"]:
            failures.append(
                "the implication ledger lists %d material lines and the page reports "
                "%d. A reader checking the count must arrive at the published one"
                % (len(mat), cov["material_total"]))
        if cov.get("primary_context") is not None and len(ctx) != cov["primary_context"]:
            failures.append(
                "the implication ledger lists %d primary-context lines and the page "
                "reports %d" % (len(ctx), cov["primary_context"]))
        tally = collections.Counter(x.get("implication") for x in mat)
        for k, v in (p.get("implication_counts") or {}).items():
            if v and tally.get(k, 0) != v:
                failures.append(
                    "the ledger counts %d %s material line(s) and the summary reports "
                    "%d" % (tally.get(k, 0), k, v))

    for r in (p.get("guidance_check") or {}).get("scored") or []:
        imp, now, v = (r.get("guide_implied_growth"), r.get("latest_growth"),
                       r.get("verdict"))
        if imp is None or now is None or not v:
            continue
        nm = r.get("display") or r.get("ltcode")
        if v.startswith("continues") and (imp < 0) != (now < 0):
            failures.append(
                "guidance verdict for %s reads %r while the implied rate (%.1f) and "
                "the reported rate (%.1f) have opposite signs; a sign change is a "
                "reversal, not a continuation" % (nm, v, imp, now))
        if v == "reverses to growth" and not (now < 0 <= imp):
            failures.append(
                "guidance verdict for %s reads 'reverses to growth' but the reported "
                "rate is %.1f and the implied rate %.1f" % (nm, now, imp))
        if v == "turns negative" and imp >= 0:
            failures.append(
                "guidance verdict for %s reads 'turns negative' on an implied rate of "
                "%.1f" % (nm, imp))
        if v == "continues acceleration" and imp < now:
            failures.append(
                "guidance verdict for %s reads 'continues acceleration' on an implied "
                "rate (%.1f) below the reported one (%.1f)" % (nm, imp, now))

    for r in (p.get("guidance_check") or {}).get("delivered") or []:
        lo, hi, act, pos = (r.get("guide_low"), r.get("guide_high"),
                            r.get("actual"), r.get("position"))
        if None in (lo, hi, act) or not pos:
            continue
        nm = r.get("display") or r.get("ltcode")
        tol = float(r.get("precision_tolerance") or 0.0)
        if tol and lo != hi:
            failures.append("delivered guidance row for %s applies a precision "
                            "tolerance to a range guide" % nm)
        _a, _l, _h = ((-act, -hi, -lo) if r.get("compare_on_magnitude") else (act, lo, hi))
        want = "above" if _a > _h + tol else ("below" if _a < _l - tol else "within")
        if pos != want:
            failures.append(
                "delivered guidance row for %s says %r but %s against a %s to %s guide "
                "is %r" % (nm, pos, act, lo, hi, want))
        gap = r.get("gap")
        if gap is not None:
            exp = (act - hi if want == "above" else (act - lo if want == "below" else 0.0))
            if abs(gap - exp) > 1e-6:
                failures.append(
                    "delivered guidance row for %s reports a gap of %s against a "
                    "computed %s" % (nm, gap, exp))

    for n in (p.get("guidance_check") or {}).get("outlook_notes") or []:
        if n.get("note_value") is None:
            continue
        txt = str(n.get("text") or "")
        v = n["note_value"]
        forms = {("%g" % v), ("%.2f" % v), ("%.1f" % v), str(v)}
        if not any(f in txt for f in forms):
            failures.append(
                "the figure read from a guidance note (%s) does not appear in the note "
                "text it is attributed to: %r" % (v, txt[:60]))

    _disclosed = "held out of the displayed tables" in page_flat
    _cap = (p.get("constants") or {}).get("rows_per_ranked_table", 5)
    for key, label in (("accelerating", "Accelerating"),
                       ("decelerating", "Decelerating")):
        have = (p.get(key) or [])[:_cap]
        if not have or _disclosed:
            continue
        for r in have:
            nm = re.sub(r"\s+", " ", str(r.get("display") or r.get("name") or "")).lower()
            if nm and nm not in page_flat:
                failures.append(
                    "%s row %r does not reach the page and the page does not say any "
                    "row was held out" % (label, r.get("display") or r.get("name")))

    FELL_READS = {"fell while revenue grew", "fell faster than revenue",
                  "fell while revenue also fell"}
    GREW_READS = {"outgrowing revenue", "growing faster than revenue", "broadly tracking revenue",
                  "growing more slowly than revenue", "longer than a year ago"}
    FELL_READS = FELL_READS | {"shorter than a year ago"}
    cc = p.get("cash_conversion") or {}
    for r in (cc.get("rows") or []):
        g, st = r.get("latest_growth"), str(r.get("status") or "")
        if g is None or not st or st == "reported":
            continue
        nm = r.get("label") or r.get("ltcode")
        if st not in FELL_READS | GREW_READS:
            failures.append("cash panel read for %s is not one of the engine's own "
                            "reads: %r" % (nm, st))
        elif g < 0 and st in GREW_READS:
            failures.append(
                "cash panel read for %s says %r on a growth rate of %+.1f%%; a line "
                "that fell did not grow more slowly" % (nm, st, g))
        elif g >= 0 and st in FELL_READS:
            failures.append(
                "cash panel read for %s says %r on a growth rate of %+.1f%%"
                % (nm, st, g))
    concl = str(cc.get("conclusion") or "")
    if concl:
        _neg = [r for r in (cc.get("rows") or [])
                if r.get("label") in ("Operating Cash Flow", "Free Cash Flow")
                and (r.get("latest_growth") or 0) < 0]
        if _neg and re.search(r"cash generation grew more slowly", concl, re.I):
            failures.append(
                "the cash panel concludes that cash generation 'grew more slowly' while "
                "%d of its cash lines fell outright" % len(_neg))

    _clientmode = (prov_print_mode != "full")
    if _clientmode:
        low = page_flat
        _gc = p.get("guidance_check") or {}
        _rk_codes = {r.get("ltcode")
                     for k in ("headline_moves", "accelerating", "decelerating",
                               "deep_dive", "monitor", "primary_context",
                               "quiet_headline")
                     for r in (p.get(k) or [])}
        _has_delivered = bool([r for r in (_gc.get("delivered") or [])
                               if r.get("ltcode") in _rk_codes]
                              or [n for n in (_gc.get("outlook_notes") or [])
                                  if n.get("matched_gap_display")])
        _has_forward = bool((_gc.get("scored") or [])
                            or (_gc.get("forward_uncompared") or [])
                            or [n for n in (_gc.get("outlook_notes") or [])
                                if n.get("targets_next") and n.get("note_value_display")])
        for needed, label, required in (
                ("sources, guidance and methodology", "the reference section title", True),
                ("figures are", "the key-source table", bool(p.get("provenance"))),
                ("reported against guidance", "the delivered-versus-guidance table",
                 _has_delivered),
                ("company outlook", "the forward outlook table", _has_forward),
                ("methodology", "the methodology box", True),
                ("limitations", "the limitations paragraph", True)):
            if required and needed not in low:
                failures.append("the client reference page is missing %s" % label)
        for th, label in (("shown in", "a 'shown in' column"),
                          ("parsed midpoint", "a parsed-midpoint column"),
                          ("ltcode", "an internal-code column"),
                          ("identifier", "a document-identifier column")):
            if re.search(r"(?is)<th[^>]*>\s*" + re.escape(th) + r"\s*</th>", doc):
                failures.append("the client PDF still carries %s" % label)
        for banned, label in (
                ("identifier not returned", "a document row with no identifier"),
                ("guidance rows as returned", "the raw guidance-rows table"),
                ("full audit trail", "audit-trail language"),
                ("bundled engine", "implementation language"),
                ("no comparison attempted", "a parse-status diagnostic"),
                ("step series, not a continuous measure",
                 "a held-out implementation reason"),
                ("derived twin", "a held-out implementation reason")):
            if banned in low:
                failures.append("the client PDF still carries %s (%r)" % (label, banned))
        if low.count("guided for") > 1:
            failures.append("a guidance table appears more than once; the delivered "
                            "figures should print on the reference page only")

    st = p.get("sector_tables") or {}
    if not st:
        warnings.append("the payload carries no sector_tables block; the template "
                        "consistency check could not run")
    else:
        _labels = set(st.get("labels") or {})
        _pri = set(st.get("has_ranking_rules") or [])
        _ev = set(st.get("has_routing_evidence") or [])
        _only = set(st.get("declared_label_only") or {})
        for rule, owners in (st.get("rule_owners") or {}).items():
            for o in owners:
                if o not in _labels:
                    failures.append(
                        "divergence rule %r is owned by template %r, which does not "
                        "exist in the template table; no note can be routed to it, so "
                        "gate 46 will reject any note where the rule fires" % (rule, o))
        for t in sorted(_labels):
            if (t in _pri and t in _ev) or t in _only:
                continue
            failures.append(
                "template %r has a label but %s, and is not declared label-only; a "
                "half-built template must be an explicit decision"
                % (t, "no ranking rules" if t in _ev else
                   ("no routing evidence" if t in _pri else
                    "neither routing evidence nor ranking rules")))
        for t in sorted(_only):
            if t not in _labels:
                failures.append("a template declared label-only, %r, is not a "
                                "template" % t)
            elif t in _pri and t in _ev:
                failures.append("template %r is declared label-only but now has both "
                                "routing evidence and ranking rules; the declaration "
                                "is stale" % t)
        for t in sorted(st.get("validated_by_live_fixture") or {}):
            if t not in _labels:
                failures.append("a template recorded as fixture-validated, %r, is not "
                                "a template" % t)
        _sem = st.get("table_semantics") or {}
        if (set(st.get("validated_by_live_fixture") or {}) & _only) and not _sem.get("both"):
            failures.append(
                "template(s) %s appear in both validated_by_live_fixture and "
                "declared_label_only and the payload does not say what that means; "
                "a reader of the audit trail sees a contradiction"
                % ", ".join(sorted(set(st.get("validated_by_live_fixture") or {}) & _only)))

    _BANDS = {"very unusual", "unusual", "notable", "statistically routine",
              "not scoreable"}
    _ECON = {"high", "moderate", "low", "immaterial", "not comparable"}
    _XM = {"diverging", "confirmed", "diverging and confirmed", "not corroborated",
           "not assessed"}
    _COMM = {"explained", "partly explained", "not addressed", "conflicting",
             "unavailable"}
    _EXPL = {"supported explanation", "unresolved driver"}
    for key in ("headline_moves", "accelerating", "decelerating", "primary_context",
                "deep_dive"):
        for r in p.get(key) or []:
            nm = r.get("display") or r.get("ltcode")
            a = r.get("assessment")
            if not a:
                failures.append("%s: %s carries no dual-materiality assessment"
                                % (key, nm))
                continue
            su, em = a.get("statistical_unusualness") or {}, \
                a.get("economic_materiality") or {}
            if su.get("band") not in _BANDS:
                failures.append("%s: %s has statistical band %r, which is not one of "
                                "the declared bands" % (key, nm, su.get("band")))
            if em.get("band") not in _ECON:
                failures.append("%s: %s has economic band %r, which is not one of the "
                                "declared bands" % (key, nm, em.get("band")))
            if em.get("large_on_economics_alone") and su.get("clears_statistical_bar"):
                failures.append(
                    "%s: %s is marked as large on economics alone AND as clearing "
                    "the statistical bar; those are mutually exclusive" % (key, nm))
            xm = (a.get("cross_metric") or {}).get("status")
            if xm not in _XM:
                failures.append("%s: %s has cross-metric status %r, which is not one "
                                "of the declared states" % (key, nm, xm))
            cf = a.get("confidence") or {}
            if cf.get("explanation") not in _EXPL:
                failures.append("%s: %s has explanation standing %r" % (key, nm,
                                                                        cf.get("explanation")))
            st = ((cf.get("commentary") or {}).get("standing"))
            if st not in _COMM:
                failures.append("%s: %s has commentary standing %r, which is not one "
                                "of the five declared states" % (key, nm, st))
    _ps = p.get("primary_story")
    _known = {d.get("rule") for d in ((p.get("divergences") or [])
                                      + (p.get("confirmations") or []))}
    if _ps and _ps.get("rule") not in _known:
        failures.append("the summary leads with a cross-metric story %r that is not "
                        "among the detected divergences or confirmations"
                        % _ps.get("rule"))
    if not _ps and _known:
        failures.append("relationships were detected but the summary leads with none")
    if not _ps and "single-line" not in str(p.get("story_basis") or ""):
        failures.append("no cross-metric story was selected and the note does not say "
                        "the findings stand on individual lines")
    for c in p.get("confirmations") or []:
        if c.get("basis") != "observed relationship":
            failures.append("confirmation %r declares basis %r; a co-movement is an "
                            "observed relationship, never an explanation"
                            % (c.get("rule"), c.get("basis")))
    if re.search(r"(?<!statistically )\broutine\s*[-+]?\d", page_flat):
        failures.append("the page prints 'Routine' as a band label; the label is "
                        "'Statistically routine', because the short form reads as "
                        "unimportant")

    _STOP = {"the", "and", "of", "for", "a", "an", "in", "on", "to", "per",
             "total", "consolidated", "adj", "items", "1x", "ex", "co", "defined",
             "average", "net", "long", "term"}

    def _words(txt):
        return {w[:-1] if len(w) > 4 and w.endswith("s") else w
                for w in re.findall(r"[a-z]+", (txt or "").lower())
                if len(w) > 2 and w not in _STOP}

    _dive_rows = p.get("deep_dive") or []
    _blocks = []
    if narr and _dive_rows:
        _own = {}
        for i, r in enumerate(_dive_rows, 1):
            _own[str(i)] = {
                "display": r.get("display") or r.get("name"),
                "words": _words(r.get("display") or r.get("name")),
                "unit": r.get("unit"), "kind": r.get("kind"),
                "unit_label": r.get("unit_label")}
        for k, v in _own.items():
            others = set().union(*[o["words"] for k2, o in _own.items()
                                   if k2 != k] or [set()])
            v["unique"] = v["words"] - others
        for k, v in (narr_raw or narr or {}).items():
            if isinstance(v, (list, tuple)):
                for j, x in enumerate(v, 1):
                    _blocks.append((f"{k}[{j}]", str(x)))
            elif isinstance(v, str):
                _blocks.append((k, v))
        for label, raw_txt in _blocks:
            used = set(re.findall(r"\{\{\s*dd(\d)\.", raw_txt))
            if not used:
                continue
            bw = _words(re.sub(r"\{\{[^}]*\}\}", " ", raw_txt))
            named_by_token = set(re.findall(r"\{\{\s*dd(\d)\.name\s*\}\}", raw_txt))
            for idx in sorted(used):
                o = _own.get(idx)
                if not o or not o["words"]:
                    continue
                named_it = idx in named_by_token or (
                    bool(bw & o["unique"]) if o["unique"] else
                    (len(bw & o["words"]) * 2 >= len(o["words"])))
                if not named_it:
                    named = sorted(
                        v["display"] for k2, v in _own.items()
                        if v["words"] and (bool(bw & v["unique"]) if v["unique"]
                                           else len(bw & v["words"]) * 2
                                           >= len(v["words"])))
                    failures.append(
                        "narrative %s carries figures belonging to %r (placeholder "
                        "dd%s) but never names it%s. A sentence may only carry "
                        "figures from the metric it names"
                        % (label, o["display"], idx,
                           "; it names " + ", ".join(repr(x) for x in named)
                           if named else ""))

    _ph = p.get("placeholders") or {}

    def _resolve(txt):
        return re.sub(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}",
                      lambda m: str(_ph.get(m.group(1), " ")), str(txt))

    _resolved_blocks = [(lb, _resolve(tx)) for lb, tx in (_blocks or [])] \
        if narr and _dive_rows else []
    if _resolved_blocks:
        _MONEY = re.compile(r"\b\d[\d,]*\.?\d*\s*(?:[KMBT]\b|billion|million)")
        _RATIO_NAMED = re.compile(r"\b(margin|ratio|load factor|yield)\b", re.I)
        for label, txt in (_resolved_blocks or []):
            if not _RATIO_NAMED.search(txt):
                continue
            if _MONEY.search(txt) and not re.search(
                    r"\b(revenue|sales|cash flow|fcf|ocf|income|profit|fee|"
                    r"expense|capex|assets?|aum|ebit|ebitda|ebitdax|buyback|repurchases?|earnings)\b", txt, re.I):
                failures.append(
                    "narrative %s names a ratio and carries a currency amount with "
                    "no currency line named: %r" % (label, txt[:90]))

    _cc = p.get("cash_conversion") or {}
    _cc_rows = _cc.get("rows") or []
    _cc_concl = str(_cc.get("conclusion") or "")
    if _cc_concl and _cc_rows:
        _CASH_LABELS = ("Operating Cash Flow", "Free Cash Flow")
        _sets = {
            "panel line": len(_cc_rows),
            "cash line": len([r for r in _cc_rows if r.get("label") in _CASH_LABELS]),
        }
        for _noun, _n in _sets.items():
            _pat = _noun.replace(" ", r"\s+") + r"s?\b"
            for m in re.finditer(r"\b(\d+)\s+of\s+(\d+)\s+" + _pat, _cc_concl, re.I):
                if int(m.group(2)) != _n:
                    failures.append(
                        "the cash panel says %r but the payload carries %d %s%s; a "
                        "count must match the set it names"
                        % (m.group(0), _n, _noun, "" if _n == 1 else "s"))
                if int(m.group(1)) > int(m.group(2)):
                    failures.append("the cash panel says %r, a subset larger than its "
                                    "set" % m.group(0))
            for m in re.finditer(r"\b(\d+)\s+" + _pat + r"[^.]*?,\s*of\s+(\d+)\s+shown",
                                 _cc_concl, re.I):
                if int(m.group(2)) != _n:
                    failures.append(
                        "the cash panel says %r but the payload carries %d %s%s; a "
                        "count must match the set it names"
                        % (m.group(0), _n, _noun, "" if _n == 1 else "s"))
            for m in re.finditer(r"\b(Both|\d+)\s+" + _pat + r"(?!\s+shown)"
                                 r"(?![^.]*\bof\s+\d+)", _cc_concl, re.I):
                _claim = 2 if m.group(1).lower() == "both" else int(m.group(1))
                if _claim != _n:
                    failures.append(
                        "the cash panel says %r, an unqualified count of %d, while the "
                        "payload carries %d %s%s"
                        % (m.group(0).strip(), _claim, _n, _noun,
                           "" if _n == 1 else "s"))

    for _q in (p.get("quiet_headline") or []):
        if not _q.get("flow_rate_mode"):
            continue
        _dn = str(_q.get("flow_denominator_name") or "")
        _nm = str(_q.get("display") or _q.get("name") or "")
        for _m in re.finditer(
                re.escape(_nm) + r"\s+ran at[^;]{0,90}", page_flat, re.I):
            _s = _m.group(0)
            if re.search(r"\bopening\b", _s, re.I) and re.search(
                    r"\b(eop|end[- ]of[- ]period|closing)\b", _s, re.I):
                if not re.search(r"prior quarter'?s closing", _s, re.I):
                    failures.append(
                        "the flow-rate sentence for %s calls its denominator "
                        "'opening' while labelling it end-of-period: %r"
                        % (_nm, _s[:110]))
            if _dn and re.search(r"\bof the opening\s+" + re.escape(_dn),
                                 _s, re.I) and re.search(
                                     r"\beop\b|end[- ]of[- ]period", _dn, re.I):
                failures.append(
                    "the flow-rate sentence for %s prints the stock's closing label "
                    "under an opening claim: %r" % (_nm, _s[:110]))

    _qz = {str(q.get("metric")): q for q in
           ((p.get("scan_status") or {}).get("quantised_lines") or [])}
    _flagged = {}
    for _sec in ("deep_dive", "accelerating", "decelerating", "primary_context"):
        for _r in (p.get(_sec) or []):
            if "quantised_source" in (_r.get("flags") or []):
                _nm0 = str(_r.get("display") or _r.get("name"))
                if str(_r.get("confidence")) == "High" and \
                        str(_flagged.get(_nm0, {}).get("confidence")) != "High":
                    _flagged[_nm0] = _r
                else:
                    _flagged.setdefault(_nm0, _r)
    for _nm, _r in sorted(_flagged.items()):
        if _nm not in _qz:
            failures.append(
                "%r is computed on a coarse reporting grid and is absent from "
                "scan_status.quantised_lines, so the page cannot disclose it" % _nm)
        elif _nm.lower() not in page_flat:
            failures.append(
                "%r is disclosed as coarsely reported in the payload and its name "
                "never appears on the page" % _nm)
        if str(_r.get("confidence")) == "High":
            failures.append(
                "%r carries High confidence on a figure the source rounds to %s bps; "
                "confidence grades data quality and a coarse grid is a data-quality "
                "fact" % (_nm, _r.get("quant_step_bps")))
    for _nm in sorted(set(_qz) - set(_flagged)):
        failures.append(
            "scan_status names %r as coarsely reported and no displayed row "
            "carries the flag" % _nm)

    _rc = p.get("reporting_currency") or {}
    _sym = _rc.get("symbol")
    _cur_rows = [r for _sec in ("deep_dive", "accelerating", "decelerating",
                                "primary_context")
                 for r in (p.get(_sec) or [])
                 if r.get("unit_label") == "currency"
                 and r.get("unit_status") != "conflicted"]
    if _cur_rows and not _sym:
        failures.append(
            "%d displayed row(s) are money and the note states no reporting "
            "currency; a magnitude with no symbol reads as the reader's own"
            % len(_cur_rows))
    for _r in _cur_rows:
        if not _r.get("currency"):
            failures.append(
                "%r is money and carries no currency; the symbol is parsed off "
                "every source value and must not be dropped"
                % (_r.get("display") or _r.get("name")))
            break
    if _sym and _sym.lower() not in page_flat:
        failures.append(
            "the reporting currency %r appears nowhere on the page; a magnitude "
            "with no symbol reads as the reader's own currency" % _sym)
    if _sym and _cur_rows:
        _attached = len(re.findall(re.escape(_sym) + r"\s?-?[\d.,]", page_flat, re.I))
        if _attached < 1:
            failures.append(
                "%d displayed row(s) are quoted in %s and no figure on the page "
                "carries the symbol; a magnitude printed bare reads as the "
                "reader's own currency" % (len(_cur_rows), _sym))
    if _sym and not re.search(r"currency[^.]{0,80}(reports? in|denominated in|"
                              r"stated in)[^.]{0,40}" + re.escape(_sym),
                              page_flat, re.I):
        failures.append(
            "the note never STATES its reporting currency; %r appears on figures "
            "but no sentence says what the figures are denominated in. A symbol "
            "on a number is not a disclosure" % _sym)
    if _rc.get("conflicted_lines"):
        failures.append(
            "line(s) quoted in more than one currency across the window: %s. A "
            "growth rate across a currency change is not a growth rate"
            % ", ".join(_rc["conflicted_lines"][:3]))

    _ccq = p.get("cash_conversion") or {}
    _qn = str(_ccq.get("quality_note") or "")
    if _qn:
        _ra = _ccq.get("revenue_accel")
        _rg = _ccq.get("revenue_growth")
        if re.search(r"accelerating revenue|revenue accelerat", _qn, re.I) and \
                (_ra is None or _ra <= 0):
            failures.append(
                "the cash panel's quality note says revenue accelerated; the panel "
                "records a revenue acceleration of %s" % _ra)
        _rows_q = {r.get("label"): r for r in (_ccq.get("rows") or [])}
        if re.search(r"receivables? growth|faster receivables", _qn, re.I):
            _ar = _rows_q.get("Accounts Receivable") or _rows_q.get("Accounts Receivables")
            if _ar and _rg is not None and (_ar.get("latest_growth") or 0) <= _rg:
                failures.append(
                    "the cash panel's quality note cites faster receivables growth; "
                    "receivables grew %.1f%% against revenue of %.1f%%"
                    % (_ar.get("latest_growth") or 0, _rg))
        _cash_out = [r for r in (_ccq.get("rows") or [])
                     if r.get("label") in ("Operating Cash Flow", "Free Cash Flow")
                     and _rg is not None and (r.get("latest_growth") or 0) > _rg]
        if _cash_out and re.search(r"less favourable|unfavourable|ahead of cash", _qn, re.I):
            failures.append(
                "the cash panel's quality note reads the quarter as a conversion "
                "worry while %s grew faster than revenue"
                % " and ".join(r["label"] for r in _cash_out))

    _exc = {e.get("ltcode"): str(e.get("reason") or "")
            for e in (p.get("excluded") or [])}
    for _u in (p.get("unscoreable_primary") or []):
        _c = _u.get("ltcode")
        if _c in _exc and _exc[_c] != str(_u.get("reason") or ""):
            failures.append(
                "%s is given two different reasons for not being scored: %r in "
                "excluded and %r in unscoreable_primary. A reader who reads both "
                "learns that the note does not know"
                % (_c, _exc[_c][:60], str(_u.get("reason") or "")[:60]))
            break

    _minq = (p.get("constants") or {}).get("min_quarters_to_rank", 9)
    for _e in (p.get("excluded") or []) + (p.get("unscoreable_primary") or []):
        _rs = str(_e.get("reason") or "")
        _q = _e.get("quarters") or 0
        if re.search(r"not enough history|want of history", _rs, re.I) and _q >= _minq:
            failures.append("%s is held out for want of history with %d quarters (gate 115)"
                            % (_e.get("ltcode"), _q))
        _m115 = re.match(r"only (\d+) quarters of history came back", _rs)
        if _m115 and int(_m115.group(1)) >= _minq:
            failures.append("%s: %r, at or above the %d-quarter minimum (gate 115)"
                            % (_e.get("ltcode"), _rs[:60], _minq))
        if _e.get("reason_code") == "unclassified":
            warnings.append("%s was held out by a stage that recorded no reason"
                            % _e.get("ltcode"))

    _cc = p.get("cash_conversion") or {}
    _concl = str(_cc.get("conclusion") or "")
    _rows = _cc.get("rows") or []
    _m = re.search(r"no (\w+) line was returned", _concl, re.I)
    if _m:
        _noun = _m.group(1).lower()
        _CASH = ("Operating Cash Flow", "Free Cash Flow")
        _present = ([r for r in _rows if r.get("label") in _CASH]
                    if _noun == "cash" else _rows)
        if _present:
            failures.append(
                "the cash panel states that no %s line was returned while the "
                "panel prints %d: %s. The sentence names a wider set than the "
                "one it counts" % (_noun, len(_present),
                                   ", ".join(str(r.get("label")) for r in _present[:3])))

    for _sec in ("headline_moves", "accelerating", "decelerating", "deep_dive",
                 "primary_context", "monitor", "quiet_headline"):
        for _r in (p.get(_sec) or []):
            if _r.get("pct_source_marked") and _r.get("pct_read_as_fraction"):
                failures.append(
                    "%r is quoted by the source with a percent sign and was "
                    "rescaled as though it were a fraction; the published level "
                    "is a hundred times the reported one"
                    % (_r.get("display") or _r.get("name")))
                break

    _blanked = {e.get("ltcode") for e in (p.get("excluded") or [])
                if "never been near zero" in str(e.get("reason") or "")}
    if _blanked:
        for _sec in ("headline_moves", "accelerating", "decelerating", "deep_dive",
                     "primary_context", "monitor", "quiet_headline"):
            _bad = [r.get("ltcode") for r in (p.get(_sec) or [])
                    if r.get("ltcode") in _blanked]
            if _bad:
                failures.append(
                    "%s is ranked in %s after its latest value was withheld as a "
                    "feed gap; a line held out for a missing figure is held out "
                    "everywhere" % (", ".join(map(str, _bad)), _sec))

    _gcr = ((p.get("guidance_check") or {}).get("retrieval") or {})
    _declines = (p.get("guidance_check") or {}).get("declines_to_guide") or []
    _vis = " ".join(visible_text(doc).split())
    _GAP_CLAIM = re.compile(r"not established either way|retrieval gap", re.I)
    _REFUSAL_CLAIM = re.compile(r"states? (?:it |that it )?does not guide|"
                                r"declin\w+ to (?:provide|give|issue) guidance|"
                                r"not providing guidance", re.I)
    if _declines and _GAP_CLAIM.search(_vis):
        failures.append(
            "the note reports guidance as an open retrieval gap while the engine "
            "holds %d statement(s) from management that it does not provide "
            "guidance. A company on the record declining to guide is a fact "
            "about the issuer, not a hole in this scan" % len(_declines))
    if not _declines and _REFUSAL_CLAIM.search(_vis):
        failures.append(
            "the note states that the company does not guide, and the engine "
            "holds no such statement from management. A refusal to guide is the "
            "company's own disclosure and is reported only where the source "
            "carries it")
    if _gcr.get("state") == "declines_to_guide" and not _declines:
        failures.append(
            "guidance state is 'declines_to_guide' with no statement behind it")

    _DIVTAG = re.compile(r"<div\b[^>]*>|</div>", re.I)
    for _sm in re.finditer(r'<div class="sect">\s*<div class="label">(.*?)</div>'
                           r'\s*<div class="rule"></div>', doc, re.S):
        _depth, _end = 1, _sm.end()
        for _t in _DIVTAG.finditer(doc, _sm.end()):
            _depth += 1 if _t.group(0).lower().startswith("<div") else -1
            if _depth == 0:
                _end = _t.start()
                break
        else:
            continue
        _lbl = re.sub(r"<[^>]+>", "", _sm.group(1)).strip()
        _body = re.sub(r"<[^>]+>", " ", doc[_sm.end():_end])
        if not html_lib.unescape(_body).strip():
            failures.append(
                "the section %r prints its heading with nothing under it. A "
                "heading is a promise about what follows; where the section has "
                "nothing to report it has to say so in a sentence, because a "
                "bare label reads as a rendering fault rather than as an "
                "absence the note meant to report" % _lbl)

    _RATIO_UNITS = {"%", "% Y/Y", "bps", None}
    for _sec in ("deep_dive", "accelerating", "decelerating", "primary_context",
                 "monitor", "quiet_headline"):
        for _r in (p.get(_sec) or []):
            if _r.get("kind") != "ratio":
                continue
            if _r.get("unit_status") == "conflicted":
                continue
            _u = _r.get("unit_label")
            if _u not in _RATIO_UNITS and "/" not in str(_u):
                failures.append(
                    "%r is quoted as a percentage by the source and displayed "
                    "in %r; a keyword in a line's name cannot turn a percentage "
                    "into a %s" % (_r.get("display") or _r.get("name"), _u, _u))
                break

    _sr = p.get("sector_routing") or {}
    if _sr.get("template"):
        _rel = _sr.get("classification_relation")
        _prop = _sr.get("proposed_from_classification") or []
        _tmpl = _sr.get("template")
        _want = ("filled_gap" if not _prop
                 else "agreed" if _tmpl == _prop[0]
                 else "chose_among_proposed" if _tmpl in _prop
                 else "overrode")
        if _rel != _want:
            failures.append(
                "the note records the routing as %r; the classification proposed "
                "%s and the template is %r, which is %r. The reader is told the "
                "evidence did something to the classification that it did not do"
                % (_rel or "nothing", _prop or "nothing", _tmpl, _want))
        if _sr.get("overrode_classification") != (_want == "overrode"):
            failures.append(
                "overrode_classification is %r on a routing that %r the "
                "classification" % (_sr.get("overrode_classification"), _want))
        _lab = str(_sr.get("template_label") or "")
        _cls_str = str(_sr.get("insync_subindustry") or "")
        if re.search(r"the note used\s+" + re.escape(_lab) + r"\s+instead",
                     page_flat, re.I):
            if _want != "overrode":
                failures.append(
                    "the page says the note used %r INSTEAD of the source "
                    "classification, and the routing %r it" % (_lab, _want))
            if _lab.strip().lower() == _cls_str.strip().lower():
                failures.append(
                    "the page says the note used %r instead of %r - the same "
                    "thing named twice" % (_lab, _cls_str))

    for _sec in ("deep_dive", "accelerating", "decelerating", "primary_context"):
        for _r in (p.get(_sec) or []):
            if _r.get("tier") != 1 or not _r.get("segment_scope"):
                continue
            if _r.get("tier_basis") != "segment_size":
                failures.append(
                    "%r is scoped to the %r segment and graded Primary on %r; a "
                    "segment's copy of a spine line is not the firm's spine, and "
                    "reaches the primary tier only by share of group revenue"
                    % (_r.get("display") or _r.get("name"), _r.get("segment_scope"),
                       _r.get("tier_basis") or "no published basis"))

    _uns = p.get("unscoreable_primary") or []
    if _uns:
        _named = [u for u in _uns[:3]
                  if str(u.get("display") or u.get("name") or "").lower() in page_flat]
        if not _named:
            failures.append(
                "%d primary line(s) could not be scored and none is named on the "
                "page: %s. Absent for want of history is not the same finding as "
                "scored and unremarkable"
                % (len(_uns), ", ".join(str(u.get("display") or u.get("name"))
                                        for u in _uns[:3])))
    for _u in _uns:
        _nm = str(_u.get("display") or _u.get("name") or "")
        for _sec in ("accelerating", "decelerating", "deep_dive", "primary_context"):
            if any((r.get("display") or r.get("name")) == _nm
                   for r in (p.get(_sec) or [])):
                failures.append(
                    "%r is listed as unscoreable and also displayed in %s; a line "
                    "is scored or it is not" % (_nm, _sec))

    _NOUN_PAT = re.compile(r"\b(margin|occupancy|rent change|retention|load factor|"
                           r"payout ratio|tax rate|growth rate|coverage|yield|share|"
                           r"ratio|top line|fee growth|cash conversion|unit trend)\b", re.I)
    _NOUN_OK = {
        "margin": r"margin|\bgm\b|gross profit", "occupancy": r"occupan",
        "rent change": r"rent change", "retention": r"retention",
        "load factor": r"load factor", "payout ratio": r"payout",
        "tax rate": r"tax rate|\betr\b", "growth rate": r"growth|y/y|yoy|organic|cagr|\bcc\b|constant currency",
        "coverage": r"coverage", "yield": r"yield|take rate",
        "share": r"%\s*revenue|as % of|share|% of|price\s*/?\s*mix|contribution|\bfx\b|currency|m&a",
        "ratio": r"ratio|\bmlr\b|\bmcr\b", "top line": r"revenue|sales",
        "fee growth": r"fee", "cash conversion": r"cash|\bocf\b|\bfcf\b",
        "unit trend": r"volume|production|per |/|unit|price|cost|rasm|casm|yield|per available seat|arpu|throughput",
    }
    for _m in (p.get("monitor") or []):
        _q = str(_m.get("question") or "")
        _nm = str(_m.get("variable") or "")
        _src = next((r for k in ("deep_dive", "headline_moves", "accelerating",
                                 "decelerating", "primary_context")
                     for r in (p.get(k) or []) if r.get("ltcode") == _m.get("ltcode")), None)
        _raw = str((_src or {}).get("name") or _nm)
        for _hit in _NOUN_PAT.findall(_q):
            _k = _hit.lower()
            if _k in _NOUN_OK and not re.search(_NOUN_OK[_k], _raw, re.I):
                failures.append(
                    "monitor question for %r asks about %r, which is not what the "
                    "line is: %r" % (_nm, _hit, _q))
                break

    for _r in (p.get("guidance_check") or {}).get("revisions") or []:
        if _r.get("is_percentage") and _r.get("midpoint_change_pct") is not None:
            failures.append(
                "guidance revision for %r is a percentage guide and carries a "
                "midpoint change in percent (%s); the change belongs in points"
                % (_r.get("display"), _r.get("midpoint_change_pct")))
            break
        if not _r.get("is_percentage") and _r.get("midpoint_change_bps") is not None:
            failures.append(
                "guidance revision for %r is a money or volume guide and carries a "
                "change in basis points" % _r.get("display"))
            break

    for _sec in ("headline_moves", "accelerating", "decelerating", "deep_dive",
                 "primary_context", "monitor"):
        for _r in (p.get(_sec) or []):
            if "cumulative_series" in (_r.get("flags") or []):
                failures.append(
                    "%r is flagged as a cumulative (year-to-date) series and is "
                    "displayed in %s beside quarterly lines"
                    % (_r.get("display") or _r.get("name") or _r.get("variable"), _sec))
                break

    _shown_codes = {r.get("ltcode") for k in ("headline_moves", "accelerating",
                                               "decelerating", "deep_dive", "primary_context")
                    for r in (p.get(k) or [])}
    for _e in (p.get("excluded") or []):
        if str(_e.get("reason") or "").startswith("identical history") and \
                _e.get("twin_of") in _shown_codes and _e.get("provenance") == "filed" \
                and _e.get("twin_provenance") == "derived":
            failures.append(
                "%r (filed) was held out as a twin of %r (calculated); the filed "
                "line is the survivor when two copies of one number tie"
                % (_e.get("name"), _e.get("twin_of")))
            break

    for _sec in ("headline_moves", "accelerating", "decelerating", "deep_dive"):
        for _r in (p.get(_sec) or []):
            _w, _g = _r.get("level_word"), _r.get("latest_growth")
            if _r.get("kind") == "pre_growth":
                _g = ((_r.get("display_value") - _r.get("prior_year_value"))
                      if _r.get("display_value") is not None and _r.get("prior_year_value") is not None
                      else None)
            if not _w or _g is None or _g == 0:
                continue
            _says_up = _w in ("growing", "widening", "rising")
            if (_g > 0) != _says_up:
                failures.append(
                    "%r carries the level word %r while its year-over-year change is %s"
                    % (_r.get("display") or _r.get("name"), _w, _g))
                break
    for _sec in ("headline_moves", "accelerating", "decelerating", "deep_dive"):
        for _r in (p.get(_sec) or []):
            _t = _r.get("pace_only")
            if _t and _r.get("level_word") and _t != "still " + str(_r.get("level_word")):
                failures.append(
                    "%r carries a pace tag %r that does not match its level word %r"
                    % (_r.get("display") or _r.get("name"), _t, _r.get("level_word")))
                break

    _ccr = [r for r in ((p.get("cash_conversion") or {}).get("rows") or [])
            if r.get("label") in ("Operating Cash Flow", "Free Cash Flow", "Capital Expenditure")]
    if re.search(r"three months", visible_text(doc), re.I) and _ccr and \
            not all(r.get("period_basis") == "quarterly" for r in _ccr):
        failures.append(
            "the cash panel labels its figures three months while %s cannot prove a "
            "quarterly basis" % ", ".join(str(r.get("label")) for r in _ccr
                                           if r.get("period_basis") != "quarterly"))
    for _r in _ccr:
        if _r.get("period_basis") == "year_to_date":
            failures.append("%s reads as year-to-date and is compared with quarterly "
                            "revenue" % _r.get("label"))
            break

    for _ex in (p.get("company_exclusions") or [])[:1]:
        if _ex.get("base_value") and abs(_ex.get("gap") or 0) >= 0.10 * abs(_ex["base_value"]):
            if str(_ex.get("gap_display")) not in visible_text(doc) or \
                    str(_ex.get("item") or "").lower() not in visible_text(doc).lower():
                failures.append(
                    "the company files %r excluding %r and the gap (%s) is material, "
                    "but the page does not state it"
                    % (_ex.get("base_display"), _ex.get("item"), _ex.get("gap_display")))

    _vis = visible_text(doc)
    _visl = re.sub(r"\s+", " ", _vis).lower()

    for _ex in (p.get("company_exclusions") or [])[:1]:
        if _ex.get("base_value") and abs(_ex.get("gap") or 0) >= 0.10 * abs(_ex["base_value"]):
            if "item the company excludes" in _visl or \
                    (str(_ex.get("item") or "").lower() + " is inside") not in _visl:
                failures.append(
                    "the exclusion is worded backwards: %r includes %r and the page must "
                    "say the item is inside it, not that the company excludes it"
                    % (_ex.get("base_display"), _ex.get("item")))

    _rows_by = {}
    for _k in ("headline_moves", "accelerating", "decelerating", "deep_dive", "primary_context"):
        for _r in (p.get(_k) or []):
            _rows_by.setdefault(_r.get("ltcode"), _r)
    for _m in (p.get("monitor") or []):
        _q = str(_m.get("question") or "")
        _claim = "hard" if "harder comparison" in _q else ("easy" if "easier comparison" in _q else None)
        if not _claim:
            continue
        _r = _rows_by.get(_m.get("ltcode")) or {}
        _nb = (_r.get("forward_bar") or {}).get("next_base_period")
        _ps, _gs = _r.get("period_series") or [], _r.get("growth_series") or []
        _actual = None
        if _nb in _ps and len(_ps) == len(_gs):
            _gb = _gs[_ps.index(_nb)]
            _h = sorted(x for x in _gs if x is not None)
            if _gb is not None and len(_h) >= 6:
                _actual = ("hard" if _gb >= _h[(3 * len(_h)) // 4]
                           else "easy" if _gb <= _h[len(_h) // 4] else None)
        if _actual != _claim:
            failures.append(
                "monitor question on %r calls next quarter's comparison %s, but the %s "
                "base's own growth rate does not put it there"
                % (_m.get("variable"), "harder" if _claim == "hard" else "easier", _nb))

    _gc = p.get("guidance_check") or {}
    for _k in ("other_horizon", "scored", "forward_uncompared", "delivered"):
        for _g in (_gc.get(_k) or []):
            if not _g.get("guide_display_text"):
                continue
            _t = re.sub(r"(?<=[a-z\)])\d(?=[,.;:]|\s|$)", "", str(_g.get("guide_text") or ""))
            if not re.search(r"\d", _t):
                failures.append(
                    "guide on %r shows a figure (%s) but its source text has no number "
                    "once footnote markers are removed: %r"
                    % (_g.get("display") or _g.get("name"), _g.get("guide_display_text"),
                       str(_g.get("guide_text"))[:60]))

    _cc = p.get("cash_conversion") or {}
    _returned = {r.get("label") for r in (_cc.get("rows") or [])}
    _returned |= {str(u.get("display") or "") for u in (p.get("unscoreable_primary") or [])}
    _bad = [x for x in (_cc.get("not_disclosed") or []) if x in _returned]
    if _bad:
        failures.append("the cash panel says %s %s not in the scan, but the scan returned "
                        "%s" % (", ".join(_bad), "is" if len(_bad) == 1 else "are",
                                "it" if len(_bad) == 1 else "them"))

    _BOUND = re.compile(r"greater than|more than|at least|in excess of|^\s*>|less than|"
                        r"up to|no more than|^\s*<", re.I)
    for _rv in (_gc.get("revisions") or []):
        _both = all(_BOUND.search(str(_rv.get(k) or "")) for k in ("first_text", "latest_text"))
        if _both and (_rv.get("midpoint_change_pct") is not None
                      or _rv.get("midpoint_change_bps") is not None
                      or not str(_rv.get("direction") or "").startswith(("floor", "ceiling"))):
            failures.append("the %s guide on %r is a floor or ceiling and is reported as a "
                            "range with a midpoint" % (_rv.get("target_period"), _rv.get("display")))

    _YA = re.compile(r"\b(faster|slower|more slowly|more quickly|smaller|larger) than a year ago|"
                     r"year-ago (quarter|comparison) that", re.I)
    for _k, _v in (narr_raw or {}).items():
        for _txt in (_v if isinstance(_v, list) else [_v]):
            if isinstance(_txt, str) and _YA.search(_txt):
                failures.append("narrative %s compares the pace with 'a year ago'; the "
                                "comparison the note measures is last quarter's rate: %r"
                                % (_k, _YA.search(_txt).group(0)))

    for _sec in ("provenance and method", "sources, guidance and methodology"):
        _refs = len(re.findall(re.escape(_sec), _visl))
        _head = re.search(r'class="label">\s*' + re.escape(_sec), doc, re.I)
        if _refs and not _head:
            failures.append("the page refers readers to %r, which this edition does not "
                            "print" % _sec.title())
    if "named under sources" in _visl or "scoped to the" in _visl and "discovery plan" in _visl:
        failures.append("the page carries a stale pointer to a list it does not print")

    _flat = re.sub(r"\s+", " ", _vis)

    _neg = {}
    for _k in ("headline_moves", "accelerating", "decelerating", "deep_dive"):
        for _r in (p.get(_k) or []):
            if _r.get("kind") == "level" and (_r.get("latest_growth") or 0) < 0:
                _neg[str(_r.get("display") or _r.get("name"))] = _r.get("latest_growth")
    for _st in [p.get("primary_story") or {}] + list(p.get("supporting_stories") or []):
        for _m in (_st.get("metrics") or []):
            if _m.get("unit") == "pct" and _m.get("kind") != "pre_growth" \
                    and (_m.get("growth") or 0) < 0:
                _neg[str(_m.get("display") or _m.get("name"))] = _m.get("growth")
    for _nm, _g in _neg.items():
        if re.search(r"(?<![A-Za-z])" + re.escape(_nm) + r"(?:'s)? growth (picked up|accelerated|quickened|sped up|rose)",
                     _flat, re.I):
            failures.append("%r is described as growing faster while it is below its "
                            "year-ago level (%+.1f%% y/y); say the decline eased" % (_nm, _g))

    _otn = re.search(r"One-time items the company reports this quarter:(.*?)Each is sized against revenue", _flat)
    _otn = _otn.group(1) if _otn else ""
    for _o in (p.get("one_time_items") or []):
        if str(_o.get("display") or _o.get("name")) not in _otn:
            failures.append("one-time item %r is in the payload and not on the page"
                            % (_o.get("display") or _o.get("name")))
        elif _o.get("revenue_bps") is not None and ("%s bps of revenue" % _o["revenue_bps"]) not in _otn:
            failures.append("one-time item %r is listed without its size against revenue"
                            % (_o.get("display") or _o.get("name")))

    for _r in (p.get("headline_moves") or []):
        _pb = _r.get("period_basis")
        if _pb == "quarterly" and str(_r.get("period_evidence")) not in _flat:
            failures.append("cash-flow line %r is shown without the evidence that it is "
                            "quarterly" % (_r.get("display") or _r.get("name")))
        elif _pb == "unverified" and "period unverified" not in _flat:
            failures.append("cash-flow line %r cannot prove its period and is not marked"
                            % (_r.get("display") or _r.get("name")))
        elif _pb == "year_to_date":
            failures.append("year-to-date line %r is in the quarterly top table"
                            % (_r.get("display") or _r.get("name")))

    for _r in (p.get("headline_moves") or []):
        if _r.get("provenance") != "derived":
            continue
        if _r.get("insync_calculated") and str(_r.get("display") or "").startswith("InSync-Calculated"):
            continue
        _cell = re.search(r'<td class="name">' + re.escape(html_lib.escape(str(_r.get("display") or _r.get("name")))) + r'(.*?)</td>', doc)
        if not _cell or 'class="mk cm"' not in _cell.group(1):
            failures.append("calculated row %r carries no calculated-by-InSync mark"
                            % (_r.get("display") or _r.get("name")))

    for _r in (p.get("headline_moves") or []):
        if _r.get("kind") == "level" and _r.get("latest_growth") is not None \
                and abs(_r["latest_growth"]) >= 50.0:
            if ("\u201c" + str(_r.get("name")) + "\u201d") not in _vis:
                failures.append("large move on %r (%+.1f%%) does not name its source line"
                                % (_r.get("display") or _r.get("name"), _r["latest_growth"]))

    for _k in ("other_horizon", "scored", "delivered", "forward_uncompared"):
        for _g in ((p.get("guidance_check") or {}).get(_k) or []):
            _t = str(_g.get("guide_display_text") or "")
            if re.search(r"capex|capital expend", str(_g.get("name") or _g.get("display") or ""), re.I) \
                    and _t.startswith("-"):
                failures.append("capex guide on %r is shown as a negative figure (%s)"
                                % (_g.get("display") or _g.get("name"), _t))

    _mm = [m for m in (p.get("monitor") or []) if m.get("ltcode")
           and re.search(r"\bmargin\b", str(m.get("variable") or ""), re.I)]
    if len(_mm) > 1:
        failures.append("the monitor table asks %d margin questions (%s); one per concept"
                        % (len(_mm), ", ".join(str(m.get("variable")) for m in _mm)))

    _mast = re.search(r'<div class="mast">(.*?)<div class="mastrule">', doc, re.S)
    _mt = re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", _mast.group(1)))) if _mast else ""
    _lq = str(p.get("latest_quarter") or "")
    _qm = re.match(r"^([1-4])Q(\d{2})$", _lq)
    if not re.search(r"Inflection Monitor Reported Financial & Operating Momentum \| " + re.escape(_lq), _mt):
        failures.append("masthead does not say what the note monitors")
    _qe = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", str(p.get("latest_quarter_end") or ""))
    _mon = ("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()[int(_qe.group(2)) - 1]
            if _qe and 1 <= int(_qe.group(2)) <= 12 else None)
    _want_qe = ("Quarter End: %d %s %s (fiscal %s)" % (int(_qe.group(3)), _mon, _qe.group(1), _lq)) \
        if _mon else "Quarter End: not in source (fiscal %s)" % _lq
    if _want_qe not in _mt:
        failures.append("masthead quarter end is missing or wrong (want %r)" % _want_qe)
    if "Earnings Date:" not in _mt:
        failures.append("masthead lacks the earnings date")
    if "Source: company reports via InSync Analytics" not in _vis:
        failures.append("the source line is missing from the footer")
    if not re.search(r'<div class="brandline"><img class="logo" src="data:image/png;base64,[A-Za-z0-9+/=]{200,}', doc):
        failures.append("the InSync logo is missing from the masthead")
    for _lab in ("Company:", "Reported Quarter:", "Data Source:"):
        if _lab in _mt:
            failures.append("the masthead repeats %r, which the title already states" % _lab)
    if "does not compare results with consensus estimates" not in _vis:
        failures.append("the purpose statement is missing")
    if _qm:
        _qn, _yy = int(_qm.group(1)), int(_qm.group(2))
        _ya = "%dQ%02d" % (_qn, _yy - 1)
        _pq = ("%dQ%02d" % (_qn - 1, _yy)) if _qn > 1 else ("4Q%02d" % (_yy - 1))
        _want = "Comparison: %s (year ago) \u00b7 %s (last quarter)" % (_ya, _pq)
        if _want not in _mt:
            failures.append("masthead comparison basis is missing or wrong (want %r)" % _want)
        for _c in ((p.get("quarter_strip") or {}).get("cells") or []):
            for _k, _v in (("year_ago_period", _ya), ("prev_period", _pq)):
                if _c.get(_k) and _c[_k] != _v and _c.get("latest_growth") is not None:
                    failures.append("quarter strip tile %r compares with %s, masthead says %s"
                                    % (_c.get("label"), _c[_k], _v))

    _vis116 = re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", doc)))
    for _m in re.finditer(r"[^.]{0,60}\b(data file|JSON file|accompanying (file|json|data))\b[^.]{0,60}",
                          _vis116, re.I):
        failures.append("the page promises a file the reader does not receive: %r (gate 116)"
                        % _m.group(0).strip()[:120])
        break

    for _tb in re.findall(r"<table.*?</table>", doc, re.S):
        _ths = [html_lib.unescape(re.sub(r"<[^>]+>", "", x)).strip()
                for x in re.findall(r"<th[^>]*>(.*?)</th>", _tb, re.S)]
        _hit = None
        for _row in re.findall(r"<tr[^>]*>(.*?)</tr>", _tb, re.S):
            for _i, (_attrs, _c) in enumerate(re.findall(r"<td([^>]*)>(.*?)</td>", _row, re.S)):
                if "filler" in _attrs:
                    continue
                if not re.sub(r"<[^>]+>|\s|&nbsp;", "", _c):
                    _hit = _ths[_i] if _i < len(_ths) else "column %d" % (_i + 1)
                    break
            if _hit:
                break
        if _hit:
            failures.append("a table cell under %r is empty; a blank cannot say whether the "
                            "value is unchanged, missing or not applicable (gate 117)" % _hit)

    _ph = p.get("placeholders") or {}
    _gc118 = p.get("guidance_check") or {}

    def _scaled(sc):
        return str(sc or "").strip().lower() not in ("", "1", "10^0", "1.0")
    _bad118 = []
    for _i, _r in enumerate(_gc118.get("revisions") or [], 1):
        for _side in ("first", "latest"):
            _v = _ph.get("fy%d.%s" % (_i, _side))
            _t = _r.get(_side + "_text")
            if (_v and _t and str(_v) == str(_t) and re.search(r"\d", str(_t))
                    and _scaled(_r.get(_side + "_scale"))):
                _bad118.append("fy%d.%s" % (_i, _side))
    for _pre, _bk in (("fg", "scored"), ("fh", "other_horizon")):
        for _i, _r in enumerate(_gc118.get(_bk) or [], 1):
            _v = _ph.get("%s%d.guide" % (_pre, _i))
            _t = _r.get("guide_text")
            if (_v and _t and str(_v) == str(_t) and not _r.get("guide_display_text")
                    and re.search(r"\d", str(_t)) and _scaled(_r.get("guide_scale"))):
                _bad118.append("%s%d.guide" % (_pre, _i))
    if _bad118:
        failures.append("guide placeholder(s) %s resolve to the source's raw text of a scaled "
                        "guide; the figure is off by its SCALE (gate 118)" % ", ".join(_bad118))

    _txt119 = re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", doc)))
    _need119 = []
    _dv = [r for r in (p.get("deep_dive") or []) if r.get("non_operating_window")]
    _hm = [r for r in (p.get("headline_moves") or []) if r.get("non_operating_window")]
    _qs = [c for c in ((p.get("quarter_strip") or {}).get("cells") or [])
           if c.get("non_operating_window") and c.get("prev_growth") is not None
           and c.get("latest_growth") is not None]
    for _it in (p.get("non_operating_window") or []):
        if _dv or _hm:
            _need119.append("%s (%s) included %s" % (_it.get("period"), _it.get("role"),
                                                    _it.get("value_display")))
        elif _qs and _it.get("role") == "last quarter":
            _need119.append("%s included %s" % (_it.get("period"), _it.get("value_display")))
    _miss119 = [x for x in _need119 if x not in _txt119]
    if _miss119:
        failures.append("a displayed line below operating profit is measured against a quarter "
                        "with a non-operating item the page does not name: %s (gate 119)"
                        % "; ".join(_miss119))

    _RATE_NAME = re.compile(r"\b(sss|comps?|comparable|same[- ]store|organic|price|volume|"
                            r"mix|traffic|growth|rent change|contribution)\b", re.I)
    for _r in ((p.get("guidance_check") or {}).get("delivered") or []):
        if not _r.get("comparable") or _r.get("position") not in ("above", "below"):
            continue
        _v = str(_r.get("versus_display") or "")
        if _r.get("kind") == "pre_growth" or (_r.get("unit") == "bps"
                                               and _RATE_NAME.search(str(_r.get("name") or ""))):
            _want, _ok = "pp", bool(re.search(r"\d pp$", _v))
        elif _r.get("unit") == "bps" or _r.get("kind") == "ratio":
            _want, _ok = "bps", bool(re.search(r"\d bps$", _v))
        else:
            _want, _ok = "%", bool(re.search(r"\d%$", _v)) or not _r.get("guide_high")
        if not _ok:
            failures.append("%r: the gap to guidance reads %r; a %s row's gap is in %s "
                            "(gate 113)" % (_r.get("display") or _r.get("name"), _v,
                                            "rate" if _want == "pp" else
                                            "ratio" if _want == "bps" else "level", _want))

    def _qi(lbl):
        _m = re.match(r"^(?:R\d*-)?([1-4])Q(\d{2})$", str(lbl or "").strip(), re.I)
        return int(_m.group(2)) * 4 + int(_m.group(1)) - 1 if _m else None
    _seen112 = set()
    for _k in ("deep_dive", "accelerating", "decelerating", "primary_context",
               "rate_kpis", "headline_moves", "quiet_headline", "monitor"):
        for _r in p.get(_k) or []:
            if not isinstance(_r, dict) or _r.get("ltcode") in _seen112:
                continue
            _seen112.add(_r.get("ltcode"))
            _ps = _r.get("period_series") or []
            _gs = _r.get("growth_series") or []
            _as = _r.get("accel_series") or []
            _idx = [_qi(x) for x in _ps]
            if not _ps or any(i is None for i in _idx) or _r.get("flow_rate_mode"):
                continue
            _lag = 1 if _r.get("kind") == "pre_growth" else 4
            _bad = None
            for _t in range(len(_ps)):
                if _r.get("kind") != "pre_growth" and _t < len(_gs) and _gs[_t] is not None \
                        and (_t < 4 or _idx[_t] - _idx[_t - 4] != 4):
                    _bad = "y/y for %s is measured against %s" % (
                        _ps[_t], _ps[_t - 4] if _t >= 4 else "no quarter")
                if _t < len(_as) and _as[_t] is not None and (_t < 1 or _idx[_t] - _idx[_t - 1] != 1):
                    _bad = "change in pace for %s is differenced from %s" % (
                        _ps[_t], _ps[_t - 1] if _t >= 1 else "no quarter")
                if _bad:
                    break
            if _bad:
                failures.append("%r: the year-ago period is shifted - %s (gate 112)"
                                % (_r.get("display") or _r.get("name"), _bad))

    _ranked_keys = ("headline_moves", "accelerating", "decelerating", "primary_context")
    for _k in _ranked_keys:
        for _r in p.get(_k) or []:
            if _r.get("kind") == "pre_growth":
                failures.append("rate KPI %r sits in the %s table, where its reading prints "
                                "as y/y growth" % (_r.get("display") or _r.get("name"), _k))
    if (p.get("rate_kpis") or []) and "Rate KPI" not in _vis:
        failures.append("rate KPIs in the payload but no Rate KPIs table on the page")
    for _k in _ranked_keys[:3] + ("deep_dive",):
        for _r in p.get(_k) or []:
            if _r.get("period_basis") in ("unverified", "year_to_date"):
                failures.append("%r (period %s) is ranked in %s; a line that cannot prove "
                                "it is a quarter is not a quarterly inflection"
                                % (_r.get("display") or _r.get("name"), _r.get("period_basis"), _k))
    _CS = "$\u00a5\u20ac\u00a3"
    _gc = p.get("guidance_check") or {}
    _chk = []
    for _k in ("other_horizon", "scored", "forward_uncompared", "delivered"):
        for _g in _gc.get(_k) or []:
            _chk.append((_g.get("display"), _g.get("guide_text"), _g.get("guide_display_text")))
    for _g in _gc.get("revisions") or []:
        _chk.append((_g.get("display"), _g.get("first_text"), _g.get("first_display")))
        _chk.append((_g.get("display"), _g.get("latest_text"), _g.get("latest_display")))
    _sg = (p.get("quarter_strip") or {}).get("guide") or {}
    if _sg:
        _src = next((g.get("latest_text") for g in (_gc.get("revisions") or [])
                     if g.get("ltcode") == _sg.get("ltcode")), None)
        _chk.append((_sg.get("display"), _src, _sg.get("latest_display")))
    for _nm, _src, _disp in _chk:
        if _src and _disp and any(c in str(_src) for c in _CS) \
                and re.search(r"\d", str(_disp)) and not any(c in str(_disp) for c in _CS) \
                and not re.search(r"\b(USD|EUR|JPY|GBP|cents?)\b", str(_disp)):
            failures.append("money guide %r shows %r with no currency symbol (source %r)"
                            % (_nm, _disp, _src))
    _GBS = re.compile(r"accounts payable|current liabilit|current assets|total liabilit|total assets|"
                      r"raw materials|finished goods|work[- ]in[- ]process|\(balance sheet\)|"
                      r"segment assets|held for sale|^(19|20)\d\d\b|^there\s*after", re.I)
    for _k in ("accelerating", "decelerating", "headline_moves"):
        _rows = p.get(_k) or []
        for _r in _rows:
            _nm = str(_r.get("display") or _r.get("name") or "")
            if _GBS.search(_nm) or _GBS.search(str(_r.get("name") or "")):
                failures.append("%r in the %s table is a balance-sheet or schedule line" % (_nm, _k))
            if _r.get("unit_status") not in (None, "validated"):
                failures.append("%r in the %s table has an unresolved unit" % (_nm, _k))
        _mg = [r for r in _rows if r.get("kind") == "ratio"
               and re.search(r"margin", str(r.get("display") or r.get("name") or ""), re.I)]
        if len(_mg) > 2:
            failures.append("%s table carries %d margins; at most two" % (_k, len(_mg)))
    for _d in p.get("divergences") or []:
        _bt = ((_d.get("bridge") or {}).get("text") or "")
        if _d.get("rule") == "eps_outpacing_revenue" and _d.get("bridge_available") \
                and (not _bt or " ".join(_bt.split()) not in " ".join(_vis.split())):
            failures.append("EPS outgrowing revenue is stated without its bridge")
    if (p.get("sector_routing") or {}).get("template") == "reit":
        if re.search(r"Adjusted (Diluted )?EPS", _vis):
            failures.append("REIT note labels net earnings per share as Adjusted EPS")
    _shown = set()
    for _k in _ranked_keys + ("rate_kpis", "quiet_headline", "deep_dive"):
        for _r in p.get(_k) or []:
            _shown.add(str(_r.get("display") or _r.get("name") or ""))
    _BSUF = re.compile(r"\s*-\s*(?:[A-Z][\w&.]*\s+){0,3}share$|\s*-\s*owned\s*(?:&|and)\s*managed$", re.I)
    _stems = {re.sub(r"^%\s*", "", _BSUF.sub("", n)).strip().lower() for n in _shown if _BSUF.search(n)}
    for n in _shown:
        if not _BSUF.search(n) and "basis not given" not in n \
                and re.sub(r"^%\s*", "", n).strip().lower() in _stems:
            failures.append("%r prints beside a sibling that states its basis, with none of "
                            "its own" % n)

    def _band(v):
        return ("Very unusual" if v >= 2.5 else "Unusual" if v >= 1.5
                else "Notable" if v >= 0.75 else "Normal range")
    for _m in re.finditer(r"(Very unusual|Unusual|Notable|Normal range) \((\d+\.\d) SD\)",
                          " ".join(_vis.split())):
        if _band(float(_m.group(2))) != _m.group(1):
            failures.append("%r: the band word does not match the printed %s SD"
                            % (_m.group(0), _m.group(2)))
            break
    if "\u03c3" in _vis:
        _i = _vis.index("\u03c3")
        failures.append("the page prints the sigma sign (%r); write unusualness in "
                        "standard deviations as 'SD', e.g. 'a {{dd1.z}} SD move'"
                        % " ".join(_vis[max(0, _i - 40):_i + 5].split()))
    _spl = {e.get("ltcode") for e in (p.get("excluded") or [])
            if "changed what it measures" in str(e.get("reason") or "")}
    for _k in ("headline_moves", "accelerating", "decelerating", "primary_context", "deep_dive",
               "rate_kpis"):
        for _r in p.get(_k) or []:
            if _r.get("ltcode") in _spl:
                failures.append("%r is ranked in %s although its series changed what it "
                                "measures" % (_r.get("display") or _r.get("name"), _k))
    for _r in ((p.get("cash_conversion") or {}).get("rows") or []):
        if _r.get("ltcode") in _spl:
            failures.append("cash panel reads %r, whose series changed what it measures"
                            % (_r.get("label") or _r.get("display")))
    for _k in ("headline_moves", "accelerating", "decelerating"):
        for _r in p.get(_k) or []:
            if re.search(r"financing activit|\bcff\b", str(_r.get("name") or ""), re.I):
                failures.append("%r is ranked in %s; financing cash flow is how a quarter was "
                                "funded" % (_r.get("display") or _r.get("name"), _k))

    _GEN = {"growth", "change", "rate", "level", "quarter", "year", "basis", "company",
            "reported", "including", "excluding", "share", "line", "figure", "guide",
            "from", "with", "into", "over", "than", "this", "that", "was", "were"}
    _slot_names = {}
    for _k, _v in (_ph or {}).items():
        if _k.endswith(".name") and _v:
            _sl = _k[:-5]
            if re.match(r"^(dd\d|acc\d|dec\d|fg\d+|dg\d+|fy\d+|fh\d+|div\d\.m\d)$", _sl):
                _slot_names[_sl] = str(_v)
    _ALIAS = [(r"operating expense", " opex"), (r"\bcogs\b", " goods cost"),
              (r"cost of (goods|sales|revenue)", " cogs"), (r"free cash flow", " fcf"),
              (r"operating cash flow|cash from operat", " ocf"),
              (r"capital expend", " capex"), (r"\bd&a\b", " depreciation amortization"),
              (r"earnings per share", " eps"), (r"\beps\b", " earnings"),
              (r"annual recurring revenue", " arr"), (r"\barr\b", " annual recurring"),
              (r"\beop\b", " period end"), (r"period[- ]end", " eop"),
              (r"repurchase", " buyback"), (r"buyback", " repurchase"),
              (r"\bsg&a\b", " selling general administrative"),
              (r"\br&d\b", " research development")]

    def _w103(txt):
        t = str(txt or "")
        for _pat, _add in _ALIAS:
            if re.search(_pat, t, re.I):
                t += _add
        return {w[:6] for w in _words(t)} - {g[:6] for g in _GEN}
    _slot_words = {k: _w103(v) for k, v in _slot_names.items()}
    _slot_unique = {}
    for _k, _w in _slot_words.items():
        _oth = set().union(*[w for k2, w in _slot_words.items()
                             if _slot_names[k2].lower() != _slot_names[_k].lower()] or [set()])
        _slot_unique[_k] = _w - _oth
    _b103 = []
    for _k, _v in (narr_raw or {}).items():
        if isinstance(_v, (list, tuple)):
            _b103 += [(f"{_k}[{j}]", str(x)) for j, x in enumerate(_v, 1)]
        elif isinstance(_v, str):
            _b103.append((_k, _v))
    for _lab, _txt in _b103:
        for _sent in re.split(r"(?<=[.;])\s+", _txt):
            _toks = re.findall(r"\{\{\s*[a-zA-Z0-9_.]+\s*\}\}|[A-Za-z]+", _sent)
            for _i, _t in enumerate(_toks):
                _m = re.match(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}", _t)
                if not _m:
                    continue
                _full = _m.group(1)
                _sl = next((s2 for s2 in sorted(_slot_names, key=len, reverse=True)
                            if _full.startswith(s2 + ".")), None)
                if not _sl or _sl.startswith("dd") or _full.endswith(".name") \
                        or _full.endswith(".adjusted_name"):
                    continue
                if _full.endswith(".adjusted") and ("{{%s.adjusted_name}}" % _sl) in _sent:
                    continue
                _own = _slot_unique[_sl] or _slot_words[_sl]
                if not _own:
                    continue
                _pool = {k2: w for k2, w in _slot_words.items()
                         if not re.match(r"f[yh]\d", k2) or re.match(r"f[yh]\d", _sl)}
                _vocab = set().union(*_pool.values()) if _pool else set()
                _verdict, _run, _started, _gap = None, set(), False, 0
                for _t2 in reversed(_toks[:_i]):
                    _m2 = re.match(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}", _t2)
                    if _m2:
                        if _m2.group(1).startswith(_sl + ".") and not _started:
                            _verdict = "ok"
                        break
                    _w = _w103(_t2)
                    if not _w:
                        continue
                    _w = next(iter(_w))
                    if _w in _vocab:
                        _run.add(_w)
                        _started, _gap = True, 0
                    elif _started:
                        _gap += 1
                        if _gap > 1:
                            break
                def _judge(run):
                    if not run:
                        return None
                    def _sc(k2):
                        w = _slot_words[k2]
                        return (len(run & w), bool(w) and w <= run)
                    mine = _sc(_sl)
                    riv = [(_sc(k2), k2) for k2 in _pool
                           if _slot_names[k2].lower() != _slot_names[_sl].lower()]
                    top = max(riv, default=((0, False), None))
                    if top[0][0] > mine[0] or (top[0][0] == mine[0] and top[0][1] and not mine[1]
                                               and mine[0] > 0):
                        return top[1]
                    if mine[0] == 0:
                        return None
                    return "S" if mine[0] > top[0][0] else "tie"
                if _verdict is None:
                    _b = _judge(_run)
                    _frun, _fst = set(), False
                    for _t2 in _toks[_i + 1:_i + 7]:
                        if re.match(r"\{\{", _t2):
                            break
                        _w = _w103(_t2)
                        if not _w:
                            continue
                        _w = next(iter(_w))
                        if _w in _vocab:
                            _frun.add(_w)
                            _fst = True
                        elif _fst:
                            break
                    _f = _judge(_frun)
                    if _b in ("S", "tie"):
                        _verdict = "ok"
                    elif _b is not None:
                        _verdict = "ok" if _f == "S" else _slot_names[_b]
                    elif _f is not None:
                        _verdict = "ok" if _f in ("S", "tie") else _slot_names[_f]
                if _verdict is None and not (_w103(re.sub(r"\{\{[^}]*\}\}", " ", _sent))
                                             & _slot_words[_sl]):
                    _verdict = "unnamed"
                if _verdict not in (None, "ok"):
                    failures.append(
                        "narrative %s prints {{%s}}, a figure for %r, %s. A figure must "
                        "sit next to the name it belongs to; {{%s.name}} always binds"
                        % (_lab, _full, _slot_names[_sl],
                           "in a sentence that never names it" if _verdict == "unnamed"
                           else "after naming %r" % _verdict, _sl))

    if args.pdf and os.path.exists(args.pdf):
        _pt = "\f".join(pdf_pages_text(args.pdf)) or None
        if _pt is not None:
            _pdf_nums = set(re.findall(r"[-+]?\$?\d[\d,]*\.?\d*%?", _pt))
            _html_nums = [t for t in re.findall(r"[-+]?\$?\d[\d,]*\.?\d*%?", _vis)
                          if len(t.strip("+-$%")) >= 3]
            _lost = sorted({t for t in _html_nums if t not in _pdf_nums})
            if _lost:
                failures.append("%d figure(s) printed on the HTML page are missing from the PDF "
                                "(clipped or off the page): %s" % (len(_lost), ", ".join(_lost[:6])))

    for _j, _b in enumerate((narr_raw or {}).get("guidance") or [], 1):
        if re.search(r"\{\{\s*dd\d\.threshold\s*\}\}", str(_b)):
            failures.append("narrative guidance[%d] carries an InSync continuation level "
                            "({{ddN.threshold}}); continuation levels are arithmetic, not "
                            "guidance, and belong in What to Monitor Next" % _j)

    _deb = str((narr_raw or {}).get("debate") or "").strip()
    if _deb:
        if not _deb.endswith("?"):
            failures.append("narrative 'debate' must be a single question ending in '?'")
        if re.search(r"\b(should|we think|we believe|likely|expect|attractive|cheap|expensive)\b", _deb, re.I):
            failures.append("narrative 'debate' states a view; it must pose the question the "
                            "figures raise")
        if not re.search(r"\{\{", _deb):
            failures.append("narrative 'debate' carries no figure; ground the question in the "
                            "page's own numbers")

    for _tb in re.findall(r"<table[^>]*>(.*?)</table>", doc, re.S):
        _hd = [re.sub(r"<[^>]+>", "", h).strip() for h in re.findall(r"<th[^>]*>(.*?)</th>", _tb, re.S)]
        if "Current Level Direction" not in _hd or "Current YoY Change" not in _hd:
            continue
        _iy, _il = _hd.index("Current YoY Change"), _hd.index("Current Level Direction")
        for _tr in re.findall(r"<tr>(.*?)</tr>", _tb, re.S):
            _cells = [html_lib.unescape(re.sub(r"<[^>]+>", " ", c)).strip()
                      for c in re.findall(r"<td[^>]*>(.*?)</td>", _tr, re.S)]
            if len(_cells) <= max(_iy, _il):
                continue
            _y, _l = _cells[_iy], _cells[_il].lower()
            _up = re.search(r"\b(higher|growing|widening|rising)\b", _l)
            _dn = re.search(r"\b(lower|declining|narrowing|falling)\b", _l)
            if (_y.startswith("+") and _dn and not _up) or (_y.startswith("-") and _up and not _dn):
                failures.append("Current Level Direction %r contradicts the y/y change %r in row %r"
                                % (_cells[_il], _y, _cells[0][:40]))

    _vis = html_lib.unescape(re.sub(r"<[^>]+>", " ", doc))
    for _k in ("accelerating", "decelerating", "primary_context"):
        for _r in p.get(_k) or []:
            _sb = _r.get("small_base") or {}
            _nm = str(_r.get("display") or _r.get("name") or "")
            if _sb.get("base_display") and _sb.get("typical_display") \
                    and re.search(r">\s*" + re.escape(html_lib.escape(_nm, quote=False)) + r"\s*<", doc) \
                    and "Small base." in _vis:
                pass
            elif _sb.get("base_display") and _sb.get("typical_display") \
                    and re.search(r">\s*" + re.escape(html_lib.escape(_nm, quote=False)) + r"\s*<", doc) \
                    and "Small base." not in _vis:
                failures.append("%r is shown with growth off a small base (%s in %s against about %s) "
                                "and the page does not say so"
                                % (_nm, _sb["base_display"], _sb.get("base_period"), _sb["typical_display"]))
    for _c in (p.get("source_corrections") or [])[:1]:
        if "Source correction." not in _vis:
            failures.append("the source's %s labels were exchanged to make the parts add up, "
                            "and the page does not say so" % _c.get("metric"))
    for _c in ((p.get("guidance_check") or {}).get("component_checks") or [])[:1]:
        if "Guide components do not add up" not in _vis:
            failures.append("the %s guide components sum to about %s against a total guide of %s, "
                            "and the page does not say so" % (_c.get("metric"), _c.get("implied_display"),
                                                              _c.get("total_text")))

    _ph = p.get("placeholders") or {}
    if narr_raw:
        def _named(txt, j):
            nm = str(_ph.get("dd%d.name" % j) or "")
            words = [w for w in re.findall(r"[a-z]{4,}", nm.lower())
                     if w not in ("insync", "calculated", "adjusted", "total", "margin")]
            low = txt.lower()
            return ("{{dd%d.name}}" % j) in txt or (nm and nm.lower() in low) or \
                (words and all(w in low for w in words[:2]))
        for _i in (1, 2, 3, 4, 5):
            _txt = narr_raw.get("dd%d" % _i)
            if not isinstance(_txt, str) or not _txt.strip():
                continue
            _own = _named(_txt, _i)
            for _j in sorted({int(m) for m in re.findall(r"\{\{dd(\d)\.", _txt)} - {_i}):
                if ("{{dd%d.name}}" % _j) in _txt and not _own:
                    failures.append("narrative dd%d is written about dd%d's line (%s), not its own"
                                    % (_i, _j, _ph.get("dd%d.name" % _j)))
                elif not _named(_txt, _j):
                    failures.append("narrative dd%d carries a dd%d figure without naming %s"
                                    % (_i, _j, _ph.get("dd%d.name" % _j)))

    _shadow = {e.get("ltcode") for e in (p.get("excluded") or [])
               if "the company reports this measure itself" in str(e.get("reason") or "")}
    for _k in ("headline_moves", "accelerating", "decelerating", "deep_dive"):
        for _r in p.get(_k) or []:
            if _r.get("ltcode") in _shadow:
                failures.append("%r is an InSync calculation ranked in %s although the company "
                                "reports the same measure" % (_r.get("display") or _r.get("name"), _k))

    _aq_blocks = " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", x)) for x in
                          re.findall(r'<b>Adjustment quality\.</b>.*?</p>|<div class="qs-x">.*?</div>', doc, re.S))
    _aq_blocks = " ".join(_aq_blocks.split())
    for _e in (p.get("adjustment_quality") or []):
        if _e.get("size_display") and not (_e["size_display"] in _aq_blocks
                                           and str(_e.get("label") or "") in _aq_blocks):
            failures.append("adjustment quality for %s (%s from %s) is not printed"
                            % (_e.get("display"), _e.get("size_display"), _e.get("label")))
    if narr_raw:
        for _i in (1, 2, 3, 4, 5):
            if ("dd%d.effect" % _i) in _ph:
                _txt = narr_raw.get("dd%d" % _i)
                if isinstance(_txt, str) and ("{{dd%d.effect}}" % _i) not in _txt:
                    failures.append("narrative dd%d is about %s, whose move includes %s, and does "
                                    "not say so: write {{dd%d.effect}} into the paragraph"
                                    % (_i, _ph.get("dd%d.name" % _i), _ph["dd%d.effect" % _i], _i))

    _PDF_CHECKS_RAN = None
    if args.pdf and not os.path.exists(args.pdf):
        _PDF_CHECKS_RAN = False
    if args.pdf and os.path.exists(args.pdf):
        _PDF_CHECKS_RAN = bool(pdf_pages_text(args.pdf))
        if not _PDF_CHECKS_RAN:
            warnings.append("PDF text checks did not run: pdftotext is not installed, so gate 104 "
                            "(every figure on the page is in the PDF) and the PDF hygiene read were "
                            "skipped. Install poppler-utils, or check the PDF by eye before release.")
    out = {"passed": not failures, "failures": failures, "warnings": warnings,
           "checks_run": GATE_COUNT, "numeric_tokens_on_page": len(page),
           "pdf_checks_run": _PDF_CHECKS_RAN,
           "provenance_rows": len(prov_codes),
           "displayed_metrics": len(displayed)}
    if getattr(args, "json_out", None):
        os.makedirs(os.path.dirname(os.path.abspath(args.json_out)) or ".", exist_ok=True)
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2)
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        if failures:
            print("FAIL")
            for f in failures:
                print(f"  - {f}")
        else:
            print("PASS  all gates clear")
        for w in warnings:
            print(f"  ! {w}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _contract import run as _run_contract
    _run_contract(main, 'format_lint')
