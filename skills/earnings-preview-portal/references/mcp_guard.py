#!/usr/bin/env python3
"""
mcp_guard.py - build and validate InSync MCP call arguments BEFORE the call goes out.

WHY THIS IS CODE AND NOT PROSE
------------------------------
The skill documents state every rule below. They are still followed unevenly, because they
are prose and a model has to remember them at the moment it writes the `tool_calls` block. So
the model fetches, and the script decides.

A timeout is the one defect class in these skills that CANNOT be gated after the fact. Every
other check - unit tokens, polarity, consensus probes, page fit - inspects data that arrived.
A timeout leaves nothing behind to inspect. It has to be stopped at call construction or not
at all. That is what this file is.

NO NETWORK. It never issues a call. It rewrites or refuses an argument dict, and the caller
issues the corrected call.

MEASURED TRAPS IT BLOCKS

  1. search_tickerid(ticker=X, company=Y)          180s. Most such calls hang: ticker alone
                                                   returns in seconds, adding company= times out,
                                                   and a name not spelled exactly as InSync
                                                   spells it hangs.
  2. country="US"                                  180s, and minutes lost per retry.
  3. region=<anything> on explore                  region="Asia" timed out 3 of 3, alone in the call.
                                                   region="US" also means the Americas and returns
                                                   BRL issuers into a US screen.
  4. two semantic searches in one execute          The 180s ceiling is per CALL, not per tool. Two
                                                   ~100s searches pass alone and die together.
                                                   Measured: 3.3 min lost to one such batch.
  5. insync_subindustry not from a payload         A guessed label ("Application Software") hangs
                                                   to 180s where the real label ("Software")
                                                   returns in seconds. The industry-standard-style
                                                   guess is the natural one to type, and it hangs.
  6. >5 tool_calls in one execute                  hard cap, rejected server side.
  7. parse_document_content on a file over 5 MB    times out rather than erroring, or elides
                                                   in-band with NO error at all.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It does not change which issuer, period, ltcode or metric you ask for. Every rewrite is to the
FORM of the request, never its SUBJECT, so the data that comes back for a given ticker and
period is identical to a correctly hand-written call. The one exception is `region`, which it
refuses outright: a region screen returns a different and wrong peer set, which is the point.

USAGE
-----
    python mcp_guard.py learn  <payload.json>            # record canonical facts from a reply
    python mcp_guard.py check  '<tool_calls JSON>'       # validate/rewrite; corrected JSON to stdout
    python mcp_guard.py check  --file calls.json
    python mcp_guard.py docpick <document_tree.json>     # size-filtered document ladder
    python mcp_guard.py selftest                         # offline, no network

Exit codes for `check`:
    0  safe. Corrected call JSON on stdout. Any rewrite is reported on stderr.
    2  MUST SPLIT. stdout carries a JSON list of call groups to send one after another.
    3  REFUSED. Nothing safe to send. Read stderr and fix the input.
"""
import json
import os
import re
import sys

STATE = os.environ.get("MCP_GUARD_STATE") or os.path.join(os.getcwd(), ".mcp_guard_state.json")

SEMANTIC = "semantic"

COUNTRY_CANON = {
    "us": "United States of America",
    "usa": "United States of America",
    "u.s.": "United States of America",
    "u.s.a.": "United States of America",
    "united states": "United States of America",
    "united states of america": "United States of America",
}

MAX_CALLS_PER_EXECUTE = 5
DOC_MAX_BYTES = 5 * 1024 * 1024


class Refuse(Exception):
    pass




def _load_state():
    try:
        with open(STATE, encoding="utf-8") as fh:
            s = json.load(fh)
    except Exception:
        s = {}
    s.setdefault("subindustries", [])
    s.setdefault("countries", [])
    s.setdefault("tickers", {})
    return s


def _save_state(s):
    try:
        with open(STATE, "w", encoding="utf-8") as fh:
            json.dump(s, fh, indent=1, sort_keys=True)
    except Exception:
        pass


