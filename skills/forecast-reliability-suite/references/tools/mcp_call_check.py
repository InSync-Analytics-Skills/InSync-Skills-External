#!/usr/bin/env python3
"""mcp_call_check.py - validate and rewrite InSync MCP call arguments BEFORE the call goes out.

WHY THIS IS CODE AND NOT PROSE
------------------------------
SKILL.md Step 2b already states every rule below. They are still followed unevenly, because they
are prose and a model has to recall them at the moment it writes the `tool_calls` block.

More importantly: a timeout is the ONE failure class in this skill that cannot be gated after the
fact. Every other check - the consensus cutoff, polarity, reconciliation, the sufficiency gate -
inspects data that arrived. A timeout leaves nothing behind to inspect. It has to be stopped at
call construction or not at all.

NO NETWORK. It never issues a call. It rewrites or refuses an argument dict; the caller sends the
corrected version.

SCOPE - deliberately narrower than the general InSync call guard
----------------------------------------------------------------
That guard defends seven traps, five of which live on the explore/screening surface
(`mode="explore"`, `country=`, `region=`, `insync_subindustry=`, oversized document parses). This
skill is single-ticker and never touches any of them, so importing those rules would be
machinery guarding a door this skill does not have. Only the rules that apply here are
implemented, and the ones that do not are listed in NOT_APPLICABLE below so a reader can see
they were considered rather than missed.

RULES ENFORCED
  1. `company` alongside (or instead of) `ticker` on search_tickerid    -> dropped / refused
     A ticker alone can resolve exact_ticker 100.0 while the company name for the same issuer
     returns `Ambiguous matches` offering unrelated companies, and the company parameter can hang
     to the 180s ceiling. There is no upside: this skill accepts only the ticker row.
  4. Two semantic searches in one execute                               -> MUST SPLIT
     The ceiling is per CALL, not per tool, so two searches that each pass alone can die together,
     and one timeout loses every result in the batch. SKILL.md Step 2 asks for two
     `search_lineitem` calls (the top_kpi pull and the widening sweep), which is exactly this case.
  6. More than 5 calls in one execute                                   -> refused (hard cap)
  A. A call whose output feeds the next one, batched with anything      -> MUST SPLIT
     SKILL.md Step 2b's NEVER-batch list, enforced rather than remembered.
  B. Arguments the MCP rejects outright (7.12.0)                        -> refused
     A rejected call is a wasted turn; these are refused here instead, with the fix named:
       search_lineitem      `limit` above 20 (rejected, not clamped)
       get_market_data      `limit` above 100 (the documented maximum; reported rejected in live runs)
       parse_document_content  needs `file_paths` and `user_prompt`
       get_document_tree    needs `latest: true`, or a `year` / `fiscal_period`

NOT_APPLICABLE (present in the general guard, deliberately omitted here)
  2. country= short forms          - this skill never passes country
  3. region= on explore            - this skill never uses mode="explore"
  5. typed insync_subindustry      - this skill never screens by subindustry
  7. parse_document_content >5MB   - not implemented. Step 3d does parse filings (to confirm a
                                     surprise above 50%); keep each parse to one document.

USAGE
    python3 mcp_call_check.py '<tool_calls JSON>'
    python3 mcp_call_check.py --file calls.json
    python3 mcp_call_check.py selftest          # offline

EXIT CODES
    0  safe. Corrected call JSON on stdout. Any rewrite explained on stderr.
    2  MUST SPLIT. stdout carries a JSON list of groups, to send one after another.
    3  REFUSED. Nothing safe to send. Read stderr.
"""
import json
import sys

MAX_CALLS_PER_EXECUTE = 5
LIMIT_MAX = {"search_lineitem": 20, "get_market_data": 100}
LIMIT_HINT = {"search_lineitem": "split the sweep across several calls",
              "get_market_data": "narrow the date window, or split it across calls"}

SEMANTIC = {"search_lineitem"}
SEQUENTIAL = {"search_tickerid", "search_lineitem", "search_by_lineitem", "parse_document_content"}


class Refuse(Exception):
    pass


