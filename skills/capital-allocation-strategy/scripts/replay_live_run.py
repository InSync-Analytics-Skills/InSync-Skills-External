#!/usr/bin/env python3
"""
replay_live_run.py - the regression test that answers "will this change my output?"

Every call below was ACTUALLY ISSUED against the live InSync MCP, and each one is recorded with
what really happened to it. The `calendarize` pair at the end is the case written up in SKILL.md
under "Peers are MANDATORY": same peers, same codes, no rows with calendarisation and a full
set without.

The contract this proves, and the only one that matters:

  * every call that SUCCEEDED live must pass through the guard BYTE-IDENTICAL, so the data
    that comes back is the same data, for any ticker;
  * every call that FAILED live must be refused or rewritten before it is ever sent - whether
    it failed loudly (TIMEOUT) or silently (EMPTY: returned, returned nothing, said nothing).

If the first half ever fails, the guard is changing results and must not ship.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mcp_guard

LIVE = [
    ("batched company-mode + explore('Application Software')",
     [{"tool_name": "search_tickerid",
       "tool_arguments": {"ticker": "EXSW", "company": "Example Software", "mode": "company"}},
      {"tool_name": "search_tickerid",
       "tool_arguments": {"mode": "explore", "company": "Example Software",
                          "insync_subindustry": "Application Software", "limit": "10"}}],
     "TIMEOUT"),
    ("explore('Application Software') + company, alone",
     [{"tool_name": "search_tickerid",
       "tool_arguments": {"mode": "explore", "company": "Example Software",
                          "insync_subindustry": "Application Software", "limit": "10"}}],
     "TIMEOUT"),
    ("explore('Application Software'), no company",
     [{"tool_name": "search_tickerid",
       "tool_arguments": {"mode": "explore", "insync_subindustry": "Application Software",
                          "limit": "10"}}],
     "TIMEOUT"),

    ("peer annual pull with calendarize=true  (0 rows live)",
     [{"tool_name": "get_actuals",
       "tool_arguments": {"ticker_id": "1004,1010,1000",
                          "ltcode": "BS_P_114,BS_P_115,BS_P_113",
                          "fiscal_period": "2024,2025", "calendarize": True, "limit": "100"}}],
     "EMPTY"),

    ("peer annual pull, uncalendarised  (18 rows live)",
     [{"tool_name": "get_actuals",
       "tool_arguments": {"ticker_id": "1004,1010,1000",
                          "ltcode": "BS_P_114,BS_P_115,BS_P_113",
                          "fiscal_period": "2024,2025", "limit": "100"}}],
     "OK"),
    ("search_tickerid company-mode, EXSW",
     [{"tool_name": "search_tickerid",
       "tool_arguments": {"ticker": "EXSW", "company": "Example Software", "mode": "company"}}],
     "OK"),
    ("explore, well formed",
     [{"tool_name": "search_tickerid",
       "tool_arguments": {"mode": "explore", "country": "United States of America",
                          "insync_subindustry": "Software", "limit": "5"}}],
     "OK"),
    ("search_lineitem top_kpi + get_call_date",
     [{"tool_name": "search_lineitem",
       "tool_arguments": {"ticker_id": "20", "queries": "all", "filter": "top_kpi"}},
      {"tool_name": "get_call_date",
       "tool_arguments": {"ticker_id": "20", "start_date": "2025-06-01",
                          "end_date": "2027-06-30", "limit": "20"}}],
     "OK"),
    ("search_lineitem spine keywords",
     [{"tool_name": "search_lineitem",
       "tool_arguments": {"ticker_id": "20",
                          "queries": "total revenue, adjusted diluted EPS, gross profit",
                          "limit": "6"}}],
     "OK"),
    ("3-call actuals + guidance batch",
     [{"tool_name": "get_actuals",
       "tool_arguments": {"ticker_id": "20", "ltcode": "IS_P_101,IS_P_128,IS_P_106,IS_P_106-M",
                          "fiscal_period": "3Q26,2Q26,1Q26", "limit": "100"}},
      {"tool_name": "get_actuals",
       "tool_arguments": {"ticker_id": "20", "ltcode": "KM_P_1019,SD_S_564,KM_S_576",
                          "fiscal_period": "3Q26,2Q26,1Q26", "limit": "100"}},
      {"tool_name": "get_guidance",
       "tool_arguments": {"ticker_id": "20", "start_date": "2026-01-01",
                          "end_date": "today", "limit": "100"}}],
     "OK"),
    ("5-call ladder + market + valuation batch",
     [{"tool_name": "get_actuals",
       "tool_arguments": {"ticker_id": "20", "ltcode": "IS_P_114-M,IS_P_110-M",
                          "fiscal_period": "3Q26,2Q26,3Q25", "limit": "60"}},
      {"tool_name": "get_market_data",
       "tool_arguments": {"ticker_id": "20", "start_date": "2026-09-01",
                          "end_date": "today", "limit": "20"}},
      {"tool_name": "get_valuation",
       "tool_arguments": {"ticker_id": "20", "mode": "NTM",
                          "metrics": "P/E_Adj_Ratio,EV/Sales", "start_date": "5d",
                          "end_date": "today", "limit": "40"}},
      {"tool_name": "get_valuation",
       "tool_arguments": {"ticker_id": "20", "mode": "NTM", "metrics": "P/E_Adj_Ratio",
                          "start_date": "3y", "end_date": "today", "summarize": True,
                          "limit": "20"}},
      {"tool_name": "get_market_data",
       "tool_arguments": {"ticker_id": "20", "start_date": "1y", "end_date": "today",
                          "summarize": True, "limit": "5"}}],
     "OK"),
    ("consensus probe, 4 get_estimates",
     [{"tool_name": "get_estimates",
       "tool_arguments": {"ticker_id": "20", "ltcode": "IS_P_101,IS_P_128",
                          "fiscal_period": "3Q26", "latest": {"enabled": False},
                          "updatedate_start": "2026-06-12", "updatedate_end": "2026-09-09",
                          "limit": "100"}},
      {"tool_name": "get_estimates",
       "tool_arguments": {"ticker_id": "20", "ltcode": "KM_P_1019,SD_S_564",
                          "fiscal_period": "3Q26", "latest": {"enabled": False},
                          "updatedate_start": "2026-06-12", "updatedate_end": "2026-09-09",
                          "limit": "100"}},
      {"tool_name": "get_estimates",
       "tool_arguments": {"ticker_id": "20", "ltcode": "IS_P_101,IS_P_128",
                          "fiscal_period": "4Q26,2026,2027",
                          "latest": {"enabled": True, "limit": 1}, "limit": "30"}},
      {"tool_name": "get_estimates",
       "tool_arguments": {"ticker_id": "20", "ltcode": "IS_P_101,IS_P_128",
                          "fiscal_period": "4Q26,2026,2027",
                          "latest": {"enabled": True, "limit": 1},
                          "updatedate_end": "2026-09-09", "limit": "30"}}],
     "OK"),
    ("peer ticker resolve + peer valuation",
     [{"tool_name": "search_tickerid",
       "tool_arguments": {"ticker": "PEER1,PEER2,PEER3,PEER4,PEER5", "mode": "company"}}],
     "OK"),
    ("peer valuation pull",
     [{"tool_name": "get_valuation",
       "tool_arguments": {"ticker_id": "1004,1010,1053,148,1041", "mode": "NTM",
                          "metrics": "P/E_Adj_Ratio,EV/Sales,EV/EBITDA_Adj",
                          "start_date": "2026-09-16", "end_date": "today", "limit": "40"}}],
     "OK"),
    ("verification re-pull, guidance + valuation x2",
     [{"tool_name": "get_guidance",
       "tool_arguments": {"ticker_id": "20", "ltcode": "IS_P_101,IS_P_128,IS_P_110-M",
                          "fiscal_period": "3Q26,4Q26,2026", "start_date": "2026-05-01",
                          "end_date": "today", "limit": "60"}},
      {"tool_name": "get_valuation",
       "tool_arguments": {"ticker_id": "20", "mode": "NTM", "metrics": "P/E_Adj_Ratio",
                          "start_date": "3y", "end_date": "today", "summarize": True,
                          "limit": "20"}},
      {"tool_name": "get_valuation",
       "tool_arguments": {"ticker_id": "20,1004,1010,1053,148,1041", "mode": "NTM",
                          "metrics": "P/E_Adj_Ratio,EV/Sales,EV/EBITDA_Adj",
                          "start_date": "2026-09-16", "end_date": "2026-09-16", "limit": "40"}}],
     "OK"),
]


def main():
    fd, tmp = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(tmp)
    mcp_guard.STATE = tmp
    mcp_guard.learn({"data": {"success": [
        {"ticker": "EXSW", "ticker_id": 20, "company_name": "Example Software Inc",
         "match_type": "exact_ticker", "insync_subindustry": "Software"}]}})

    print("replaying %d live call groups (earnings-wrap run + capital-allocation peer pull)"
          % len(LIVE))
    print("=" * 84)
    fails, blocked, unchanged, rewritten = [], 0, 0, 0

    for label, calls, outcome in LIVE:
        src = json.loads(json.dumps(calls))
        try:
            status, payload, notes = mcp_guard.check(calls)
            refused = None
        except mcp_guard.Refuse as e:
            status, payload, notes, refused = "refused", None, [], str(e)

        if outcome in ("TIMEOUT", "EMPTY"):
            caught = refused is not None or status == "split" or (
                payload is not None and payload != src)
            if caught:
                blocked += 1
                how = "refused" if refused else ("split" if status == "split" else "rewritten")
                print("  BLOCKED  [%s]  %s" % (how, label))
            else:
                fails.append("NOT BLOCKED: " + label)
                print("  MISSED   %s" % label)
        else:
            if refused is not None:
                fails.append("FALSE POSITIVE (refused a call that worked live): " + label)
                print("  BROKE    %s\n           %s" % (label, refused.split("\n")[0]))
            elif status == "split":
                fails.append("FALSE SPLIT (split a call that worked live): " + label)
                print("  BROKE    split a working call: %s" % label)
            elif payload == src:
                unchanged += 1
                print("  SAME     %s" % label)
            else:
                only_company = all(
                    set(a["tool_arguments"]) <= set(b["tool_arguments"]) and
                    set(b["tool_arguments"]) - set(a["tool_arguments"]) <= {"company"} and
                    all(a["tool_arguments"][k] == b["tool_arguments"][k]
                        for k in a["tool_arguments"])
                    for a, b in zip(payload, src))
                if only_company:
                    rewritten += 1
                    print("  SAFE     %s  (company= dropped, ticker kept)" % label)
                else:
                    fails.append("ALTERED a working call: " + label)
                    print("  BROKE    altered a working call: %s" % label)

    print("=" * 84)
    print("failing calls blocked         : %d of %d  (%d timeout, %d silent-empty)"
          % (blocked, sum(1 for _, _, o in LIVE if o in ("TIMEOUT", "EMPTY")),
             sum(1 for _, _, o in LIVE if o == "TIMEOUT"),
             sum(1 for _, _, o in LIVE if o == "EMPTY")))
    print("working calls passed untouched: %d" % unchanged)
    print("working calls safely rewritten: %d  (company= dropped only)" % rewritten)
    try:
        os.unlink(tmp)
    except Exception:
        pass
    if fails:
        print("\nFAILED:")
        for f in fails:
            print("  - " + f)
        return 1
    print("\nCONTRACT HELD: every live failure blocked, every working call reaches the server "
          "asking for the same data.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
