#!/usr/bin/env python3
"""
mcp_guard_epp.py - the earnings-preview-portal rules, layered on the SHARED mcp_guard.py.

WHY THIS IS A SEPARATE FILE
---------------------------
`mcp_guard.py` is byte-identical in earnings-wrap, earnings-flash-skill and this skill. That is the
whole value of it: a trap measured on any one of the three immediately protects the other two.
Editing it here would fork it and end that. So the shared traps stay there, untouched, and the
traps that are specific to THIS skill's call surface live here, on top.

    check() = mcp_guard.check()  THEN  the EPP rules below

WHAT EPP ADDS, AND WHY EACH ONE IS HERE
---------------------------------------
The shared file guards the two skills' surface: explore-mode screens, the company= hang, the
5 MB parse ceiling, the semantic-batch ceiling. EPP retired peer fetching so it
never screens, but it hammers `get_estimates` / `get_actuals` / `search_lineitem` far harder
than either sibling, and that surface has its own measured failures:

  E1  search_lineitem limit > 20              REJECTED server-side: "'limit' <= 20. Got: 30"
                                              killed a whole no-filter batch.
  E2  get_estimates/get_actuals limit > 100   REJECTED: "'limit' <= 100. Got: 200" killed the
                                              §7 snapshot call - the one call the entire
                                              beat-credibility rubric depends on.
  E3  more than one ticker_id in a value call  The MCP converts the WHOLE response to USD when
                                              it sees several tickers. Verified on a KRW reporter:
                                              revenue is KRW140.0000T single-ticker
                                              and $100.0000B in a five-ticker batch. compute
                                              refuses a mixed-basis run (exit 3), but by then
                                              the fetch is already spent.
  E4  latest{enabled:true} + updatedate_*      NON-DETERMINISTIC, which is worse than a hard
                                              error: row counts vary wildly across tickers
                                              and across repeats of one window. Zero
                                              rows from it looks exactly like "no estimates
                                              exist". §5 says use enabled:false with a window.
  E5  fiscal_period "FY27" on an annual ask    REJECTED: "Invalid fiscal period 'FY27'".
                                              Annual periods are BARE YEARS.
  E6  a CONSTRUCTED derived -Y/-Q/-M code      get_estimates validates ltcodes ALL-OR-NOTHING:
                                              one code the ticker does not carry fails the
                                              WHOLE request and returns nothing for the codes
                                              that were fine, each costing a whole batch and
                                              a re-fetch.

                                              Batch size does not predict the fault: large
                                              derived batches succeed. What does: a failing
                                              code is CONSTRUCTED by
                                              appending -Y/-Q/-M to a base code, never returned
                                              by a search_lineitem payload. The searches DO
                                              return the derived twins that exist (IS_P_101-Y,
                                              IS_P_110-Y, IS_P_128-Y...), so "did the database
                                              hand me this code?" is answerable offline - the
                                              same learn-then-check shape the shared file uses
                                              for subindustry labels. Batch size is now an
                                              advisory note, not a refusal.
  E7  parse_document_content on a non-transcript
                                              EPP's three parse passes are defined over
                                              TRANSCRIPT files (data-collection.md
                                              §Transcripts, passes A/B/C). A release, a
                                              presentation or a 10-Q carries no Q&A and no
                                              management commentary, so parsing one in place
                                              of a transcript builds Tone Shift from a press
                                              release. Measured on 10 document trees: the >5MB
                                              files in InSync are presentations and 10-Qs,
                                              never transcripts (max seen 2.89MB) -
                                              so the shared 5MB rule and this one interact:
                                              the ceiling drops the big file, and this rule
                                              stops the ladder falling through to a document
                                              that cannot answer the prompt.
  E8  search_tickerid batched with anything    §0: resolution calls go ONE per request. The
                                              shared file already strips company=; this keeps
                                              the call alone, which is the other half of §1.

SUBJECT IS NEVER CHANGED. Every rule refuses or splits. None rewrites which issuer, period,
ltcode or metric you asked for, so a call that passes reaches the server asking for exactly
what it asked for before. A replay of real calls (internal edition only) proves it.

USAGE
-----
    python mcp_guard_epp.py learn <search_lineitem payload.json>   # after each Step 0 batch
    python mcp_guard_epp.py check '<tool_calls JSON>'     # or --file calls.json
    python mcp_guard_epp.py selftest

Exit codes match the shared file: 0 safe, 2 MUST SPLIT (stdout is a list of groups), 3 REFUSED.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mcp_guard
from mcp_guard import Refuse

EPP_STATE = os.environ.get("MCP_GUARD_EPP_STATE") or os.path.join(os.getcwd(), ".mcp_guard_epp_state.json")

LIMIT_CAP = {"search_lineitem": 20, "get_estimates": 100, "get_actuals": 100}
VALUE_CALLS = ("get_estimates", "get_actuals")
DERIVED_BATCH_MAX = 4
TRANSCRIPT_HINT = "transcript"


def _known_ltcodes():
    try:
        with open(EPP_STATE, encoding="utf-8") as fh:
            return set(json.load(fh).get("ltcodes") or [])
    except Exception:
        return set()


def learn(payload):
    known = _known_ltcodes()
    n = 0
    for d in mcp_guard._walk(payload):
        for key in ("ltcode", "LTCODE"):
            v = d.get(key)
            if isinstance(v, str) and v.strip():
                known.add(v.strip())
                n += 1
    try:
        with open(EPP_STATE, "w", encoding="utf-8") as fh:
            json.dump({"ltcodes": sorted(known)}, fh, indent=1)
    except Exception:
        pass
    return {"learned_rows": n, "known_ltcodes": len(known)}


def _codes(args):
    lt = args.get("ltcode")
    if isinstance(lt, (list, tuple)):
        return [str(x).strip() for x in lt if str(x).strip()]
    return [c.strip() for c in str(lt or "").split(",") if c.strip()]


def _tickers(args):
    t = args.get("ticker_id")
    if isinstance(t, (list, tuple)):
        return [str(x).strip() for x in t if str(x).strip()]
    return [x.strip() for x in str(t or "").split(",") if x.strip()]


def _epp_rules(calls, notes):
    out = []
    split_later = []
    for c in calls:
        name = c["tool_name"]
        a = dict(c.get("tool_arguments") or {})

        cap = LIMIT_CAP.get(name)
        if cap is not None and a.get("limit") not in (None, ""):
            try:
                lim = int(str(a["limit"]))
            except ValueError:
                lim = None
            if lim is not None and lim > cap:
                raise Refuse(
                    "%s: limit=%s. The server REJECTS this outright ('limit' <= %d) and returns "
                    "nothing, which reads as missing data rather than a bad argument. Paginate "
                    "with offset instead of raising limit." % (name, a["limit"], cap))

        if name in VALUE_CALLS and len(_tickers(a)) > 1:
            raise Refuse(
                "%s: %d ticker_ids in one call. The MCP converts the WHOLE response to USD when "
                "it sees several tickers, so a KRW actual lands beside a USD estimate in the same "
                "printed row (verified on a KRW reporter: KRW140.0000T single-ticker, $100.0000B in a "
                "five-ticker batch). compute refuses a mixed-basis run, but the fetch is already "
                "spent. One ticker per call." % (name, len(_tickers(a))))

        if name == "get_estimates":
            latest = a.get("latest")
            enabled = isinstance(latest, dict) and latest.get("enabled") is True
            windowed = bool(a.get("updatedate_start") or a.get("updatedate_end"))
            if enabled and windowed:
                raise Refuse(
                    "get_estimates: latest{enabled:true} together with an updatedate window. "
                    "Measured non-deterministic across windows - a few rows on one issuer, one on another, 0 then "
                    "dozens on a third - and zero rows from it looks exactly like 'no estimates exist'. "
                    "Use latest{enabled:false} with the window (data-collection.md §5/§7), and "
                    "only fall back to latest{enabled:true,limit:1} on a window that is genuinely "
                    "empty, saying so in the report.")

            fp = str(a.get("fiscal_period") or "")
            bad = [p.strip() for p in fp.split(",")
                   if p.strip().upper().startswith("FY") and p.strip()[2:].isdigit()]
            if bad:
                raise Refuse(
                    "get_estimates: fiscal_period %s. The server rejects 'FY27' with "
                    "\"Invalid fiscal period\". Annual periods are BARE YEARS ('2027'); the "
                    "\"FY27 Guide\" column header is built from the bare year downstream."
                    % ", ".join(repr(b) for b in bad))

        if name in VALUE_CALLS:
            derived = [c2 for c2 in _codes(a) if c2.endswith(("-Y", "-Q", "-M"))]
            known = _known_ltcodes()
            if derived and known:
                unproved = [d for d in derived if d not in known]
                proved = [d for d in _codes(a) if d not in unproved]
                if unproved and proved:
                    split_later.append((len(out), proved, unproved))

        if name == "parse_document_content":
            paths = a.get("file_paths") or []
            if paths and not any(TRANSCRIPT_HINT in str(p).lower() for p in paths):
                raise Refuse(
                    "parse_document_content: no file_path looks like a transcript (%s). EPP's "
                    "three passes are defined over TRANSCRIPT files - A the latest call, B the "
                    "prior call, C one per call for the Q&A. A release, a presentation or a 10-Q "
                    "carries no Q&A and no management commentary, so parsing one in its place "
                    "builds Call Commentary and Tone Shift from a press release. If the "
                    "transcript is genuinely absent or was dropped by the 5 MB ceiling, that is "
                    "the degradation path in degradation-matrix.md, not a substitution."
                    % ", ".join(str(p) for p in paths))

        out.append({"tool_name": name, "tool_arguments": a})

    if split_later:
        rebuilt, moved = [], {}
        for idx, proved, unproved in split_later:
            base = out[idx]
            good = dict(base["tool_arguments"]); good["ltcode"] = ",".join(proved)
            risky = dict(base["tool_arguments"]); risky["ltcode"] = ",".join(unproved)
            moved[idx] = ({"tool_name": base["tool_name"], "tool_arguments": good},
                          {"tool_name": base["tool_name"], "tool_arguments": risky})
            notes.append("SPLIT: %d derived code(s) (%s) were never returned by a "
                         "search_lineitem payload in this run, and were sharing a request with "
                         "%d proved code(s). get_estimates validates ltcodes ALL-OR-NOTHING, so "
                         "one absent code returns nothing for the ones that were fine - measured "
                         "three times in 158 calls (KM_P_303-Y, CF_S_431-Y, IS_P_209-Q), each "
                         "losing a whole batch. The proved codes now go together and the "
                         "unproved ones go alone, where a miss costs only itself."
                         % (len(unproved), ", ".join(unproved), len(proved)))
        first = [moved[i][0] if i in moved else c for i, c in enumerate(out)]
        second = [moved[i][1] for i in sorted(moved)]
        return "split", [first, second]

    res = [i for i, c in enumerate(out) if c["tool_name"] == "search_tickerid"]
    if res and len(out) > 1:
        groups = [[out[i]] for i in res] + [[c for i, c in enumerate(out) if i not in res]]
        groups = [g for g in groups if g]
        notes.append("SPLIT: search_tickerid was batched with %d other call(s). §0 sends a "
                     "resolution call ONE per request - a hang there destroys its batch-mates, "
                     "and a numeric non-US code (PETR4) hung to the ceiling twice and took the "
                     "other operation with it. Sending %d requests in sequence."
                     % (len(out) - len(res), len(groups)))
        return "split", groups
    return "ok", out


def check(tool_calls):
    status, payload, notes = mcp_guard.check(tool_calls)
    if status == "split":
        groups = []
        for g in payload:
            st, out = _epp_rules(g, notes)
            groups.extend(out if st == "split" else [out])
        return "split", groups, notes
    st, out = _epp_rules(payload, notes)
    return st, out, notes




def selftest():
    fails = []

    def ok(label, cond):
        if not cond:
            fails.append(label)
        print(("  PASS  " if cond else "  FAIL  ") + label)

    def refused(calls):
        try:
            check(calls)
            return None
        except Refuse as e:
            return str(e)

    print("mcp_guard_epp selftest")
    print("-" * 78)

    ok("search_lineitem limit>20 refused",
       refused([{"tool_name": "search_lineitem",
                 "tool_arguments": {"ticker_id": "62", "queries": "revenue", "limit": "30"}}]))
    ok("get_estimates limit>100 refused",
       refused([{"tool_name": "get_estimates",
                 "tool_arguments": {"ticker_id": "62", "ltcode": "IS_P_101", "limit": "200"}}]))
    st, p, _ = check([{"tool_name": "get_estimates",
                       "tool_arguments": {"ticker_id": "62", "ltcode": "IS_P_101", "limit": "100"}}])
    ok("limit exactly at the cap passes", st == "ok")

    ok("multi-ticker get_actuals refused",
       refused([{"tool_name": "get_actuals",
                 "tool_arguments": {"ticker_id": "62,35", "ltcode": "IS_P_101"}}]))
    st, p, _ = check([{"tool_name": "get_actuals",
                       "tool_arguments": {"ticker_id": "62", "ltcode": "IS_P_101"}}])
    ok("single-ticker get_actuals passes", st == "ok")

    ok("latest{enabled:true} + updatedate window refused",
       refused([{"tool_name": "get_estimates",
                 "tool_arguments": {"ticker_id": "62", "ltcode": "IS_P_101",
                                    "latest": {"enabled": True, "limit": 1},
                                    "updatedate_start": "2026-01-01"}}]))
    st, p, _ = check([{"tool_name": "get_estimates",
                       "tool_arguments": {"ticker_id": "62", "ltcode": "IS_P_101",
                                          "latest": {"enabled": False},
                                          "updatedate_start": "2026-01-01"}}])
    ok("enabled:false with a window passes (the documented §5 form)", st == "ok")
    st, p, _ = check([{"tool_name": "get_estimates",
                       "tool_arguments": {"ticker_id": "62", "ltcode": "IS_P_101",
                                          "latest": {"enabled": True, "limit": 1}}}])
    ok("latest{enabled:true} with NO window passes (the §4 form)", st == "ok")

    ok("fiscal_period FY27 refused",
       refused([{"tool_name": "get_estimates",
                 "tool_arguments": {"ticker_id": "62", "ltcode": "IS_P_101",
                                    "fiscal_period": "3Q26,FY27"}}]))
    st, p, _ = check([{"tool_name": "get_estimates",
                       "tool_arguments": {"ticker_id": "62", "ltcode": "IS_P_101",
                                          "fiscal_period": "3Q26,2027"}}])
    ok("bare-year 2027 passes", st == "ok")

    import tempfile as _tf
    fd, st_tmp = _tf.mkstemp(suffix=".json"); os.close(fd)
    global EPP_STATE
    EPP_STATE = st_tmp
    learn({"data": [{"ltcode": c} for c in
                    ["IS_P_101", "IS_P_101-Y", "IS_P_110-Y", "IS_P_128-Y", "KM_P_301-Y",
                     "IS_P_306-Y", "IS_S_114-Y", "KM_P_301", "KM_P_303"]]})

    st, groups, notes = check([{"tool_name": "get_estimates",
                                "tool_arguments": {"ticker_id": "62",
                                                   "ltcode": "KM_P_301-Y,KM_P_303-Y,IS_P_306-Y,IS_S_114-Y",
                                                   "fiscal_period": "3Q26"}}])
    ok("a batch mixing proved and UNPROVED derived codes is SPLIT, not refused",
       st == "split" and len(groups) == 2)
    if st == "split":
        risky = groups[1][0]["tool_arguments"]["ltcode"]
        safe = groups[0][0]["tool_arguments"]["ltcode"]
        ok("the unproved code goes alone, where a miss costs only itself", risky == "KM_P_303-Y")
        ok("the proved codes stay together and are not lost",
           set(safe.split(",")) == {"KM_P_301-Y", "IS_P_306-Y", "IS_S_114-Y"})
    ok("the note names the measured losses", any("KM_P_303-Y" in n for n in notes))

    st, p, _ = check([{"tool_name": "get_estimates",
                       "tool_arguments": {"ticker_id": "62",
                                          "ltcode": "KM_P_301-Y,IS_P_306-Y,IS_S_114-Y,IS_P_101-Y,IS_P_110-Y,IS_P_128-Y",
                                          "fiscal_period": "3Q26"}}])
    ok("an all-proved derived batch passes untouched however large (size is not the fault)",
       st == "ok")
    st, p, _ = check([{"tool_name": "get_estimates",
                       "tool_arguments": {"ticker_id": "62",
                                          "ltcode": "IS_P_101,IS_P_110,IS_P_128,IS_P_117,IS_P_118,IS_P_123",
                                          "fiscal_period": "3Q26"}}])
    ok("BASE codes are never touched - the rule is about derived twins only", st == "ok")
    st, groups, _ = check([{"tool_name": "get_actuals",
                            "tool_arguments": {"ticker_id": "62",
                                               "ltcode": "KM_P_301-Y,KM_P_303-Y,IS_P_306-Y",
                                               "fiscal_period": "3Q26"}}])
    ok("get_actuals validates all-or-nothing too, so it is split the same way",
       st == "split" and groups[1][0]["tool_name"] == "get_actuals"
       and groups[1][0]["tool_arguments"]["ltcode"] == "KM_P_303-Y"
       and set(groups[0][0]["tool_arguments"]["ltcode"].split(",")) == {"KM_P_301-Y", "IS_P_306-Y"})
    try:
        os.unlink(st_tmp)
    except Exception:
        pass

    ok("a release parsed in place of a transcript is refused",
       refused([{"tool_name": "parse_document_content",
                 "tool_arguments": {"file_paths": ["IS/EXM/2026/2Q26/EXM 2Q26 Earnings Release.pdf"],
                                    "user_prompt": "..."}}]))
    st, p, _ = check([{"tool_name": "parse_document_content",
                       "tool_arguments": {"file_paths": ["IS/EXM/2026/2Q26/EXM 2Q26 Earnings Transcript.pdf"],
                                          "user_prompt": "..."}}])
    ok("a transcript passes", st == "ok")
    st, p, _ = check([{"tool_name": "parse_document_content",
                       "tool_arguments": {"ticker_id": "62", "user_prompt": "...",
                                          "file_paths": ["IS/EXM/2026/2Q26/EXM 2Q26 Earnings Transcript.pdf"]}}])
    ok("a stray ticker_id on a parse is removed, nothing else changes",
       st == "ok" and p[0]["tool_arguments"] == {"user_prompt": "...",
                                                 "file_paths": ["IS/EXM/2026/2Q26/EXM 2Q26 Earnings Transcript.pdf"]})

    st, groups, notes = check([
        {"tool_name": "search_tickerid", "tool_arguments": {"ticker": "EXM"}},
        {"tool_name": "get_call_date", "tool_arguments": {"ticker_id": "62"}}])
    ok("search_tickerid batched with another call is split",
       st == "split" and len(groups) == 2 and groups[0][0]["tool_name"] == "search_tickerid")
    st, p, _ = check([{"tool_name": "search_tickerid", "tool_arguments": {"ticker": "EXM"}}])
    ok("search_tickerid alone passes", st == "ok")

    st, p, notes = check([{"tool_name": "search_tickerid",
                           "tool_arguments": {"ticker": "TSCO", "company": "Tesco"}}])
    ok("the SHARED company= strip still fires under the EPP layer",
       st == "ok" and p[0]["tool_arguments"] == {"ticker": "TSCO"})

    src = {"tool_name": "get_actuals",
           "tool_arguments": {"ticker_id": "62", "ltcode": "IS_P_101,IS_P_110",
                              "fiscal_period": "1Q25,2Q25", "limit": "100"}}
    ok("a compliant call passes through byte-identical",
       check([json.loads(json.dumps(src))])[1][0] == src)

    print("-" * 78)
    if fails:
        print("FAILED %d: %s" % (len(fails), "; ".join(fails)))
        return 1
    print("all checks passed")
    return 0


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    if argv[1] == "selftest":
        return selftest()
    if argv[1] == "learn":
        if len(argv) < 3:
            sys.stderr.write("learn needs a payload file\n")
            return 2
        print(json.dumps(learn(json.load(open(argv[2], encoding="utf-8"))), indent=1))
        return 0
    if argv[1] == "check":
        if len(argv) >= 4 and argv[2] == "--file":
            calls = json.load(open(argv[3], encoding="utf-8"))
        elif len(argv) >= 3:
            calls = json.loads(argv[2])
        else:
            calls = json.load(sys.stdin)
        try:
            status, payload, notes = check(calls)
        except Refuse as e:
            sys.stderr.write("REFUSED: %s\n" % e)
            return 3
        for n in notes:
            sys.stderr.write("guard: %s\n" % n)
        print(json.dumps(payload, indent=1))
        return 0 if status == "ok" else 2
    sys.stderr.write("unknown command %r\n" % argv[1])
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