def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            for d in _walk(v):
                yield d
    elif isinstance(obj, list):
        for v in obj:
            for d in _walk(v):
                yield d


def learn(payload):
    s = _load_state()
    subs, ctys, n = set(s["subindustries"]), set(s["countries"]), 0
    for d in _walk(payload):
        si = d.get("insync_subindustry")
        if isinstance(si, str) and si.strip():
            subs.add(si.strip())
            n += 1
        ct = d.get("country")
        if isinstance(ct, str) and ct.strip():
            ctys.add(ct.strip())
        tk, tid = d.get("ticker"), d.get("ticker_id")
        if isinstance(tk, str) and tid is not None:
            s["tickers"][tk.strip().upper()] = {
                "ticker_id": tid,
                "company_name": d.get("company_name"),
                "match_type": d.get("match_type"),
                "latest_fiscal_quarter": d.get("latest_fiscal_quarter"),
                "primary_currency": d.get("primary_currency"),
            }
    s["subindustries"] = sorted(subs)
    s["countries"] = sorted(ctys)
    _save_state(s)
    return {"learned_subindustry_rows": n,
            "known_subindustries": s["subindustries"],
            "known_tickers": sorted(s["tickers"])}




def _classify(name, args):
    if name == "search_lineitem":
        return SEMANTIC
    if name == "search_tickerid" and str(args.get("mode", "company")).lower() == "explore":
        return SEMANTIC
    return "keyed"


def _guard_search_tickerid(args, notes, state):
    a = dict(args)
    mode = str(a.get("mode", "company")).lower()

    if mode != "explore" and a.get("company") and a.get("ticker"):
        notes.append("search_tickerid: dropped company=%r and kept ticker=%r. A company string "
                     "that misses a high-confidence match sends the resolver into a pass that "
                     "never returns (11 of 18 measured calls hung). Resolution is unchanged: the "
                     "ticker row is the one the skills accept, and only match_type='exact_ticker' "
                     "is accepted downstream." % (a["company"], a["ticker"]))
        a.pop("company")

    if mode != "explore" and a.get("company") and not a.get("ticker"):
        raise Refuse("search_tickerid: company=%r with no ticker. This is the call shape that "
                     "hangs to the 180s ceiling on any name that is not InSync's exact canonical "
                     "spelling (a near-miss spelling hangs where the exact registered name returns in seconds). Ask the user "
                     "for the symbol and pass ticker alone." % a["company"])

    if mode == "explore":
        if a.get("region"):
            raise Refuse("search_tickerid(mode='explore'): region=%r. Measured: region='Asia' "
                         "timed out 3 of 3 even alone in the call, and region='US' means the "
                         "AMERICAS, which returns foreign-currency issuers into a domestic "
                         "screen. Pass country= with the full country name instead."
                         % a["region"])

        ct = a.get("country")
        if isinstance(ct, str) and ct.strip():
            key = ct.strip().lower()
            canon = COUNTRY_CANON.get(key)
            if canon and canon != ct:
                notes.append("search_tickerid: country %r -> %r. The short form is the 180s "
                             "timeout; the full name is the canonical value." % (ct, canon))
                a["country"] = canon
            elif not canon and len(ct.strip()) <= 3:
                raise Refuse("search_tickerid(mode='explore'): country=%r looks like a short "
                             "code. Short codes hang to the 180s ceiling (country='US' cost a "
                             "live run 6 minutes over two attempts). Pass the country's full "
                             "name." % ct)

        si = a.get("insync_subindustry")
        if isinstance(si, str) and si.strip():
            known = state["subindustries"]
            if si.strip() not in known:
                raise Refuse(
                    "search_tickerid(mode='explore'): insync_subindustry=%r was never returned "
                    "by a payload in this run. An unmatched label hangs to the 180s ceiling - "
                    "reproduced 2026-09-17: 'Application Software' timed out, 'Software' "
                    "returned in 7s, and 'Software' is the feed's real label. Run "
                    "`mcp_guard.py learn <search_tickerid payload>` first and pass the string it "
                    "recorded, verbatim.%s"
                    % (si, ("\n  Known this run: " + ", ".join(repr(k) for k in known))
                       if known else "\n  Nothing learned yet this run."))
    return a