def _guard_search_tickerid(args, notes):
    a = dict(args)
    if a.get("company") and a.get("ticker"):
        notes.append(
            "search_tickerid: dropped company=%r, kept ticker=%r. The company arm adds no field "
            "this skill uses and adds an ambiguity path: a ticker resolves "
            "exact_ticker 100.0 while a near-miss company name can return Ambiguous matches "
            "naming unrelated issuers. Resolution is unchanged - Step 1 accepts only an "
            "exact match on the ticker row." % (a["company"], a["ticker"]))
        a.pop("company")
    if a.get("company") and not a.get("ticker"):
        raise Refuse(
            "search_tickerid: company=%r with no ticker. A company string that is not InSync's "
            "canonical spelling returns an ambiguous set with no right answer to fall back on, "
            "and a sibling skill measured this shape hanging to the 180s ceiling. Ask the user "
            "for the symbol and pass ticker alone." % a["company"])
    return a


def _guard_args(name, a):
    if name in LIMIT_MAX and a.get("limit") is not None:
        try:
            lim = int(a["limit"])
        except (TypeError, ValueError):
            lim = None
        if lim is None or lim > LIMIT_MAX[name]:
            raise Refuse("%s: limit=%r. The MCP rejects a limit above %d; pass %d or less and %s."
                         % (name, a["limit"], LIMIT_MAX[name], LIMIT_MAX[name], LIMIT_HINT[name]))
    if name == "parse_document_content":
        miss = [k for k in ("file_paths", "user_prompt") if not a.get(k)]
        if miss:
            raise Refuse("parse_document_content: missing %s. Pass the file path(s) from get_document_tree "
                         "as file_paths and say what to extract in user_prompt." % " and ".join(miss))
    if name == "get_document_tree":
        if not (a.get("latest") in (True, "true", "True") or a.get("year") or a.get("fiscal_period")):
            raise Refuse("get_document_tree: needs latest=true, or a year / fiscal_period to list.")


def check(tool_calls):
    notes = []
    calls = tool_calls if isinstance(tool_calls, list) else [tool_calls]
    out = []
    for c in calls:
        if not isinstance(c, dict) or "tool_name" not in c:
            raise Refuse("each entry needs a tool_name. Got: %r" % (c,))
        name = c["tool_name"]
        args = dict(c.get("tool_arguments") or {})
        if name == "search_tickerid":
            args = _guard_search_tickerid(args, notes)
        _guard_args(name, args)
        out.append({"tool_name": name, "tool_arguments": args})

    if len(out) > MAX_CALLS_PER_EXECUTE:
        raise Refuse("%d calls in one execute. The MCP hard cap is %d."
                     % (len(out), MAX_CALLS_PER_EXECUTE))

    seq = [i for i, c in enumerate(out) if c["tool_name"] in SEQUENTIAL]
    sem = [i for i, c in enumerate(out) if c["tool_name"] in SEMANTIC]
    if len(out) > 1 and seq:
        groups = [[out[i]] for i in seq]
        rest = [c for i, c in enumerate(out) if i not in seq]
        if rest:
            groups.append(rest)
        if len(sem) > 1:
            notes.append(
                "SPLIT: %d semantic searches (%s) were in one execute. The ceiling is per CALL, "
                "not per tool, so two searches that each pass alone can time out together and one "
                "timeout loses every result in the batch. Sending %d groups in sequence."
                % (len(sem), ", ".join(out[i]["tool_name"] for i in sem), len(groups)))
        else:
            notes.append(
                "SPLIT: %s feed(s) the next call and cannot share an execute (SKILL.md Step 2b "
                "NEVER-batch list). Sending %d groups in sequence."
                % (", ".join(out[i]["tool_name"] for i in seq), len(groups)))
        return "split", groups, notes

    return "ok", out, notes


