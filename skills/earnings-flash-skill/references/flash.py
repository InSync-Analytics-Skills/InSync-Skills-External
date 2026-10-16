#!/usr/bin/env python3
"""
flash.py - v11 run driver for the Earnings Flash skill. It works the same under any LLM that can
call the InSync MCP and run Python.

The model does three things: send the MCP calls this script prints, save each response as it
came back, and write the narrative. Everything else - the call arguments, the period, the
KPI ladder, the rate limit, the provenance record, input.json, the checks and the build - is
done here, so nothing depends on the model remembering a rule.

  python3 flash.py start TICKER [--period 3Q26] [--dir DIR]   begin a run
  python3 flash.py next                                        what to do now (always safe to run)
  python3 flash.py save NAME FILE                              store the MCP response for step NAME
  python3 flash.py set KEY VALUE                               record a fact the model read (currency, country, guidance_source)
  python3 flash.py parsed KIND PATH RESULT [TEXTFILE]          record a document parse attempt
  python3 flash.py assemble                                    write input.json
  python3 flash.py facts                                       probe run -> the fact sheet the narrative is written from
  python3 flash.py template                                    write a rule-based narrative.json (fallback)
  python3 flash.py build [OUT.docx] [flags]                    render; flags pass through to run_pipeline.py
  python3 flash.py verify OUT.docx                             check the rendered page
  python3 flash.py eightk FILE                                 8-K actuals, only on the user's word (errors.md)
  python3 flash.py guides FILE                                 release day: tag the guides read off the document

State lives in DIR (default ./flash_TICKER). FILE may be "-" to read the response from stdin.
Every command prints a line starting NEXT: that says exactly what to do after it.
"""
import datetime as _dt
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_math

SPINE = list(dict.fromkeys(lt for m in run_math.FINANCIAL_METRICS for lt in m["ltcodes"]))
RATE_LIMIT_TOOLS = 25
MAX_KPIS = 8
KPI_FETCH = 12
GUIDED_CONSENSUS_MAX = 10
CONSENSUS_WINDOW_DAYS = (90, 120)
LATEST_N = 3
PAIRS_PER_REQUEST = 30
KPI_TARGET_ROWS = 4
TIERS = ("top_kpi", "company_kpi", "industry_kpi", "keyword")
KEYWORDS = ("segment revenue, orders, bookings, backlog, volume, units, subscribers, members, "
            "stores, comparable sales, occupancy, utilization")
_CCY_TOKENS = ("USD", "US$", "EUR", "GBP", "JPY", "KRW", "CNY", "RMB", "INR", "CHF", "CAD", "AUD",
               "HKD", "SEK", "NOK", "DKK", "BRL", "MXN", "ZAR", "TWD", "SGD")
_COUNTRY_BY_REGION = {"US": "United States of America"}
REVENUE_EX_ONE_OFF = "IS_P_102"
_EUR = ("Austria", "Belgium", "Finland", "France", "Germany", "Greece", "Ireland", "Italy",
        "Luxembourg", "Netherlands", "Portugal", "Spain")
_HOME_CCY = dict({c: "EUR" for c in _EUR}, **{
    "United States of America": "USD", "United Kingdom": "GBP", "Switzerland": "CHF", "Sweden": "SEK",
    "Norway": "NOK", "Denmark": "DKK", "Japan": "JPY", "Australia": "AUD", "Canada": "CAD",
    "South Korea": "KRW", "China": "CNY", "Hong Kong": "HKD", "Taiwan": "TWD", "India": "INR",
    "Singapore": "SGD", "Brazil": "BRL", "Mexico": "MXN", "South Africa": "ZAR", "Israel": "ILS",
    "Hungary": "HUF", "Poland": "PLN"})


def _dir_from_args(argv):
    if "--dir" in argv:
        return argv[argv.index("--dir") + 1]
    return os.environ.get("FLASH_DIR")


def _find_state_dir(argv):
    d = _dir_from_args(argv)
    if d:
        return d
    cands = [x for x in os.listdir(".") if x.startswith("flash_") and os.path.isfile(os.path.join(x, "state.json"))]
    if len(cands) == 1:
        return cands[0]
    if not cands:
        _die("no run found here. Start one: python3 flash.py start TICKER")
    _die("several runs here (%s). Pass --dir DIR." % ", ".join(sorted(cands)))


def _load(d):
    with open(os.path.join(d, "state.json"), encoding="utf-8") as f:
        return json.load(f)