def _guard_parse_document(args, notes):
    a = dict(args)
    if a.pop("ticker_id", None) is not None:
        notes.append("parse_document_content: removed ticker_id, which this tool does not take "
                     "(the file path names the document).")
    sizes = a.pop("_file_sizes", None)
    if isinstance(sizes, dict):
        too_big = {p: n for p, n in sizes.items() if isinstance(n, (int, float)) and n > DOC_MAX_BYTES}
        keep = [p for p in a.get("file_paths", []) if p not in too_big]
        if too_big:
            if not keep:
                raise Refuse(
                    "parse_document_content: every candidate is over the 5 MB ceiling (%s). An "
                    "oversized parse times out rather than erroring, or elides in-band with no "
                    "error, so the note gets built from partial text and nothing says so. Pick "
                    "the next document down the ladder: `mcp_guard.py docpick <tree.json>`."
                    % ", ".join("%s=%.1fMB" % (p, n / 1048576.0) for p, n in too_big.items()))
            notes.append("parse_document_content: dropped %d file(s) over the 5 MB ceiling (%s). "
                         "Oversized parses time out or elide silently."
                         % (len(too_big),
                            ", ".join("%.1fMB" % (n / 1048576.0) for n in too_big.values())))
            a["file_paths"] = keep
    return a




def check(tool_calls):
    state = _load_state()
    notes = []
    calls = tool_calls if isinstance(tool_calls, list) else [tool_calls]

    out = []
    for c in calls:
        if not isinstance(c, dict) or "tool_name" not in c:
            raise Refuse("each entry needs a tool_name. Got: %r" % (c,))
        name = c["tool_name"]
        args = dict(c.get("tool_arguments") or {})

        if name == "search_tickerid":
            args = _guard_search_tickerid(args, notes, state)
        elif name == "parse_document_content":
            args = _guard_parse_document(args, notes)

        out.append({"tool_name": name, "tool_arguments": args})

    if len(out) > MAX_CALLS_PER_EXECUTE:
        raise Refuse("%d calls in one execute. The hard cap is %d."
                     % (len(out), MAX_CALLS_PER_EXECUTE))

    sem = [i for i, c in enumerate(out) if _classify(c["tool_name"], c["tool_arguments"]) == SEMANTIC]
    if len(sem) > 1:
        groups, rest = [], [c for i, c in enumerate(out) if i not in sem]
        for i in sem:
            groups.append([out[i]])
        if rest:
            groups.append(rest)
        notes.append("SPLIT: %d semantic searches (%s) were in one execute. The 180s ceiling is "
                     "per CALL, not per tool, so two ~100s searches pass alone and time out "
                     "together, and one timeout loses every result in the batch. Measured cost "
                     "of this exact mistake: 3.3 minutes. Sending %d calls in sequence."
                     % (len(sem), ", ".join(out[i]["tool_name"] for i in sem), len(groups)))
        return "split", groups, notes

    return "ok", out, notes