def _selftest():
    p = f = 0

    def ok(name, cond, detail=""):
        nonlocal p, f
        if cond:
            p += 1
            print("  PASS  " + name)
        else:
            f += 1
            print("  FAIL  " + name + ("  -> " + str(detail) if detail else ""))

    st, pay, n = check({"tool_name": "search_tickerid",
                        "tool_arguments": {"ticker": "EXM", "company": "Example Co"}})
    ok("company dropped, ticker kept", st == "ok" and pay[0]["tool_arguments"] == {"ticker": "EXM"}, pay)
    ok("the drop is explained", any("company" in x for x in n))

    try:
        check({"tool_name": "search_tickerid", "tool_arguments": {"company": "Example Co"}})
        ok("bare company refused", False)
    except Refuse:
        ok("bare company refused", True)

    st, pay, n = check([{"tool_name": "search_lineitem", "tool_arguments": {"filter": "top_kpi"}},
                        {"tool_name": "search_lineitem", "tool_arguments": {"queries": "margin"}}])
    ok("two semantic searches are split", st == "split" and len(pay) == 2, pay)
    ok("each group holds one semantic search",
       st == "split" and all(len(g) == 1 for g in pay), pay)
    ok("the split names the per-call ceiling", any("per CALL" in x for x in n))

    st, pay, n = check([{"tool_name": "get_actuals", "tool_arguments": {"ticker_id": "1"}},
                        {"tool_name": "get_estimates", "tool_arguments": {"ticker_id": "1"}},
                        {"tool_name": "get_guidance", "tool_arguments": {"ticker_id": "1"}}])
    ok("keyed getters still batch freely", st == "ok" and len(pay) == 3, (st, pay))
    ok("a clean keyed batch raises no note", not n, n)

    st, pay, n = check([{"tool_name": "search_tickerid", "tool_arguments": {"ticker": "EXM"}},
                        {"tool_name": "get_actuals", "tool_arguments": {"ticker_id": "1"}}])
    ok("a sequential call is split off from keyed ones", st == "split" and len(pay) == 2, pay)
    ok("the keyed call rides in the last group",
       st == "split" and pay[-1][0]["tool_name"] == "get_actuals", pay)

    try:
        check([{"tool_name": "get_actuals", "tool_arguments": {}} for _ in range(6)])
        ok("more than 5 calls refused", False)
    except Refuse:
        ok("more than 5 calls refused", True)

    st, pay, n = check({"tool_name": "get_estimates",
                        "tool_arguments": {"ticker_id": "378", "ltcode": "IS_P_128"}})
    ok("a keyed getter passes through byte-identical",
       st == "ok" and pay[0]["tool_arguments"] == {"ticker_id": "378", "ltcode": "IS_P_128"}, pay)

    for bad, good in (
            ({"tool_name": "search_lineitem", "tool_arguments": {"ticker_id": "1", "limit": 50}},
             {"tool_name": "search_lineitem", "tool_arguments": {"ticker_id": "1", "limit": 20}}),
            ({"tool_name": "get_market_data", "tool_arguments": {"ticker_id": "1", "limit": 250}},
             {"tool_name": "get_market_data", "tool_arguments": {"ticker_id": "1", "limit": 100}}),
            ({"tool_name": "parse_document_content", "tool_arguments": {"file_paths": ["a.pdf"]}},
             {"tool_name": "parse_document_content", "tool_arguments": {"file_paths": ["a.pdf"], "user_prompt": "x"}}),
            ({"tool_name": "get_document_tree", "tool_arguments": {"ticker_id": "1", "latest": False}},
             {"tool_name": "get_document_tree", "tool_arguments": {"ticker_id": "1", "fiscal_period": "2Q26"}})):
        try:
            check(bad)
            ok("refused: %s %s" % (bad["tool_name"], bad["tool_arguments"]), False)
        except Refuse:
            ok("refused: %s %s" % (bad["tool_name"], bad["tool_arguments"]), True)
        st, pay, n = check(good)
        ok("passes untouched: %s" % good["tool_name"], st == "ok" and pay[0]["tool_arguments"] == good["tool_arguments"], pay)
    st, pay, n = check({"tool_name": "get_market_data", "tool_arguments": {"ticker_id": "1", "start_date": "2026-01-01"}})
    ok("get_market_data with no limit passes untouched", st == "ok" and pay[0]["tool_arguments"] == {"ticker_id": "1", "start_date": "2026-01-01"}, pay)
    st, pay, n = check({"tool_name": "get_document_tree", "tool_arguments": {"ticker_id": "1", "latest": True}})
    ok("get_document_tree latest=true passes untouched", st == "ok", pay)

    print("-" * 70)
    print("all checks passed" if not f else f"{f} FAILED")
    return 1 if f else 0


def main(argv):
    if not argv:
        print(__doc__)
        return 3
    if argv[0] == "selftest":
        return _selftest()
    try:
        raw = open(argv[1], encoding="utf-8").read() if argv[0] == "--file" else argv[0]
        calls = json.loads(raw)
    except Exception as e:
        print("could not read tool_calls JSON: %s" % e, file=sys.stderr)
        return 3
    try:
        status, payload, notes = check(calls)
    except Refuse as e:
        print("REFUSED: %s" % e, file=sys.stderr)
        return 3
    for x in notes:
        print(x, file=sys.stderr)
    print(json.dumps(payload, indent=1))
    return 2 if status == "split" else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