def _save_state(d, st):
    with open(os.path.join(d, "state.json"), "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1)


def _resp(d, name):
    p = os.path.join(d, "responses", name + ".json")
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _die(msg, code=2):
    print("STOP: " + msg)
    sys.exit(code)


def _today():
    return _dt.date.today()


def _iso(d):
    return d.isoformat()


def _split_tools(obj):
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except ValueError:
            return {}
    if isinstance(obj, dict) and any(k.startswith(("get_", "search_", "parse_")) for k in obj):
        return {k: v for k, v in obj.items() if k.startswith(("get_", "search_", "parse_"))}
    return {"_single": obj}


def _one(d, name, tool):
    r = _resp(d, name)
    if r is None:
        return None
    parts = _split_tools(r)
    return parts.get(tool) or parts.get("_single")


def _rows(payload):
    return run_math._rows(run_math._unwrap_mcp_payload(run_math.expand_short_keys(payload))) if payload else []


def _meta(payload):
    return run_math._meta(run_math._unwrap_mcp_payload(payload)) if payload else {}


def _lineitems(payload):
    out = []

    def walk(x):
        if isinstance(x, dict):
            lt = x.get("ltcode") or x.get("LTCODE")
            if lt and isinstance(lt, str):
                out.append({"ltcode": lt, "name": x.get("name") or x.get("LINEITEM") or x.get("lineitem") or lt,
                            "tags": x.get("tags") or ""})
                return
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(payload)
    seen, uniq = set(), []
    for c in out:
        if c["ltcode"] not in seen:
            seen.add(c["ltcode"])
            uniq.append(c)
    return uniq


def _bare(period):
    return re.sub(r"^R\d*-", "", str(period or ""))


def prior_label(period):
    p = str(period)
    m = re.match(r"^(R\d*-)(.*)$", p)
    if m:
        p = m.group(2)
    m = re.match(r"^(\d[QH]|\dM|FY)(\d{2})$", p)
    if m:
        return m.group(1) + "%02d" % ((int(m.group(2)) - 1) % 100)
    m = re.match(r"^(\d{4})$", p)
    if m:
        return str(int(p) - 1)
    return None


def next_quarter(period):
    m = re.match(r"^(\d)Q(\d{2})$", _bare(period))
    if not m:
        return None
    q, y = int(m.group(1)), int(m.group(2))
    return "%dQ%02d" % (1, y + 1) if q == 4 else "%dQ%02d" % (q + 1, y)


def fiscal_year_of(period, forward=False):
    b = _bare(period)
    m = re.search(r"(\d{2})$", b)
    if not m:
        return None
    y = int(m.group(1))
    if forward and re.match(r"^(4Q\d{2}|2H\d{2}|\d{4})$", b):
        y += 1
    return "20%02d" % y


def _is_derived(lt):
    return run_math.is_derived_ltcode(lt)


def _ccy_in_label(name):
    up = str(name).upper()
    for t in _CCY_TOKENS:
        if re.search(r"[\(\s/]" + re.escape(t) + r"[\s/\)MBK]", up + " "):
            return "USD" if t == "US$" else t
    return None


def _call(tool, **args):
    return {"tool_name": tool, "tool_arguments": {k: v for k, v in args.items() if v is not None}}


def _envelope(purpose, calls):
    return {"mode": "execute", "task_query": purpose, "tool_calls": calls}


def _print_call(name, purpose, calls, note=None, save=True):
    print("CALL %s - send this to insync_assistant exactly as written:" % name)
    print(json.dumps(_envelope(purpose, calls), indent=1))
    if note:
        print("NOTE: " + note)
    if save:
        if any(c["tool_name"] in ("get_actuals", "get_estimates") for c in calls):
            mark = os.path.join(_RUN_DIR[0], "format_shown") if _RUN_DIR[0] else None
            print("(get_actuals / get_estimates parts may be written as pipe rows, as described earlier.)"
                  if mark and os.path.exists(mark) else PIPE_FORMAT)
            if mark:
                open(mark, "w").close()
        print("NEXT: save the whole response to a file and run: python3 flash.py save %s <file>" % name)


def _rate_wait(st):
    now = time.time()
    recent = [c for c in st.get("calls", []) if now - c["t"] < 60]
    used = sum(c["n"] for c in recent)
    if used < RATE_LIMIT_TOOLS:
        return 0
    return int(60 - (now - min(c["t"] for c in recent))) + 1


def _kpi_candidates(d, st):
    out, seen, dropped = [], set(), []
    rep_ccy = (st.get("facts") or {}).get("currency")
    for tier in TIERS:
        r = _resp(d, "kpis_" + tier.replace("_kpi", ""))
        if r is None:
            continue
        for c in _lineitems(_one(d, "kpis_" + tier.replace("_kpi", ""), "search_lineitem")):
            lt = c["ltcode"]
            if lt in seen:
                continue
            if _is_derived(lt):
                dropped.append("%s (%s): derived series" % (c["name"], lt))
                continue
            if tier == "keyword" and not lt.startswith(("SD_P_", "KM_P_", "SD_S_", "KM_S_")):
                continue
            fx = _ccy_in_label(c["name"])
            seen.add(lt)
            out.append({"ltcode": lt, "name": c["name"], "tier": tier,
                        "foreign": bool(fx and rep_ccy and fx != rep_ccy)})
    return out, dropped


def _kpi_fetch_names(d):
    rd = os.path.join(d, "responses")
    extra = sorted(x[:-5] for x in (os.listdir(rd) if os.path.isdir(rd) else [])
                   if x.endswith(".json") and x.startswith(("kpi_extra", "fx_kpis")))
    return ["fetch"] + extra


def _fetched_codes(d):
    codes = set()
    for name in _kpi_fetch_names(d):
        for tool in ("get_actuals", "get_estimates"):
            for r in _rows(_one(d, name, tool)):
                if r.get("LTCODE"):
                    codes.add(r["LTCODE"])
    return codes


def _requested(st):
    return list(st.get("requested") or [])


def _qualifying(d, st, cands):
    per = st["period"]
    act, est = {}, {}
    for name in _kpi_fetch_names(d):
        for r in _rows(_one(d, name, "get_actuals")):
            if str(r.get("FISCALPERIOD")) == per and run_math.parse_mcp_value(r.get("VALUE")) is not None:
                act[r["LTCODE"]] = True
        for r in _rows(_one(d, name, "get_estimates")):
            if _bare(r.get("FISCALPERIOD")) == _bare(per) and run_math.parse_mcp_value(r.get("VALUE")) is not None:
                try:
                    est[r["LTCODE"]] = int(float(r.get("BROKERCOUNT") or 0))
                except (TypeError, ValueError):
                    est[r["LTCODE"]] = 0
    return act, est


def kpi_mapping(d, st):
    cands, _ = _kpi_candidates(d, st)
    act, est = _qualifying(d, st, cands)
    ordered = []
    for tier in TIERS:
        tc = [c for c in cands if c["tier"] == tier and c["ltcode"] in act]
        if tier != "top_kpi":
            tc = sorted(tc, key=lambda c: -est.get(c["ltcode"], -1))
        ordered += tc
    ordered = [c for c in ordered if c["ltcode"] in est] + [c for c in ordered if c["ltcode"] not in est]
    km = {"ticker": st["ticker"]}
    for i, c in enumerate(ordered[:MAX_KPIS], 1):
        km["top_kpi_%d" % i] = c["name"]
        km["top_kpi_%d_ltcode" % i] = c["ltcode"]
        km["top_kpi_%d_tier" % i] = c["tier"]
    if not ordered:
        km["no_kpis_available"] = True
        km["tiers_checked"] = ", ".join(t for t in TIERS if _resp(d, "kpis_" + t.replace("_kpi", "")) is not None)
    n_graded = len([c for c in ordered if c["ltcode"] in est])
    return km, n_graded


def _guide_list(payload):
    if not payload:
        return []
    r = run_math._unwrap_mcp_payload(run_math.expand_short_keys(payload)) or {}
    dd = r.get("data")
    if isinstance(dd, dict):
        return [x for x in (dd.get("guidance") or []) if isinstance(x, dict)]
    return [x for x in (dd or []) if isinstance(x, dict)] if isinstance(dd, list) else []


def _guided_codes(d, st):
    import narrative_template as nt
    rows = _guide_list(_merged_guidance(d))
    sps = nt.source_periods_newest_first(rows)
    per = st.get("period")
    stale = _guidance_state(d, st) != "current"
    if not sps:
        if not stale:
            return [], []
        codes = ["IS_P_101", "IS_P_128", "IS_P_127"]
        return codes, [p for p in (next_quarter(per), fiscal_year_of(per, forward=True), fiscal_year_of(per)) if p]
    newest = [r for r in rows if r.get("SOURCE_PERIOD") == sps[0]]
    board = set(SPINE) | {"IS_P_102"} | set(c["ltcode"] for c in _kpi_candidates(d, st)[0])
    codes = []
    for r in newest:
        lt = r.get("LTCODE")
        if lt and (not _is_derived(lt) or str(lt).endswith("-Y")) and lt not in codes:
            codes.append(lt)
    on_board = lambda c: c in board or (str(c).endswith("-Y") and str(c)[:-2] in board)
    picked = ([c for c in codes if on_board(c)] + [c for c in codes if not on_board(c)])[:GUIDED_CONSENSUS_MAX]
    periods = []
    for r in newest:
        fp = str(r.get("FISCALPERIOD") or "")
        if r.get("LTCODE") in picked and re.match(r"^(\d[QH]\d{2}|\d{4})$", fp) and fp not in periods:
            periods.append(fp)
    if stale:
        for p in (next_quarter(per), fiscal_year_of(per), fiscal_year_of(per, forward=True)):
            if p and p not in periods:
                periods.append(p)
    return picked, periods


def _guided_pairs(d, st, codes, periods):
    import narrative_template as nt
    if _guidance_state(d, st) != "current":
        return None
    rows = _guide_list(_merged_guidance(d))
    sps = nt.source_periods_newest_first(rows)
    if not sps:
        return None
    pairs = []
    for r in rows:
        if r.get("SOURCE_PERIOD") != sps[0]:
            continue
        lt, fp = str(r.get("LTCODE") or ""), str(r.get("FISCALPERIOD") or "")
        for code in (lt, re.sub(r"-(Y|Q|M)$", "", lt)):
            if code in codes and fp in periods and (code, fp) not in pairs:
                pairs.append((code, fp))
    return pairs


def _latest_requests(tid, pairs, cd):
    by_fp = {}
    for lt, fp in pairs:
        by_fp.setdefault(fp, [])
        if lt not in by_fp[fp]:
            by_fp[fp].append(lt)
    groups = {}
    for fp, lts in by_fp.items():
        groups.setdefault(tuple(lts), []).append(fp)
    reqs = []
    for lts, fps in groups.items():
        if len(lts) > PAIRS_PER_REQUEST:
            chunks = [(list(lts[i:i + PAIRS_PER_REQUEST]), [fp]) for fp in fps
                      for i in range(0, len(lts), PAIRS_PER_REQUEST)]
        else:
            k = max(1, PAIRS_PER_REQUEST // len(lts))
            chunks = [(list(lts), fps[i:i + k]) for i in range(0, len(fps), k)]
        for c_lts, c_fps in chunks:
            reqs.append(dict(ticker_id=str(tid), ltcode=",".join(c_lts), fiscal_period=",".join(c_fps),
                             latest={"enabled": True, "limit": LATEST_N}, updatedate_end=cd, limit="100"))
    return reqs


def _fetch2_names(d, st):
    names = ["fetch2"] + ["fetch2_part%d" % i for i in range(2, 2 + len(st.get("fetch2_parts") or []))]
    rd = os.path.join(d, "responses")
    more = [n[:-5] for n in (os.listdir(rd) if os.path.isdir(rd) else []) if n.startswith("fetch2_more_")]
    names += sorted(more, key=lambda n: tuple(int(x) for x in n.split("_")[2:]))
    return [n for n in names if _resp(d, n) is not None]


def _est_parts(d, name):
    r = _resp(d, name)
    if r is None:
        return []
    parts = _split_tools(r)
    keys = sorted((k for k in parts if k == "get_estimates" or k.startswith("get_estimates_")),
                  key=lambda k: int(k.rsplit("_", 1)[1]) if k[-1].isdigit() else 1)
    return [parts[k] for k in keys] or ([parts["_single"]] if "_single" in parts else [])


def _hb(st):
    return "fetch2_hist" if st.get("fetch2_mode") == "history" else "fetch2"


def _latest_parts(d, st):
    reqs = st.get("fetch2_reqs") or []
    firsts = _est_parts(d, "fetch2") + [p for i in range(2, 2 + len(st.get("fetch2_parts") or []))
                                        for p in _est_parts(d, "fetch2_part%d" % i)]
    out = []
    for j, first in enumerate(firsts[:len(reqs)]):
        rows, page, last = list(_rows(first)), 1, first
        while _resp(d, "fetch2_more_%d_%d" % (j, page + 1)) is not None:
            page += 1
            last = (_est_parts(d, "fetch2_more_%d_%d" % (j, page)) or [None])[0]
            rows += _rows(last)
        out.append((reqs[j], rows, last, page))
    return out


def _latest_broken(d, st):
    for a, rows, _, _ in _latest_parts(d, st):
        for r in rows:
            day = str(r.get("UPDATEDATE") or r.get("UPDATE_DATE") or "")[:10]
            if day > a["updatedate_end"]:
                return "a consensus update dated %s, after the call date %s" % (day, a["updatedate_end"])
    return None


def _window_rows(rows, cd):
    c0 = _dt.date.fromisoformat(cd)
    s90 = _iso(c0 - _dt.timedelta(days=CONSENSUS_WINDOW_DAYS[0]))
    s120 = _iso(c0 - _dt.timedelta(days=CONSENSUS_WINDOW_DAYS[1]))

    def day(r):
        return str(r.get("UPDATEDATE") or r.get("UPDATE_DATE") or "")[:10]

    def key(r):
        return (r.get("LTCODE"), _bare(r.get("FISCALPERIOD")))
    n = {}
    for r in rows:
        if s90 <= day(r) <= cd:
            n[key(r)] = n.get(key(r), 0) + 1
    thin = [k for k in n if n[k] < 2]
    wide = {(lt, fp) for lt in {k[0] for k in thin} for fp in {k[1] for k in thin}}
    return [r for r in rows if (s120 if key(r) in wide else s90) <= day(r) <= cd]


def _pinned_rows(d):
    st = _load(d) if os.path.isfile(os.path.join(d, "state.json")) else {}
    if st.get("fetch2_mode") == "latest":
        rows = [r for n in _fetch2_names(d, st) for p in _est_parts(d, n) for r in _rows(p)]
        pos = {lt: i for i, lt in enumerate(st["fetch2_est_args"]["ltcode"].split(","))}
        rows.sort(key=lambda r: (str(r.get("UPDATEDATE") or "")[:10], pos.get(r.get("LTCODE"), len(pos)),
                                 str(r.get("FISCALPERIOD") or "")))
        return _window_rows(rows, st["fetch2_est_args"]["updatedate_end"])
    rows = []
    i = 1
    while True:
        name = _hb(st) if i == 1 else _hb(st) + "_page%d" % i
        if _resp(d, name) is None and i > 1:
            break
        rows += _rows(_one(d, name, "get_estimates"))
        i += 1
    wide = _rows(_one(d, _hb(st) + "_wide", "get_estimates"))
    i = 2
    while _resp(d, _hb(st) + "_wide_page%d" % i) is not None:
        wide += _rows(_one(d, _hb(st) + "_wide_page%d" % i, "get_estimates"))
        i += 1
    if wide:
        wk = {(r.get("LTCODE"), _bare(r.get("FISCALPERIOD"))) for r in wide}
        rows = [r for r in rows if (r.get("LTCODE"), _bare(r.get("FISCALPERIOD"))) not in wk] + wide
    return rows


def _thin_consensus_keys(d, st):
    if st.get("fetch2_mode") == "latest":
        return []
    a = st.get("fetch2_est_args") or {}
    cd = a.get("updatedate_end")
    if not cd:
        return []
    n = {}
    b = _hb(st)
    for r in _rows(_one(d, b, "get_estimates")) + [x for i in range(2, 50)
                                                   for x in _rows(_one(d, b + "_page%d" % i, "get_estimates"))]:
        if str(r.get("UPDATEDATE") or r.get("UPDATE_DATE") or "")[:10] <= cd:
            k = (r.get("LTCODE"), _bare(r.get("FISCALPERIOD")))
            n[k] = n.get(k, 0) + 1
    want = [(lt, fp) for lt in a.get("ltcode", "").split(",") for fp in a.get("fiscal_period", "").split(",")]
    return [k for k in want if 0 < n.get(k, 0) < 2]


def _guidance_state(d, st):
    g = _one(d, "fetch", "get_guidance")
    if g is None:
        return None
    try:
        warns, status = run_math.guidance_freshness_gate(run_math.expand_short_keys(_merged_guidance(d)),
                                                         st.get("call_date"))
    except Exception:
        return "unknown"
    state = (status or {}).get("state") or "ok"
    text = " ".join(warns or [])
    if state == "empty" or "[no guidance rows]" in text:
        return "empty"
    if state == "stale" or "[guidance predates print]" in text:
        return "stale"
    return "current"


def _merged_guidance(d):
    base = _one(d, "fetch", "get_guidance")
    if base is None:
        return None
    base = json.loads(json.dumps(base))
    data = run_math._unwrap_mcp_payload(base)
    i = 2
    while True:
        extra = _one(d, "guidance_page%d" % i, "get_guidance")
        if extra is None:
            break
        ed = run_math._unwrap_mcp_payload(extra)
        for key in ("guidance", "outlook"):
            src = (ed.get("data") or {}).get(key) if isinstance(ed.get("data"), dict) else None
            if src:
                data.setdefault("data", {}).setdefault(key, []).extend(src)
        data["meta"] = ed.get("meta", data.get("meta"))
        i += 1
    return data


_DOC_RUNGS = {
    "release": [("earnings release", "press release", "financial summary"),
                ("guidance", "outlook", "shareholder letter", "letter to shareholders", "commentary",
                 "prepared remarks", "supplemental", "production report")],
    "transcript": [("transcript",), ("presentation",), ("10-q", "10q", "10-k", "10k", "8-k", "8k")],
}
_PARSE_PROMPTS = ("Extract the full text of this document verbatim. Include all content.",
                  "Return the raw text of every page in this document as a single string. "
                  "Do not summarize, do not classify, do not interpret.")
MAX_PARSES_PER_DOC, MAX_PARSES_TOTAL = 2, 4
_PARSE_JUDGE = ("Do NOT use flash.py save for this one. Judge the reply first:\n"
                "  - refusal: it says it cannot or will not extract (e.g. 'outside document extraction scope')\n"
                "  - empty: no text, or an empty data field\n"
                "  - error: it stops mid-sentence or mid-table, is missing the tables or outlook the document\n"
                "    should carry, or is about another company. (Do not judge by file size: a PDF's text is\n"
                "    always far smaller than the file.)\n"
                "  - timeout: no reply came back (a parse that has not returned has failed: move on)\n"
                "Then: python3 flash.py parsed %s \"%s\" success [FILE]\n"
                "      FILE is the file the app saved this reply to, if it shows one. Otherwise leave FILE\n"
                "      off: do NOT copy the text into a file - you already have it above.\n"
                "or:   python3 flash.py parsed %s \"%s\" refusal|empty|error|timeout")


def _doc_candidates(d, kind):
    tree = _one(d, "fetch2", "get_document_tree")
    files = []

    def walk(x):
        if isinstance(x, dict):
            if x.get("file_path"):
                files.append(x)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(tree)
    out, seen = [], set()
    for group in _DOC_RUNGS[kind]:
        hits = [f for f in files if f["file_path"] not in seen
                and any(w in str(f.get("file_name") or f["file_path"]).lower() for w in group)]
        hits.sort(key=lambda f: int(f.get("size_bytes") or 0))
        for f in hits:
            seen.add(f["file_path"])
            out.append(f["file_path"])
    return out


def _release_path(d, kind):
    c = _doc_candidates(d, kind)
    return c[0] if c else None


def cmd_next(d, st):
    _RUN_DIR[0] = d
    wait = _rate_wait(st)
    if wait:
        print("WAIT %d seconds before the next MCP call (rate limit: at most %d tool calls a minute)." % (wait, RATE_LIMIT_TOOLS))
    t, today = st["ticker"], _today()
    if _resp(d, "resolve") is None:
        return _print_call("resolve", "Resolve %s" % t, [_call("search_tickerid", ticker=t)],
                           "Pass the ticker ALONE - adding company= can hang the resolver.")
    res = st.get("resolved")
    if not res:
        _die("the resolve response has no usable row. Save it again.")
    tid = res["ticker_id"]
    if _resp(d, "kpis_top") is None:
        return _print_call("kpis_top", "%s top KPIs" % t,
                           [_call("search_lineitem", ticker_id=str(tid), queries="all", filter="top_kpi")])
    facts = st.setdefault("facts", {})
    if not facts.get("currency"):
        country = facts.get("country") or _COUNTRY_BY_REGION.get(res.get("region"))
        if not country:
            print("NEED the issuer's home country, full name (e.g. 'South Korea', 'United Kingdom').")
            print("NEXT: python3 flash.py set country \"<full country name>\"")
            return
        _print_call("currency", "%s reported currency" % t,
                    [_call("search_tickerid", mode="explore", country=country,
                           insync_subindustry=res.get("insync_subindustry"), limit="100")],
                    "Do not save this one. Find the row whose ticker_id is %s, read its primary_currency, "
                    "then run: python3 flash.py set currency <CODE>. If the row is not there and the "
                    "company is US-listed and reports in dollars, run: python3 flash.py set currency USD" % tid,
                    save=False)
        print("NEXT: python3 flash.py set currency <CODE>")
        return
    cands, _ = _kpi_candidates(d, st)
    if _resp(d, "probe") is None:
        lt = ",".join(SPINE + [c["ltcode"] for c in cands if not c["foreign"] and c["ltcode"] not in SPINE][:MAX_KPIS])
        win = (_iso(today - _dt.timedelta(days=120)), _iso(today + _dt.timedelta(days=120)))
        st["call_window"] = {"start": win[0], "end": win[1]}
        _save_state(d, st)
        return _print_call("probe", "%s period probe and call calendar" % t, [
            _call("get_actuals", ticker_id=str(tid), ltcode=lt, limit="100"),
            _call("get_call_date", ticker_id=str(tid), start_date=win[0], end_date=win[1], limit="20")],
            "get_actuals has NO fiscal_period on purpose: it shows which periods exist.")
    probe_labels = st.get("period_probe") or []
    if (not st.get("user_period") and _resp(d, "probe_half") is None and probe_labels
            and all(re.match(r"^R?\d*-?\d{4}$", x) for x in probe_labels)):
        years = sorted({int(m.group(1)[-2:]) for x in probe_labels for m in [re.search(r"(\d{2,4})$", x)] if m})
        if years:
            y = years[-1]
            halves = ["1H%02d" % y, "2H%02d" % y, "1H%02d" % ((y + 1) % 100), "2H%02d" % ((y - 1) % 100)]
            halves += ["%dQ%02d" % (q, yy) for yy in (y, (y + 1) % 100) for q in (1, 2, 3, 4)]
            lt = ",".join(SPINE)
            return _print_call("probe_half", "%s half-year probe" % t, [
                _call("get_actuals", ticker_id=str(tid), ltcode=lt, fiscal_period=",".join(halves), limit="100")],
                "The first probe showed only %s. This checks whether the company reports half-years." %
                ", ".join(probe_labels))
    if not st.get("period"):
        _die("the probe did not resolve a period. Re-run with: python3 flash.py start %s --period <label>" % t)
    per, pp = st["period"], st["prior_period"]
    if _resp(d, "fetch") is None:
        codes = SPINE + [c["ltcode"] for c in cands if not c["foreign"] and c["ltcode"] not in SPINE][:KPI_FETCH]
        if REVENUE_EX_ONE_OFF not in codes:
            codes = codes[:len(SPINE)] + [REVENUE_EX_ONE_OFF] + codes[len(SPINE):]
        st["requested"] = codes
        _save_state(d, st)
        lt = ",".join(codes)
        chain = run_math.valuation_request_metrics(run_math.sector_group(res.get("insync_subindustry")))
        return _print_call("fetch", "%s %s flash data" % (t, per), [
            _call("get_actuals", ticker_id=str(tid), ltcode=lt, fiscal_period="%s,%s" % (per, pp), limit="100"),
            _call("get_estimates", ticker_id=str(tid), ltcode=lt, fiscal_period=_bare(per),
                  latest={"enabled": True, "limit": 1}, limit="100"),
            _call("get_valuation", ticker_id=str(tid), mode="NTM", metrics=chain, limit="12"),
            _call("get_guidance", ticker_id=str(tid), latest=True, limit="100", offset="0"),
            _call("get_market_data", ticker_id=str(tid), start_date=_iso(today - _dt.timedelta(days=14)),
                  end_date=_iso(today), limit="100")])
    i = 2
    while _resp(d, "guidance_page%d" % i) is not None:
        i += 1
    last = _one(d, "fetch" if i == 2 else "guidance_page%d" % (i - 1), "get_guidance")
    m = _meta(last)
    if m.get("has_more"):
        off = int(m.get("offset") or 0) + int(m.get("limit") or 100)
        return _print_call("guidance_page%d" % i, "%s guidance page %d" % (t, i),
                           [_call("get_guidance", ticker_id=str(tid), latest=True, limit="100", offset=str(off))])
    fx = [c["ltcode"] for c in cands if c["foreign"]]
    if fx and _resp(d, "fx_kpis") is None:
        st.setdefault("requested", []).extend(x for x in fx if x not in st["requested"])
        _save_state(d, st)
        return _print_call("fx_kpis", "%s foreign-currency KPIs, fetched alone" % t, [
            _call("get_actuals", ticker_id=str(tid), ltcode=",".join(fx), fiscal_period="%s,%s" % (per, pp), limit="100"),
            _call("get_estimates", ticker_id=str(tid), ltcode=",".join(fx), fiscal_period=_bare(per),
                  latest={"enabled": True, "limit": 1}, limit="100")])
    km, graded = kpi_mapping(d, st)
    for tier in TIERS[1:]:
        key = "kpis_" + tier.replace("_kpi", "")
        if graded >= KPI_TARGET_ROWS:
            break
        if _resp(d, key) is None and tier == "industry_kpi" and os.path.isfile(_prefetch_path(d)):
            with open(_prefetch_path(d), encoding="utf-8") as f:
                pre = json.load(f)
            with open(os.path.join(d, "responses", key + ".json"), "w", encoding="utf-8") as f:
                json.dump(pre, f, ensure_ascii=False)
            os.remove(_prefetch_path(d))
        if _resp(d, key) is None:
            if tier == "company_kpi":
                return _print_call("kpis_tiers", "%s company and industry KPIs" % t, [
                    _call("search_lineitem", ticker_id=str(tid), queries="all", filter="company_kpi"),
                    _call("search_lineitem", ticker_id=str(tid), queries="all", filter="industry_kpi")],
                    "Only %d KPI(s) have an actual and a consensus so far." % graded)
            if tier == "keyword":
                return _print_call(key, "%s KPI keyword sweep" % t,
                                   [_call("search_lineitem", ticker_id=str(tid), queries=KEYWORDS, limit="5")],
                                   "Only %d KPI(s) have an actual and a consensus so far." % graded)
            return _print_call(key, "%s %s" % (t, tier),
                               [_call("search_lineitem", ticker_id=str(tid), queries="all", filter=tier)],
                               "Only %d KPI(s) have an actual and a consensus so far." % graded)
        pend = st.setdefault("kpi_fetches", {})
        req = set(st.get("requested") or []) - {c for n, cs in pend.items() if _resp(d, n) is None for c in cs}
        tier_new = [c for c in _kpi_candidates(d, st)[0] if c["tier"] == tier and c["ltcode"] not in req]
        if tier == "keyword":
            tier_new = sorted(tier_new, key=lambda c: not c["ltcode"].startswith("KM_"))
        short = tier.replace("_kpi", "")
        for foreign, base in ((False, "kpi_extra_" + short), (True, "fx_kpis_" + short)):
            for name in (base, base + "_2"):
                if name != base and (_resp(d, base) is None or kpi_mapping(d, st)[1] >= KPI_TARGET_ROWS):
                    break
                new = pend.get(name) or [c["ltcode"] for c in tier_new if c["foreign"] == foreign][:MAX_KPIS]
                if new and _resp(d, name) is None:
                    pend[name] = new
                    st["requested"] = list(dict.fromkeys(list(st.get("requested") or []) + new))
                    _save_state(d, st)
                    return _print_call(name, "%s %s candidates%s" % (t, tier, " (foreign currency, alone)" if foreign else ""), [
                        _call("get_actuals", ticker_id=str(tid), ltcode=",".join(new), fiscal_period="%s,%s" % (per, pp), limit="100"),
                        _call("get_estimates", ticker_id=str(tid), ltcode=",".join(new), fiscal_period=_bare(per),
                              latest={"enabled": True, "limit": 1}, limit="100")])
        km, graded = kpi_mapping(d, st)
    if _resp(d, "fetch2") is None:
        codes, periods = _guided_codes(d, st)
        calls = [_call("get_document_tree", ticker_id=str(tid), latest=True)]
        cd = st.get("call_date")
        ffo = [lt for lt in ("CF_P_209", "CF_P_202", "CF_P_203") if lt in _fetched_codes(d)]
        if ffo:
            codes = codes + [ffo[0]]
            periods = periods + [p for p in [st.get("ntm_year")] if p and p not in periods]
        if codes and periods and cd:
            c0 = _dt.date.fromisoformat(cd)
            args = dict(ticker_id=str(tid), ltcode=",".join(codes), fiscal_period=",".join(periods),
                        latest={"enabled": False}, updatedate_start=_iso(c0 - _dt.timedelta(days=CONSENSUS_WINDOW_DAYS[0])),
                        updatedate_end=cd, limit="100")
            st["fetch2_est_args"] = args
            pairs = _guided_pairs(d, st, codes[:len(codes) - len(ffo[:1])], periods)
            if pairs is None:
                pairs = [(lt, fp) for lt in codes for fp in periods]
            elif ffo:
                pairs += [(ffo[0], fp) for fp in periods]
            reqs =(_latest_requests(tid, [p for p in pairs if not str(p[0]).endswith("-Y")], cd)
                    + _latest_requests(tid, [p for p in pairs if str(p[0]).endswith("-Y")], cd))
            st["fetch2_mode"] = "latest"
            st["fetch2_reqs"] = reqs
            st["fetch2_parts"] = [reqs[i:i + 5] for i in range(4, len(reqs), 5)]
            _save_state(d, st)
            calls += [_call("get_estimates", **a) for a in reqs[:4]]
        return _print_call("fetch2", "%s documents and pre-guide consensus" % t, calls,
                           "The get_estimates calls pin consensus to the last updates BEFORE the guide.")
    if st.get("fetch2_mode") == "latest":
        for i, part in enumerate(st.get("fetch2_parts") or [], start=2):
            if _resp(d, "fetch2_part%d" % i) is None:
                return _print_call("fetch2_part%d" % i, "%s pre-guide consensus, part %d" % (t, i),
                                   [_call("get_estimates", **a) for a in part])
        for j, (a, _, last, page) in enumerate(_latest_parts(d, st)):
            m = _meta(last)
            if m.get("has_more"):
                a = dict(a, offset=str(int(m.get("offset") or 0) + int(m.get("limit") or 100)))
                return _print_call("fetch2_more_%d_%d" % (j, page + 1),
                                   "%s pre-guide consensus, request %d page %d" % (t, j + 1, page + 1),
                                   [_call("get_estimates", **a)])
        why = _latest_broken(d, st)
        if why:
            st["fetch2_mode"], st["fetch2_fallback"] = "history", why
            _save_state(d, st)
    if st.get("fetch2_mode") == "history" and _resp(d, "fetch2_hist") is None:
        return _print_call("fetch2_hist", "%s pre-guide consensus, 90-day history" % t,
                           [_call("get_estimates", **st["fetch2_est_args"])],
                           "The latest-updates reply did not behave as expected (%s), so consensus is asked "
                           "for as 90 days of history instead." % st.get("fetch2_fallback"))

    b = _hb(st)
    i = 2
    while _resp(d, b + "_page%d" % i) is not None:
        i += 1
    gm = _meta(_one(d, b if i == 2 else b + "_page%d" % (i - 1), "get_estimates"))
    if st.get("fetch2_mode") != "latest" and gm.get("has_more") and st.get("fetch2_est_args"):
        args = dict(st["fetch2_est_args"], offset=str(int(gm.get("offset") or 0) + int(gm.get("limit") or 100)))
        return _print_call(b + "_page%d" % i, "%s pre-guide consensus page %d" % (t, i),
                           [_call("get_estimates", **args)])
    thin = _thin_consensus_keys(d, st)
    if thin and _resp(d, b + "_wide") is None and st.get("fetch2_est_args"):
        a = st["fetch2_est_args"]
        c0 = _dt.date.fromisoformat(a["updatedate_end"])
        lts = sorted({k[0] for k in thin})
        fps = sorted({k[1] for k in thin})
        wargs = dict(a, ltcode=",".join(lts), fiscal_period=",".join(fps),
                     updatedate_start=_iso(c0 - _dt.timedelta(days=CONSENSUS_WINDOW_DAYS[1])))
        st["fetch2_wide_args"] = wargs
        _save_state(d, st)
        return _print_call(b + "_wide", "%s pre-guide consensus, wider window" % t, [_call("get_estimates", **wargs)],
                           "%d guided line(s) had fewer than two consensus snapshots before the call." % len(thin))
    if _resp(d, b + "_wide") is not None and st.get("fetch2_wide_args"):
        i = 2
        while _resp(d, b + "_wide_page%d" % i) is not None:
            i += 1
        wm = _meta(_one(d, b + "_wide" if i == 2 else b + "_wide_page%d" % (i - 1), "get_estimates"))
        if wm.get("has_more"):
            wargs = dict(st["fetch2_wide_args"], offset=str(int(wm.get("offset") or 0) + int(wm.get("limit") or 100)))
            return _print_call(b + "_wide_page%d" % i, "%s pre-guide consensus, wider window, page %d" % (t, i),
                               [_call("get_estimates", **wargs)])
    if not facts.get("guidance_source"):
        gstate = _guidance_state(d, st)
        if gstate == "current":
            facts["guidance_source"] = "mcp"
            _save_state(d, st)
        else:
            tried = st.get("parsed") or []
            silent = set(st.get("silent_docs") or [])
            read_any = any(p["result"] == "success" for p in tried)
            for kind in ("release", "transcript"):
                for cand in _doc_candidates(d, kind):
                    mine = [p for p in tried if p["path"] == cand]
                    if any(p["result"] == "success" for p in mine):
                        if cand in silent:
                            continue
                        print("The guidance feed is %s. You have read %s." % (gstate, os.path.basename(cand)))
                        print("If it states guidance: python3 flash.py set guidance_source %s" % kind)
                        print("If it states none:     python3 flash.py set %s_silent yes" % kind)
                        print("NEXT: one of the two commands above")
                        return
                    if len(mine) >= MAX_PARSES_PER_DOC or len(tried) >= MAX_PARSES_TOTAL:
                        continue
                    n = len(mine)
                    return _print_call("parse_" + kind, "%s %s text" % (t, kind), [
                        _call("parse_document_content", file_paths=[cand], user_prompt=_PARSE_PROMPTS[min(n, 1)])],
                        "Guidance feed is %s, so the guide is read off this document. " % gstate
                        + _PARSE_JUDGE % (kind, cand, kind, cand), save=False)
            if read_any:
                print("The documents were read and carry no guidance.")
                print("NEXT: python3 flash.py set guidance_source none")
            else:
                print("No document could be read.")
                print("NEXT: python3 flash.py set guidance_source unavailable")
            return
    if facts.get("guidance_source") == "mcp":
        tried = st.get("parsed") or []
        cand = (_doc_candidates(d, "release") or [None])[0]
        mine = [p for p in tried if p["path"] == cand]
        if cand and not any(p["result"] == "success" for p in tried) and len(mine) < MAX_PARSES_PER_DOC:
            return _print_call("parse_release", "%s release text" % t, [
                _call("parse_document_content", file_paths=[cand], user_prompt=_PARSE_PROMPTS[min(len(mine), 1)])],
                "The guidance feed is current; this read is for What Matters (tone, one-off items). "
                + _PARSE_JUDGE % ("release", cand, "release", cand), save=False)
    if facts.get("guidance_source") == "none" and _resp(d, "forward") is None:
        nq, fy = next_quarter(per), fiscal_year_of(per, forward=True)
        return _print_call("forward", "%s forward consensus (company does not guide)" % t, [
            _call("get_estimates", ticker_id=str(tid), ltcode="IS_P_101,IS_P_128,IS_P_127",
                  fiscal_period=",".join(x for x in (nq, fy) if x), latest={"enabled": True, "limit": 1}, limit="100")])
    if not os.path.isfile(os.path.join(d, "input.json")):
        print("All data is in (%d MCP calls, %d tool calls)." % (len(st.get("calls", [])), sum(c["n"] for c in st.get("calls", []))))
        print("NEXT: python3 flash.py assemble")
        return
    if not os.path.isfile(os.path.join(d, "facts.json")):
        print("NEXT: python3 flash.py facts")
        return
    if not os.path.isfile(os.path.join(d, "narrative.json")):
        print("Write %s/narrative.json from facts.json (rules: references/writing-rules.md)." % d)
        print("NEXT: python3 flash.py build")
        return
    print("NEXT: python3 flash.py build")


def _post_save(d, st, name, obj):
    if name == "resolve":
        rows = []
        o = _split_tools(obj)
        o = o.get("search_tickerid") or o.get("_single")
        data = (o or {}).get("data")
        if isinstance(data, dict):
            rows = data.get("success") or []
        elif isinstance(data, list):
            rows = data
        row = next((r for r in rows if str(r.get("ticker", "")).upper() == st["ticker"]), rows[0] if rows else None)
        if not row:
            _die("search_tickerid returned no row for %s." % st["ticker"])
        if row.get("match_type") != "exact_ticker":
            _die("%s is not in the InSync ticker table - search_tickerid offered %s (%s) as a %s match. "
                 "That is a DIFFERENT company. Ask the user for the right symbol or ticker_id." %
                 (st["ticker"], row.get("ticker"), row.get("company_name"), row.get("match_type")), 3)
        st["resolved"] = row
    if name in ("probe", "probe_half"):
        acts = _rows(_one(d, "probe", "get_actuals")) + _rows(_one(d, "probe_half", "get_actuals"))
        if name == "probe_half" and not st.get("user_period"):
            st.pop("period", None)
            st.pop("call_date", None)
            st.pop("call_date_inferred", None)
        labels = []
        for r in acts:
            fp = r.get("FISCALPERIOD")
            if fp and fp not in labels:
                labels.append(str(fp))
        st["period_probe"] = labels
        if not st.get("period"):
            wrapped = {"status": "success", "data": acts}
            p, shape, _ = run_math.preferred_period(wrapped)
            if p:
                st["period"] = p
        if st.get("period"):
            st["prior_period"] = prior_label(st["period"])
            cal = _rows(_one(d, "probe", "get_call_date"))
            for r in cal:
                if _bare(r.get("FISCALPERIOD")) == _bare(st["period"]):
                    st["call_date"] = str(r.get("CALL_DATE"))[:10]
            if not st.get("call_date"):
                past = sorted(str(r.get("CALL_DATE"))[:10] for r in cal
                              if r.get("CALL_DATE") and str(r.get("CALL_DATE"))[:10] <= _iso(_today()))
                if past:
                    st["call_date"], st["call_date_inferred"] = past[-1], True
                    print("NOTE: no call-calendar row is labelled %s; using the latest past call, %s. "
                          "If that is not this print, restart with --period." % (st["period"], past[-1]))
            m = re.match(r"^(\d)Q(\d{2})$", _bare(st["period"]))
            if m:
                st["ntm_year"] = "20%02d" % (int(m.group(2)) + (1 if int(m.group(1)) >= 3 else 0))
    return st


PIPE_DROP = ("TICKER_ID", "UIC", "FORMAT_CODE", "YOY_GROWTH", "QOQ_GROWTH", "TYPE")
PIPE_INT = ("BROKERCOUNT", "TICKER_ID")
PIPE_DATE = ("QUARTERENDDATE", "UPDATEDATE")
PIPE_NEED = {"get_actuals": ("LTCODE", "LINEITEM", "FISCALPERIOD", "VALUE", "QUARTERENDDATE", "SOURCE_DERIVED"),
             "get_estimates": ("LTCODE", "LINEITEM", "FISCALPERIOD", "VALUE", "MIN", "MAX", "QUARTERENDDATE",
                               "BROKERCOUNT", "UPDATEDATE")}
PIPE_FORMAT = (
    "HOW TO SAVE A REPLY (saves typing; checked on save). Leave out the fields %s\n"
    "everywhere - nothing reads them. Write each get_actuals or get_estimates part as pipe rows:\n"
    "  @<part name, e.g. get_estimates_2; a one-tool reply: the tool's name> <its meta object, copied exactly>\n"
    "  !notes <its notes list, copied exactly>          (only if it has notes)\n"
    "  <its field names joined by |>\n"
    "  <one line per row: the values exactly as given, joined by |; empty for null>\n"
    "and every other part as @<part name> on one line, then that part's JSON on one line.\n"
    "Field names may be shortened: %s. (Plain JSON is accepted too.)" % (
        ", ".join(PIPE_DROP), ", ".join("%s %s" % (v, k) for k, v in run_math._SHORT_KEYS.items())))
_SAVE_WARNINGS = []
_RUN_DIR = [None]


class PipeError(ValueError):
    pass


def _pipe_number(v):
    m = re.match(r"^\(?-?[^\d\s(-]{0,4}\s?(-?[\d,]*\.?\d+)\s?([KkMBT%]?)\)?$", v)
    if not m:
        return None
    x = float(m.group(1).replace(",", "")) * {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}.get(m.group(2).upper(), 1)
    return -x if v.startswith(("-", "(")) and x > 0 else x


def _parse_pipe(raw):
    out, name, lines = {}, None, []
    blocks = []
    for ln in raw.splitlines():
        if ln.startswith("@"):
            if name:
                blocks.append((name, head, lines))
            name, _, head = ln[1:].strip().partition(" ")
            lines = []
        elif name and ln.strip():
            lines.append(ln.rstrip("\r"))
    if name:
        blocks.append((name, head, lines))
    for name, head, lines in blocks:
        if not lines:
            raise PipeError("part %s has nothing under it." % name)
        if lines[0].lstrip().startswith(("{", "[")) and not head.strip():
            try:
                out[name] = json.loads("\n".join(lines))
            except ValueError as e:
                raise PipeError("part %s is not valid JSON (%s)." % (name, e))
            continue
        tool = "get_actuals" if name.startswith("get_actuals") else "get_estimates" if name.startswith("get_estimates") else None
        if not tool:
            raise PipeError("part %s: pipe rows are only for get_actuals and get_estimates; write it as JSON." % name)
        try:
            meta = json.loads(head) if head.strip() else None
        except ValueError:
            meta = None
        if not isinstance(meta, dict) or not isinstance(meta.get("row_count"), int):
            raise PipeError("part %s: the @ line must carry the part's meta object exactly, e.g. "
                            "@%s {\"row_count\": 12, \"limit\": 100, \"offset\": 0, \"has_more\": false}" % (name, name))
        if "has_more" not in meta and meta["row_count"] >= int(meta.get("limit") or 100):
            raise PipeError("part %s: a full page needs its has_more value. Copy the meta object exactly." % name)
        notes = None
        if lines[0].startswith("!notes"):
            try:
                notes = json.loads(lines[0][6:].strip())
            except ValueError:
                raise PipeError("part %s: the !notes line must hold the notes list exactly as given." % name)
            lines = lines[1:]
        if not lines:
            raise PipeError("part %s has no header line." % name)
        header = [run_math._SHORT_KEYS.get(h.strip(), h.strip()) for h in lines[0].split("|")]
        miss = [f for f in PIPE_NEED[tool] if f not in header]
        if meta["row_count"] and miss:
            raise PipeError("part %s: the header leaves out %s. Write every field of the reply except %s."
                            % (name, ", ".join(miss), ", ".join(PIPE_DROP)))
        rows = []
        for i, ln in enumerate(lines[1:], start=1):
            cells = ln.split("|")
            if len(cells) != len(header):
                raise PipeError("part %s row %d has %d values for %d fields (a value with | in it must be "
                                "written as JSON instead): %s" % (name, i, len(cells), len(header), ln[:120]))
            r = {}
            for f, v in zip(header, cells):
                v = v.strip()
                if v == "":
                    continue
                if f in PIPE_INT:
                    if not re.match(r"^-?\d+$", v):
                        raise PipeError("part %s row %d: %s must be a whole number, got %r." % (name, i, f, v))
                    v = int(v)
                elif f in PIPE_DATE and not re.match(r"^\d{4}-\d{2}-\d{2}", v):
                    raise PipeError("part %s row %d: %s must be a date like 2026-06-30, got %r." % (name, i, f, v))
                elif f == "LTCODE" and not re.match(r"^[A-Za-z0-9_.\-]+$", v):
                    raise PipeError("part %s row %d: %r is not a line-item code." % (name, i, v))
                r[f] = v
            for f in ("VALUE", "MIN", "MAX"):
                if f in r and _pipe_number(r[f]) is None:
                    _SAVE_WARNINGS.append("CHECK %s row %d: %s %r does not read as a number - is it copied exactly?"
                                          % (name, i, f, r[f]))
            lo, val, hi = (_pipe_number(r.get(f, "")) for f in ("MIN", "VALUE", "MAX"))
            if None not in (lo, val, hi) and not (lo - 1e-9 * abs(lo) <= val <= hi + 1e-9 * abs(hi)):
                _SAVE_WARNINGS.append("CHECK %s row %d: %s %s is outside its low-high %s to %s - re-check that row "
                                      "against the reply." % (name, i, r.get("LTCODE"), r["VALUE"], r["MIN"], r["MAX"]))
            rows.append(r)
        if len(rows) != meta["row_count"]:
            raise PipeError("part %s has %d rows but its meta says row_count %d. Copy every row."
                            % (name, len(rows), meta["row_count"]))
        part = {"status": "success", "data": rows, "meta": meta}
        if notes is not None:
            part["notes"] = notes
        out[name] = part
    if not out:
        raise PipeError("no part found. Start each part with @<part name>.")
    return out


def _load_response(raw):
    if raw.lstrip().startswith("@"):
        return _parse_pipe(raw.lstrip())
    try:
        obj = json.loads(raw)
    except ValueError:
        i = raw.find("{")
        if i < 0:
            raise
        obj = json.JSONDecoder().raw_decode(raw[i:])[0]
    if isinstance(obj, list) and obj and all(isinstance(x, dict) and x.get("type") == "text" for x in obj):
        obj = _load_response("".join(x.get("text") or "" for x in obj))
    if isinstance(obj, dict) and set(obj) == {"text"} and isinstance(obj["text"], str):
        obj = _load_response(obj["text"])
    return obj


def _prefetch_path(d):
    return os.path.join(d, "responses", "_prefetch_kpis_industry.json")


def _split_tiers(obj):
    parts = [v for k, v in _split_tools(obj).items() if k.startswith("search_lineitem")]
    by = {}
    for p in parts:
        f = ((p or {}).get("meta") or {}).get("filter") or []
        f = f if isinstance(f, list) else [f]
        for tier in ("company_kpi", "industry_kpi"):
            if tier in f:
                by[tier] = p
    if len(by) < 2 and len(parts) == 2:
        by = {"company_kpi": parts[0], "industry_kpi": parts[1]}
    if len(by) < 2:
        _die("the kpis_tiers reply should hold two search_lineitem results (company and industry). Save it again.")
    return by["company_kpi"], by["industry_kpi"]


def cmd_save(d, st, name, path):
    raw = sys.stdin.read() if path == "-" else open(path, encoding="utf-8-sig").read()
    try:
        obj = _load_response(raw)
        obj = run_math.expand_short_keys(obj)
    except PipeError as e:
        _die("%s Fix it and save again (or save that part as JSON)." % e)
    except ValueError as e:
        _die("that file is not valid JSON (%s). Save the MCP response exactly as it came back." % e)
    os.makedirs(os.path.join(d, "responses"), exist_ok=True)
    ntools = max(1, len(_split_tools(obj)))
    if name == "kpis_tiers":
        company, industry = _split_tiers(obj)
        with open(_prefetch_path(d), "w", encoding="utf-8") as f:
            json.dump({"search_lineitem": industry}, f, ensure_ascii=False)
        obj, name = {"search_lineitem": company}, "kpis_company"
    with open(os.path.join(d, "responses", name + ".json"), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    parts = _split_tools(obj)
    st.setdefault("calls", []).append({"t": time.time(), "n": ntools, "name": name})
    st = _post_save(d, st, name, obj)
    _save_state(d, st)
    print("saved %s (%d tool result(s))." % (name, ntools))
    for w in _SAVE_WARNINGS:
        print(w)
    cmd_next(d, st)


def cmd_set(d, st, key, value):
    allowed = {"currency", "country", "guidance_source", "release_silent", "transcript_silent", "period",
               "direction", "reported"}
    if key not in allowed:
        _die("unknown key %r; one of %s" % (key, ", ".join(sorted(allowed))))
    if key in ("direction", "reported"):
        lt, _, way = value.partition(" ")
        way = way.strip()
        if key == "direction":
            if way not in ("higher", "lower") or not lt:
                _die("usage: python3 flash.py set direction <LTCODE> higher|lower")
            st.setdefault("directions", {})[lt] = way
        else:
            try:
                st.setdefault("reported", {})[lt] = float(way.replace(",", ""))
            except ValueError:
                _die("usage: python3 flash.py set reported <LTCODE> <value in base units, e.g. 1.99>")
        _save_state(d, st)
        ip = os.path.join(d, "input.json")
        if os.path.isfile(ip):
            with open(ip, encoding="utf-8") as f:
                inp = json.load(f)
            inp["kpi_direction"] = st.get("directions") or {}
            inp["company_reported"] = st.get("reported") or {}
            with open(ip, "w", encoding="utf-8") as f:
                json.dump(inp, f, indent=1)
        print("set %s %s = %s" % (key, lt, way))
        return 0
    if key == "guidance_source" and value not in ("mcp", "release", "transcript", "none", "unavailable"):
        _die("guidance_source is one of mcp, release, transcript, none, unavailable")
    if key in ("release_silent", "transcript_silent"):
        kind = key.split("_")[0]
        done = [p["path"] for p in st.get("parsed") or [] if p["kind"] == kind and p["result"] == "success"]
        if done:
            st.setdefault("silent_docs", [])
            if done[-1] not in st["silent_docs"]:
                st["silent_docs"].append(done[-1])
    if key == "period":
        st["period"], st["prior_period"] = value, prior_label(value)
    else:
        st.setdefault("facts", {})[key] = value.upper() if key == "currency" else value
    _save_state(d, st)
    print("set %s = %s" % (key, value))
    cmd_next(d, st)


def _headline_misses(d, st, body):
    out = []
    flat = body.replace(",", "")
    for r in _rows(_one(d, "fetch", "get_actuals")):
        if r.get("LTCODE") not in ("IS_P_101", "IS_P_128", "IS_P_127") or r.get("FISCALPERIOD") != st.get("period"):
            continue
        x = _pipe_number(str(r.get("VALUE") or ""))
        if x is None:
            continue
        x = abs(x)
        forms = {"%.2f" % x, "%.1f" % x} if x < 1e6 else set()
        for scale in (1e9, 1e6):
            if x >= scale:
                y = x / scale
                forms |= {"%.2f" % y, "%.1f" % y, "%.0f" % y}
        if not any(re.search(r"(?<![\d.])" + re.escape(f) + r"(?![\d])", flat) for f in forms):
            out.append("NOTE: %s %s (%s) does not appear in the release text in any usual form. Check the "
                       "row against the release before relying on it." % (r.get("LTCODE"), r.get("VALUE"), r.get("LINEITEM")))
    return out


def cmd_parsed(d, st, kind, path, result, textfile=None):
    if result not in ("success", "timeout", "refusal", "empty", "error", "skipped"):
        _die("result is one of success, timeout, refusal, empty, error, skipped")
    st.setdefault("parsed", []).append({"kind": kind, "path": path, "result": result})
    st.setdefault("calls", []).append({"t": time.time(), "n": 1, "name": "parse_" + kind})
    if textfile and result == "success" and os.path.isfile(textfile):
        st["parsed"][-1]["text_file"] = os.path.abspath(textfile)
        try:
            body = open(textfile, encoding="utf-8", errors="replace").read()
            names = [st["ticker"]] + [w for w in re.findall(r"[A-Za-z][A-Za-z&.\-]{2,}",
                                                           str((st.get("resolved") or {}).get("company_name") or ""))[:1]]
            if not any(re.search(r"\b" + re.escape(n) + r"\b", body, re.I) for n in names if n):
                print("NOTE: the text never mentions %s. Check it is this company's document before "
                      "relying on it." % " or ".join(n for n in names if n))
            for line in _headline_misses(d, st, body):
                print(line)
        except OSError:
            pass
    _save_state(d, st)
    print("recorded %s parse: %s" % (kind, result))
    cmd_next(d, st)


def cmd_eightk(d, st, path):
    try:
        vals = json.load(open(path, encoding="utf-8"))
        vals = {str(k): float(v) for k, v in vals.items()}
    except Exception as e:
        _die("that file is not a JSON object of {ltcode: number} (%s)." % e)
    bad = [k for k in vals if k not in SPINE]
    if bad:
        _die("only spine ltcodes can be supplied this way; not: %s" % ", ".join(bad))
    st["eight_k_actuals"] = vals
    _save_state(d, st)
    print("recorded %d 8-K actual(s) for %s. They are marked as typed from the release, not from the MCP." %
          (len(vals), st.get("period")))
    print("NEXT: python3 flash.py assemble   (then say in flashVerdict.net that the actuals are from the release)")


def _merge_payloads(*payloads):
    base = None
    for p in payloads:
        if p is None:
            continue
        p = json.loads(json.dumps(p))
        if base is None:
            base = p
            continue
        bd = run_math._unwrap_mcp_payload(base)
        pd = run_math._unwrap_mcp_payload(p)
        if isinstance(bd.get("data"), list) and isinstance(pd.get("data"), list):
            bd["data"].extend(pd["data"])
        nb, np_ = bd.get("notes") or [], pd.get("notes") or []
        bd["notes"] = (nb if isinstance(nb, list) else [nb]) + (np_ if isinstance(np_, list) else [np_])
        base = bd
    return base


def cmd_assemble(d, st):
    need = ["resolve", "probe", "fetch", "fetch2"]
    missing = [n for n in need if _resp(d, n) is None]
    if missing or not (st.get("facts") or {}).get("currency"):
        _die("not all data is in yet (%s). Run: python3 flash.py next" % ", ".join(missing or ["currency"]))
    res, facts = st["resolved"], st.get("facts", {})
    km, _ = kpi_mapping(d, st)
    tiers = [t for t in TIERS if _resp(d, "kpis_" + t.replace("_kpi", "")) is not None]
    mcp = {
        "get_actuals": _merge_payloads(*[_one(d, n, "get_actuals") for n in _kpi_fetch_names(d)]),
        "get_estimates": _merge_payloads(*[_one(d, n, "get_estimates") for n in _kpi_fetch_names(d)]),
        "get_valuation": _one(d, "fetch", "get_valuation"),
        "get_market_data": _one(d, "fetch", "get_market_data"),
        "get_call_date": _one(d, "probe", "get_call_date"),
        "get_guidance": _merged_guidance(d),
        "get_document_tree": _one(d, "fetch2", "get_document_tree"),
    }
    b = _hb(st)
    eg = _one(d, b, "get_estimates")
    if eg is not None:
        eg = json.loads(json.dumps(eg))
        body = run_math._unwrap_mcp_payload(eg)
        if isinstance(body.get("data"), list):
            body["data"] = _pinned_rows(d)
            i = 2
            while _resp(d, b + "_page%d" % (i + 1)) is not None:
                i += 1
            last = _one(d, b + "_page%d" % i, "get_estimates")
            if last is not None:
                lm = (run_math._unwrap_mcp_payload(last) or {}).get("meta")
                if lm is not None:
                    body["meta"] = lm
        for extra in ([b + "_page%d" % i for i in range(2, 50)] + [b + "_wide"]
                      + [b + "_wide_page%d" % i for i in range(2, 50)]):
            en = (run_math._unwrap_mcp_payload(_one(d, extra, "get_estimates") or {}) or {}).get("notes")
            if en:
                body["notes"] = (body.get("notes") or []) + (en if isinstance(en, list) else [en])
        if st.get("fetch2_mode") == "latest":
            ends = [bool(_meta(last).get("has_more")) for _, _, last, _ in _latest_parts(d, st)]
            body["meta"] = {"row_count": len(body["data"]), "has_more": any(ends)}
            for p in [p for n in _fetch2_names(d, st) for p in _est_parts(d, n)][1:]:
                en = (run_math._unwrap_mcp_payload(p) or {}).get("notes")
                if en:
                    body["notes"] = (body.get("notes") or []) + (en if isinstance(en, list) else [en])
        mcp["get_estimates_guidance"] = body
    mcp = {k: v for k, v in mcp.items() if v is not None}
    rel = _release_path(d, "release") if _resp(d, "fetch2") else None
    prov = {
        "ltcodes_requested": list(st.get("requested") or []),
        "period_probe": st.get("period_probe") or [],
        "reported_currency": facts.get("currency"),
        "kpi_query": "all",
        "tiers_checked": tiers,
        "call_window": st.get("call_window") or {},
        "ticker_resolution": {k: res.get(k) for k in ("match_type", "ticker", "company_name",
                                                         "latest_fiscal_quarter", "latest_quarter_end_date")},
        "document_attempts": [{"path": p["path"], "kind": p["kind"], "result": p["result"]}
                              for p in st.get("parsed") or []],
        "guidance_scope": "assembled by flash.py: every guidance page merged.",
    }
    prov["ticker_resolution"]["returned_ticker"] = prov["ticker_resolution"].pop("ticker")
    if rel:
        prov["release_file"] = os.path.basename(rel)
    gsrc = facts.get("guidance_source") or "mcp"
    if gsrc == "unavailable":
        g = _guide_list(mcp.get("get_guidance"))
        att = {"feed": {"rows": len(g), "result": "empty" if not g else "stale"}}
        for kind in ("release", "transcript"):
            tries = [p for p in st.get("parsed") or [] if p["kind"] == kind]
            att[kind] = {"attempts": len(tries), "path": tries[-1]["path"] if tries else "",
                         "result": tries[-1]["result"] if tries else "absent"}
        prov["guidance_attempts"] = att
    inp = {"ticker": st["ticker"], "period": st["period"], "prior_period": st["prior_period"],
           "call_date_iso": st.get("call_date"), "as_of": _iso(_today()),
           "insync_subindustry": res.get("insync_subindustry"), "guidance_source": gsrc,
           "kpi_mapping": km, "provenance": prov, "mcp_responses": mcp}
    home = _HOME_CCY.get(facts.get("country") or _COUNTRY_BY_REGION.get(res.get("region")) or "")
    if home:
        inp["home_currency"] = home
    if st.get("eight_k_actuals"):
        inp["eight_k_actuals"] = st["eight_k_actuals"]
    fx = [c["ltcode"] for c in _kpi_candidates(d, st)[0] if c["foreign"]]
    if fx:
        inp["foreign_ltcodes"] = fx
    if st.get("allow_stale_period"):
        inp["allow_stale_period"] = True
    if st.get("directions"):
        inp["kpi_direction"] = st["directions"]
    if st.get("reported"):
        inp["company_reported"] = st["reported"]
    fwd = _one(d, "forward", "get_estimates")
    if fwd is not None:
        inp["mcp_responses"]["get_estimates_forward"] = fwd
    for r in _pinned_rows(d):
        if r.get("LTCODE") in ("CF_P_209", "CF_P_202", "CF_P_203") and str(r.get("FISCALPERIOD")) == st.get("ntm_year"):
            inp["ntm_ffo_per_share"] = r.get("VALUE")
    with open(os.path.join(d, "input.json"), "w", encoding="utf-8") as f:
        json.dump(inp, f, indent=1, ensure_ascii=False)
    import copy_check
    blocks, warns = copy_check.check(inp)
    print("wrote %s/input.json (%d KPI candidate(s), guidance_source=%s)." % (d, len([k for k in km if k.endswith("_ltcode")]), gsrc))
    for w in blocks + warns:
        print("  " + w)
    if blocks:
        print("NEXT: re-copy the rows named above (save the response again), then: python3 flash.py assemble")
        return 7
    print("NEXT: python3 flash.py facts")
    return 0


_STUB = {"companyName": "x", "flashVerdict": {"positives": "x", "negatives": "x", "net": "x"},
         "investmentRead": "x", "whatMatters": ["x", "x", "x"],
         "variantView": {"street": "x", "expectationGap": "x", "evidence": "x", "whyItMatters": "x"},
         "guidance": ["x", "x"], "nextWatchItems": ["x"]}


def _pipeline(d, narrative, out, extra=()):
    extra = list(extra)
    if _load(d).get("allow_stale_period") and "--allow-stale-period" not in extra:
        extra.append("--allow-stale-period")
    cmd = [sys.executable, os.path.join(HERE, "run_pipeline.py"), os.path.join(d, "input.json"), narrative, out,
           "--keep-scratch"] + extra
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    try:
        return p.returncode, json.loads(p.stdout)
    except ValueError:
        return p.returncode, {"status": "error", "message": (p.stdout + p.stderr)[-2000:]}


def _latest_scratch(out_dir, stem):
    base = os.path.join(out_dir, "_pipeline_scratch")
    if not os.path.isdir(base):
        return None
    c = [os.path.join(base, x) for x in os.listdir(base) if x.startswith(stem)]
    c.sort(key=os.path.getmtime)
    return c[-1] if c else None


def cmd_facts(d, st):
    if not os.path.isfile(os.path.join(d, "input.json")):
        _die("no input.json yet. Run: python3 flash.py assemble")
    stub = os.path.join(d, "_stub.json")
    with open(stub, "w") as f:
        json.dump(_STUB, f)
    rc, res = _pipeline(d, stub, os.path.join(d, "_probe.docx"))
    msg = str(res.get("message", ""))
    if not msg.startswith("fact_coverage_failed"):
        print(json.dumps(res, indent=1)[:6000])
        _die("the data did not get as far as the scorecard - the message above says why. Fix that first.", rc or 1)
    sc = _latest_scratch(d, "_probe")
    with open(os.path.join(sc, "computed.json"), encoding="utf-8") as f:
        comp = json.load(f)
    import narrative_template as nt
    sheet = nt.fact_sheet(comp, json.load(open(os.path.join(d, "input.json"), encoding="utf-8")),
                          st["resolved"].get("company_name"))
    read = [p for p in st.get("parsed") or [] if p["result"] == "success"
            and p["path"] not in set(st.get("silent_docs") or [])]
    if read and sheet["guidance_source"] in ("release", "transcript"):
        sheet["guidance_doc"] = read[-1].get("text_file") or os.path.basename(read[-1]["path"])
    ok = [p for p in st.get("parsed") or [] if p["result"] == "success"]
    if ok:
        sheet["document_read"] = {"name": os.path.basename(ok[-1]["path"]), "text_file": ok[-1].get("text_file")}
    else:
        sheet["document_unread"] = True
    rg = os.path.join(d, "release_guides.json")
    if os.path.isfile(rg) and sheet["guidance_source"] in ("release", "transcript"):
        sheet["release_guides"] = json.load(open(rg, encoding="utf-8"))
    with open(os.path.join(d, "facts.json"), "w", encoding="utf-8") as f:
        json.dump(sheet, f, indent=1, ensure_ascii=False)
    print(nt.render_sheet(sheet))
    if sheet["guidance_source"] in ("release", "transcript") and "release_guides" not in sheet:
        print("NEXT: write the guides you read to a JSON file and run: python3 flash.py guides <file>")
        print("      then write %s/narrative.json and run: python3 flash.py build" % d)
        return
    print("NEXT: write %s/narrative.json from these facts (references/writing-rules.md), then: python3 flash.py build"
          % d)
    print("      or, for a rule-based draft to edit: python3 flash.py template")


def cmd_guides(d, st, path):
    import narrative_template as nt
    fp = os.path.join(d, "facts.json")
    if not os.path.isfile(fp):
        _die("no facts.json yet. Run: python3 flash.py facts")
    sheet = json.load(open(fp, encoding="utf-8"))
    if sheet.get("guidance_source") not in ("release", "transcript"):
        _die("guides is only for a run whose guidance_source is release or transcript; the feed tags these.")
    try:
        entries = json.load(open(path, encoding="utf-8"))
        assert isinstance(entries, list) and all(isinstance(e, dict) and e.get("guide") for e in entries)
    except Exception:
        _die('that file is not a JSON list like [{"ltcode": "IS_P_101", "period": "2Q27", "guide": "$6.80B-$6.85B"}]')
    tagged = nt.tag_release_guides(entries, sheet.get("prior_guides"), sheet.get("pinned_consensus"))
    shown, dropped = nt.rank_guides(tagged, {r.get("ltcode") for r in sheet.get("rows") or []})
    tagged = shown + [dict(g, dropped=True) for g in dropped]
    with open(os.path.join(d, "release_guides.json"), "w", encoding="utf-8") as f:
        json.dump(tagged, f, indent=1, ensure_ascii=False)
    sheet["release_guides"] = tagged
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(sheet, f, indent=1, ensure_ascii=False)
    for r in tagged:
        if r.get("dropped"):
            print("  NOT ON THE PAGE (ranked out): %s %s: %s" % (r["period_label"], r["lineitem"], r["guide"]))
        elif r["by"] == "code":
            print("  [%s] %s %s: %s (was %s) - tagged by code, use it%s%s" % (
                r["tag"], r["period_label"], r["lineitem"], r["guide"], r["prior_guide"],
                ("; " + r["note"]) if r.get("note") else "",
                ("; vs. " + nt.pre_cons(r["consensus"], r["consensus_as_of"])) if r.get("consensus") else ""))
        else:
            print("  [?] %s %s: %s - %s" % (r["period_label"], r["lineitem"], r["guide"], r["why"]))
    print("NEXT: write %s/narrative.json with these tags (writing-rules.md), then: python3 flash.py build" % d)


def cmd_template(d, st, only_failed=False):
    import narrative_template as nt
    if not os.path.isfile(os.path.join(d, "facts.json")):
        cmd_facts(d, st)
    sheet = json.load(open(os.path.join(d, "facts.json"), encoding="utf-8"))
    draft = nt.build_narrative(sheet)
    path = os.path.join(d, "narrative.json")
    if only_failed and os.path.isfile(path):
        cur = json.load(open(path, encoding="utf-8"))
        gsrc = sheet.get("guidance_source")
        for k in st.get("failed_fields") or []:
            if k == "guidance" and gsrc in ("release", "transcript"):
                print("guidance is left as you wrote it: rewrite it from the parsed document, using the "
                      "tags and order flash.py guides printed (consensus only where it printed one).")
                continue
            if k in draft:
                cur[k] = draft[k]
        draft = cur
    with open(path, "w", encoding="utf-8") as f:
        json.dump(draft, f, indent=1, ensure_ascii=False)
    print("wrote %s (rule-based%s)." % (path, ", failed fields only" if only_failed else ""))
    print("NEXT: python3 flash.py build")


_FIELD_OF = re.compile(r"\b(flashVerdict|investmentRead|whatMatters|variantView|guidance|forwardView|nextWatchItems)\b")


def cmd_build(d, st, argv):
    out = next((a for a in argv if a.endswith(".docx")), None)
    flags = [a for a in argv if a.startswith("--") and a != "--dir"]
    if not out:
        out = os.path.join(d, "%s_DOC_EARNINGS_FLASH_%s.docx" % (st["ticker"], _dt.datetime.now().strftime("%y%m%d_%H%M%S")))
    nar = os.path.join(d, "narrative.json")
    if not os.path.isfile(nar):
        _die("no narrative.json in %s. Write it from facts.json, or run: python3 flash.py template" % d)
    rc, res = _pipeline(d, nar, out, flags)
    print(json.dumps(res, indent=1)[:8000])
    if res.get("status") == "success":
        st["builds_failed"] = 0
        _save_state(d, st)
        print("NEXT: check the page (python3 flash.py verify %s) and report the file." % out)
        return 0
    if res.get("status") == "needs_user_decision":
        print("NEXT: ask the user the question in 'facts' above, in plain words. Do not re-run until they answer.")
        return rc
    st["builds_failed"] = int(st.get("builds_failed") or 0) + 1
    failed = sorted(set(_FIELD_OF.findall(str(res.get("message", "")))))
    st["failed_fields"] = failed
    _save_state(d, st)
    if st["builds_failed"] >= 2 and failed:
        print("Two builds failed on the narrative (%s)." % ", ".join(failed))
        print("NEXT: python3 flash.py template --failed    (rewrites ONLY those fields by rule), then build again")
    else:
        print("NEXT: fix what the message names, then: python3 flash.py build")
    return rc or 1


def _docx_text(path):
    import zipfile
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8")
    except Exception:
        return ""
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"</w:tc>", " | ", xml)
    txt = re.sub(r"<[^>]+>", "", xml)
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'")):
        txt = txt.replace(a, b)
    return txt


def cmd_verify(d, st, docx):
    base = docx[:-5] if docx.endswith(".docx") else docx
    pdf = base + ".pdf" if os.path.isfile(base + ".pdf") else None
    comp = None
    sc = os.path.join(os.path.dirname(os.path.abspath(docx)), "_pipeline_scratch")
    if os.path.isdir(sc):
        tags = sorted((os.path.join(sc, x) for x in os.listdir(sc)), key=os.path.getmtime)
        for t in reversed(tags):
            if os.path.isfile(os.path.join(t, "computed.json")):
                comp = os.path.join(t, "computed.json")
                break
    checks = [("render gate", [sys.executable, os.path.join(HERE, "render_gate.py"), base + ".docx"]
               + (["--pdf", pdf] if pdf else []) + (["--computed", comp] if comp else [])),
              ("table geometry", [sys.executable, os.path.join(HERE, "check_table_widths.py"), base + ".docx"])]
    if pdf:
        checks.append(("masthead geometry", [sys.executable, os.path.join(HERE, "check_masthead.py"), pdf]))
    bad = 0
    for label, cmd in checks:
        p = subprocess.run(cmd, capture_output=True, text=True)
        out_lines = (p.stdout or "").strip().splitlines()
        tail = (out_lines[0] if label == "render gate" else out_lines[-1]) if out_lines else (p.stderr or "").strip()[-160:]
        state = "ok" if p.returncode == 0 else ("UNVERIFIED (font not installed - expected in a sandbox)"
                                               if label == "masthead geometry" and p.returncode == 2 else "FAIL")
        if state == "FAIL":
            bad += 1
        print("%-18s %s  %s" % (label, state, tail[:160]))
    text = ""
    if pdf:
        try:
            text = subprocess.run(["pdftotext", "-layout", pdf, "-"], capture_output=True, text=True).stdout
        except Exception:
            text = ""
        try:
            info = subprocess.run(["pdfinfo", pdf], capture_output=True, text=True).stdout
            print("pages: %s" % (re.search(r"Pages:\s+(\d+)", info).group(1) if "Pages:" in info else "unknown"))
        except Exception:
            pass
    if not text:
        text = _docx_text(base + ".docx")
    print("placeholders: %d | ticker %s in masthead: %s" % (len(re.findall(r"\[INSERT\]|TODO", text)), st["ticker"],
                                                         ("(%s)" % st["ticker"]) in text))
    try:
        import narrative_template as nt
        sheet = json.load(open(os.path.join(d, "facts.json"), encoding="utf-8"))
        nar = json.load(open(os.path.join(d, "narrative.json"), encoding="utf-8"))
        bw = nt.bench_prose_check(nar, sheet)
        oo = sheet.get("one_off")
        if oo and not re.search(r"one-?offs?\b|one-?time|non-?recurring|\bgains?\b|special items",
                                json.dumps(nar), re.I):
            bw.append("[one-off items] the fact sheet says %s includes %s of one-off items, but the prose "
                      "never says so - the revenue beat reads as clean." % (oo["revenue_row"], oo["difference"]))
        print("prose checks       %s" % ("ok" if not bw else "WARN - check these, then fix or explain:"))
        for w in bw:
            print("  " + w[:240])
    except Exception:
        print("prose checks       not checked (no facts.json or narrative.json in the run folder)")
    low = text.lower()
    need = [("Flash Verdict", "flash verdict"), ("What Matters", "what matters"), ("Variant View", "variant view"),
            ("Guidance Snapshot or Forward View", ("guidance snapshot", "forward view")),
            ("Next Key Watch Items", "next key watch items"), ("disclosure", "general disclosure")]
    if (st.get("facts") or {}).get("guidance_source") == "unavailable":
        need = [n for n in need if not n[0].startswith("Guidance")]
    miss = [label for label, keys in need
            if not any(k in low for k in ((keys,) if isinstance(keys, str) else keys))]
    print("sections           %s" % ("ok - all present, disclosure included" if not miss
                                     else "FAIL - missing: " + ", ".join(miss)))
    if miss:
        bad += 1
    print("----- page text (%d characters, complete) -----" % len(text))
    print(text)
    print("-----------------------------------------------")
    print("NEXT: confirm the right company and quarter, that every row closes (actual - estimate = delta), "
          "that units are intact, and that every number in the prose matches the table. Then deliver the file.")
    return 1 if bad else 0


def cmd_start(argv):
    if not argv:
        _die("usage: python3 flash.py start TICKER [--period P] [--dir DIR]")
    t = argv[0].upper()
    d = _dir_from_args(argv) or "flash_%s" % re.sub(r"[^A-Z0-9]", "_", t)
    os.makedirs(os.path.join(d, "responses"), exist_ok=True)
    st = {"ticker": t, "created": time.time(), "calls": [], "facts": {}}
    if "--period" in argv:
        p = argv[argv.index("--period") + 1]
        st["period"], st["prior_period"], st["user_period"] = p, prior_label(p), True
    _save_state(d, st)
    print("run started in %s" % d)
    cmd_next(d, st)


class _FullPath(object):
    def __init__(self, inner):
        self.inner = inner
        q = lambda p: '"%s"' % p if re.search(r"\s", p) else p
        self.me = "%s %s" % (q(sys.executable or "python3"), q(os.path.abspath(__file__)))

    def write(self, text):
        text = text.replace("python3 flash.py", self.me)
        enc = getattr(self.inner, "encoding", None) or "utf-8"
        try:
            text.encode(enc)
        except (UnicodeEncodeError, LookupError):
            text = text.translate({0x2212: "-", 0x2009: " ", 0x202F: " ", 0x00A0: " ", 0x2011: "-"})
            try:
                text = text.encode(enc, "replace").decode(enc)
            except LookupError:
                pass
        return self.inner.write(text)

    def flush(self):
        return self.inner.flush()


def main(argv):
    sys.stdout = _FullPath(sys.stdout)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "start":
        return cmd_start(rest)
    d = _find_state_dir(rest)
    st = _load(d)
    rest = [a for i, a in enumerate(rest) if a != "--dir" and (i == 0 or rest[i - 1] != "--dir")]
    if "--allow-stale-period" in rest and not st.get("allow_stale_period"):
        st["allow_stale_period"] = True
        _save_state(d, st)
    if cmd == "next":
        return cmd_next(d, st) or 0
    if cmd == "save" and len(rest) >= 2:
        return cmd_save(d, st, rest[0], rest[1]) or 0
    if cmd == "set" and len(rest) >= 2:
        return cmd_set(d, st, rest[0], " ".join(rest[1:])) or 0
    if cmd == "parsed" and len(rest) >= 3:
        return cmd_parsed(d, st, rest[0], rest[1], rest[2], rest[3] if len(rest) > 3 else None) or 0
    if cmd == "assemble":
        return cmd_assemble(d, st)
    if cmd == "facts":
        return cmd_facts(d, st) or 0
    if cmd == "template":
        return cmd_template(d, st, "--failed" in rest) or 0
    if cmd == "build":
        return cmd_build(d, st, rest)
    if cmd == "verify" and rest:
        return cmd_verify(d, st, rest[0]) or 0
    if cmd == "eightk" and rest:
        return cmd_eightk(d, st, rest[0]) or 0
    if cmd == "guides" and rest:
        return cmd_guides(d, st, rest[0]) or 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