def docpick(tree, max_bytes=DOC_MAX_BYTES):
    docs = []
    for d in _walk(tree):
        path = d.get("file_path") or d.get("path") or d.get("file")
        if not isinstance(path, str) or not path:
            continue
        size = None
        for k in ("size_bytes", "size", "bytes", "file_size"):
            v = d.get(k)
            if isinstance(v, (int, float)):
                size = float(v)
                break
            if isinstance(v, str):
                m = re.match(r"^\s*([\d.]+)\s*(B|KB|MB|GB)?\s*$", v, re.I)
                if m:
                    mult = {"": 1, "b": 1, "kb": 1024, "mb": 1048576, "gb": 1073741824}
                    size = float(m.group(1)) * mult[(m.group(2) or "").lower()]
                    break
        docs.append({"file_path": path,
                     "name": d.get("name") or d.get("document_type") or d.get("title"),
                     "size_bytes": size})

    seen, uniq = set(), []
    for d in docs:
        if d["file_path"] not in seen:
            seen.add(d["file_path"])
            uniq.append(d)

    ok = [d for d in uniq if d["size_bytes"] is None or d["size_bytes"] <= max_bytes]
    skip = [d for d in uniq if d["size_bytes"] is not None and d["size_bytes"] > max_bytes]
    unknown = [d for d in ok if d["size_bytes"] is None]
    return {"parse_in_order": ok, "skipped_oversize": skip, "size_unknown": unknown,
            "ceiling_mb": max_bytes / 1048576.0}




def selftest():
    global STATE
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(tmp)
    STATE = tmp
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

    print("mcp_guard selftest")
    print("-" * 78)

    st, payload, notes = check([{"tool_name": "search_tickerid",
                                 "tool_arguments": {"ticker": "EXM", "company": "Example Corp"}}])
    ok("company= is stripped, ticker kept",
       st == "ok" and payload[0]["tool_arguments"] == {"ticker": "EXM"})
    ok("the strip is reported, not silent", any("dropped company" in n for n in notes))
    ok("bare company with no ticker is refused",
       refused([{"tool_name": "search_tickerid", "tool_arguments": {"company": "Example Co"}}]))

    learn({"data": {"success": [{"ticker": "EXSW", "ticker_id": 20, "company_name": "Example Software Inc",
                                 "insync_subindustry": "Software", "match_type": "exact_ticker",
                                 "country": "United States of America"}]}})
    st, payload, notes = check([{"tool_name": "search_tickerid",
                                 "tool_arguments": {"mode": "explore", "country": "US",
                                                    "insync_subindustry": "Software"}}])
    ok("country='US' rewritten to the full name",
       payload[0]["tool_arguments"]["country"] == "United States of America")
    st, payload, _ = check([{"tool_name": "search_tickerid",
                             "tool_arguments": {"mode": "explore", "country": "United States",
                                                "insync_subindustry": "Software"}}])
    ok("country='United States' also canonicalised",
       payload[0]["tool_arguments"]["country"] == "United States of America")
    ok("an unknown short country code is refused",
       refused([{"tool_name": "search_tickerid",
                 "tool_arguments": {"mode": "explore", "country": "JP",
                                    "insync_subindustry": "Software"}}]))

    ok("region= on explore is refused",
       refused([{"tool_name": "search_tickerid",
                 "tool_arguments": {"mode": "explore", "region": "Asia",
                                    "insync_subindustry": "Software"}}]))
    ok("region='US' refused too (it means the Americas)",
       refused([{"tool_name": "search_tickerid",
                 "tool_arguments": {"mode": "explore", "region": "US"}}]))

    msg = refused([{"tool_name": "search_tickerid",
                    "tool_arguments": {"mode": "explore",
                                       "country": "United States of America",
                                       "insync_subindustry": "Application Software"}}])
    ok("a typed subindustry label is refused", bool(msg))
    ok("the refusal names the learned alternative", bool(msg) and "'Software'" in msg)
    st, payload, _ = check([{"tool_name": "search_tickerid",
                             "tool_arguments": {"mode": "explore",
                                                "country": "United States of America",
                                                "insync_subindustry": "Software"}}])
    ok("the learned label passes", st == "ok")

    st, groups, notes = check([
        {"tool_name": "search_lineitem", "tool_arguments": {"ticker_id": "20", "queries": "all"}},
        {"tool_name": "search_tickerid", "tool_arguments": {"mode": "explore",
                                                            "country": "United States of America",
                                                            "insync_subindustry": "Software"}}])
    ok("two semantic searches are split", st == "split" and len(groups) == 2)
    ok("each group holds exactly one semantic search",
       st == "split" and all(len(g) == 1 for g in groups))
    st, payload, _ = check([
        {"tool_name": "get_actuals", "tool_arguments": {"ticker_id": "20", "ltcode": "IS_P_101"}},
        {"tool_name": "get_guidance", "tool_arguments": {"ticker_id": "20"}},
        {"tool_name": "get_market_data", "tool_arguments": {"ticker_id": "20"}}])
    ok("keyed getters still batch freely", st == "ok" and len(payload) == 3)
    st, groups, _ = check([
        {"tool_name": "search_lineitem", "tool_arguments": {"ticker_id": "20", "queries": "all"}},
        {"tool_name": "search_tickerid", "tool_arguments": {"mode": "explore",
                                                            "country": "United States of America",
                                                            "insync_subindustry": "Software"}},
        {"tool_name": "get_actuals", "tool_arguments": {"ticker_id": "20", "ltcode": "IS_P_101"}}])
    ok("keyed calls ride along in the last group after a split",
       st == "split" and len(groups) == 3 and groups[-1][0]["tool_name"] == "get_actuals")

    ok("more than 5 calls refused",
       refused([{"tool_name": "get_actuals", "tool_arguments": {}} for _ in range(6)]))

    ok("an oversized parse target is dropped",
       check([{"tool_name": "parse_document_content",
               "tool_arguments": {"file_paths": ["big.pdf", "small.pdf"],
                                  "_file_sizes": {"big.pdf": 9 * 1048576,
                                                  "small.pdf": 200000}}}])[1][0]
       ["tool_arguments"]["file_paths"] == ["small.pdf"])
    ok("all-oversized is refused rather than silently emptied",
       refused([{"tool_name": "parse_document_content",
                 "tool_arguments": {"file_paths": ["big.pdf"],
                                    "_file_sizes": {"big.pdf": 9 * 1048576}}}]))

    picked = docpick({"documents": [
        {"name": "Earnings Transcript", "file_path": "t.pdf", "size_bytes": 7 * 1048576},
        {"name": "Earnings Release", "file_path": "r.pdf", "size_bytes": 300000},
        {"name": "Non-GAAP Reconciliation", "file_path": "n.pdf", "size": "1.2 MB"}]})
    ok("docpick drops the oversize file only",
       [d["file_path"] for d in picked["parse_in_order"]] == ["r.pdf", "n.pdf"]
       and [d["file_path"] for d in picked["skipped_oversize"]] == ["t.pdf"])
    ok("docpick parses a '1.2 MB' string size",
       abs(picked["parse_in_order"][1]["size_bytes"] - 1.2 * 1048576) < 1)

    src = {"tool_name": "get_actuals",
           "tool_arguments": {"ticker_id": "20", "ltcode": "IS_P_101,IS_P_128",
                              "fiscal_period": "3Q26,2Q26", "limit": "50"}}
    ok("a keyed getter passes through byte-identical",
       check([json.loads(json.dumps(src))])[1][0] == src)

    print("-" * 78)
    try:
        os.unlink(tmp)
    except Exception:
        pass
    if fails:
        print("FAILED %d: %s" % (len(fails), "; ".join(fails)))
        return 1
    print("all checks passed")
    return 0




def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1]

    if cmd == "selftest":
        return selftest()

    if cmd == "learn":
        if len(argv) < 3:
            sys.stderr.write("learn needs a payload file\n")
            return 2
        with open(argv[2], encoding="utf-8") as fh:
            print(json.dumps(learn(json.load(fh)), indent=1))
        return 0

    if cmd == "docpick":
        if len(argv) < 3:
            sys.stderr.write("docpick needs a document-tree file\n")
            return 2
        with open(argv[2], encoding="utf-8") as fh:
            print(json.dumps(docpick(json.load(fh)), indent=1))
        return 0

    if cmd == "check":
        if len(argv) >= 4 and argv[2] == "--file":
            with open(argv[3], encoding="utf-8") as fh:
                calls = json.load(fh)
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

    sys.stderr.write("unknown command %r\n" % cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
