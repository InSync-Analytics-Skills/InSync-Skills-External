#!/usr/bin/env python3
"""One driver for the whole pipeline, reading the saved MCP responses from disk.

Run one command at a time, the pipeline takes dozens of model steps (plan,
ingest, verify, build, render, chat, lint, ground truth, QC summary), and the
model's steps, not the engine, dominate a run's cost and time.

This driver runs every engine step in order over ONE run folder and prints a
short summary per stage. The model's remaining jobs are the ones only it can
do: make the MCP calls and save each response as returned, write the small
run.json, the commentary and the narrative.

    scratch/<TICKER>_<Q>_<runid>/          one folder per run
        run.json          {"ticker", "company", "ticker_id", "match_type",
                           "subindustry", "latest_quarter", "report_date",
                           "prepared_by"}                    (written by the model)
        discovered.json   search_lineitem(queries="all") response, as returned
        sweep/            one file per keyword bank, as returned (any shape)
        responses/        get_actuals responses, one file each
        annual/           the plan's annual batches
        guidance/         get_guidance pages
        calls.json        get_call_date response
        commentary.json   step 4b (optional; required for a client note)
        narrative.json    the prose (publish only)

    python3 run_pipeline.py RUN_DIR file PATH  # file a saved MCP result into the run
                                               # folder (see stage_file)
    python3 run_pipeline.py RUN_DIR plan       # fetch plan: batches and keyword banks
    python3 run_pipeline.py RUN_DIR prepare    # ingest, verify, build, dives, template
    python3 run_pipeline.py RUN_DIR publish    # build, render PDF, chat, lint once,
                                               # ground truth, QC summary

Every stage stops at the first failure and prints the failing step's own
output; nothing downstream runs on a failed step. The timeout is per command
(600 s), not per stage.
"""

import argparse
import glob
import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TIMEOUT = 600


def _files(run, *patterns):
    out = []
    for pat in patterns:
        out += sorted(glob.glob(os.path.join(run, pat)))
    return [f for f in out if os.path.isfile(f)]


def _step(name, cmd, capture=None):
    r = subprocess.run([PY] + cmd, capture_output=True, text=True, timeout=TIMEOUT)
    if capture:
        with open(capture, "w", encoding="utf-8") as fh:
            fh.write(r.stdout)
    return {"step": name, "rc": r.returncode, "stdout": r.stdout, "stderr": r.stderr}


def _clip(s):
    return s if len(s) <= 3000 else s[:1500] + "\n...\n" + s[-1500:]


def _stop(results, extra=None):
    last = results[-1]
    print(json.dumps({"status": "error", "stage": last["step"], "exit": last["rc"],
                      "message": _clip(last["stdout"].strip() or last["stderr"].strip()),
                      "completed": [r["step"] for r in results[:-1]], **(extra or {})},
                     indent=1))
    sys.exit(1)


def _meta(run):
    p = os.path.join(run, "run.json")
    if not os.path.exists(p):
        raise SystemExit("run.json is missing from %s (ticker, company, ticker_id, "
                         "match_type, subindustry, latest_quarter, report_date)" % run)
    with open(p, encoding="utf-8") as fh:
        m = json.load(fh)
    miss = [k for k in ("ticker", "ticker_id", "latest_quarter", "match_type") if not m.get(k)]
    if miss:
        raise SystemExit("run.json lacks %s" % ", ".join(miss))
    return m


_YEAR = __import__("re").compile(r"^(R\d*-)?(19|20)\d{2}$")


def _kind(key, env):
    k = (key or "").lower()
    data = env.get("data") if isinstance(env, dict) else None
    if k.startswith("search_tickerid") or (
            isinstance(data, dict) and isinstance(data.get("success"), list)
            and any(isinstance(r, dict) and "match_type" in r for r in data["success"])):
        return "resolve"
    if k.startswith("search_lineitem") or (isinstance(data, dict) and data and all(
            isinstance(v, dict) for v in data.values()) and not {"guidance", "outlook"} & set(data)):
        if isinstance(data, dict) and set(data) == {"all"}:
            return "discovered"
        return "sweep"
    if k.startswith("get_guidance") or (isinstance(data, dict) and {"guidance", "outlook"} & set(data)):
        return "guidance"
    if isinstance(data, list):
        if k.startswith("get_call_date") or (data and all("CALL_DATE" in r for r in data if isinstance(r, dict))):
            return "calls"
        if k.startswith("get_actuals") or any(isinstance(r, dict) and "LTCODE" in r for r in data):
            per = [str(r.get("FISCALPERIOD") or "") for r in data if isinstance(r, dict)]
            if per and all(_YEAR.match(x) for x in per):
                return "annual"
            return "responses"
    return None


def _free(path):
    base, ext = os.path.splitext(path)
    n, out = 1, path
    while os.path.exists(out):
        n += 1
        out = "%s_%d%s" % (base, n, ext)
    return out


def _rows_in(data):
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        return sum(_rows_in(v) for v in data.values())
    return 0


def _cut_short(env):
    meta = env.get("meta") if isinstance(env, dict) else None
    want = (meta or {}).get("row_count")
    if not isinstance(want, int) or isinstance(want, bool):
        return None
    got = _rows_in(env.get("data"))
    return (got, want) if got < want else None


def _members(d):
    return (list(d.items()) if isinstance(d, dict) and "status" not in d
            and all(isinstance(v, dict) for v in d.values()) else [("", d)])


def _check_saved(run):
    bad = []
    for f in _files(run, "discovered*.json", "resolve*.json", "sweep/*.json", "sweep*.json", "responses/*.json",
                    "guidance/*.json", "guide*.json", "annual/*.json", "calls*.json"):
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
        except ValueError as e:
            bad.append("%s is not valid JSON (%s)" % (os.path.relpath(f, run), e))
            continue
        for k, env in _members(d):
            c = _cut_short(env)
            if c:
                bad.append("%s%s has %d of the %d row(s) its meta.row_count says"
                           % (os.path.relpath(f, run), (" [%s]" % k) if k else "", c[0], c[1]))
    if bad:
        raise SystemExit("saved response(s) cut short or altered - re-run the call and save the "
                         "harness's result with the file stage: " + "; ".join(bad[:10]))


def _tag_empty_members(run, members):
    sfx = lambda k: int(k.rsplit("_", 1)[1]) if k.rsplit("_", 1)[1].isdigit() else 1
    acts = sorted([(k, e) for k, e in members if (k or "").startswith("get_actuals")
                   and isinstance(e, dict) and isinstance(e.get("data"), list)],
                  key=lambda x: sfx(x[0]))
    if all(e["data"] for _k, e in acts):
        return
    try:
        with open(os.path.join(run, "plan.json"), encoding="utf-8") as fh:
            reqs = json.load(fh).get("requests") or []
    except (OSError, ValueError):
        return
    for r in reqs:
        calls = [c for c in r.get("tool_calls") or [] if c.get("tool_name") == "get_actuals"]
        if len(calls) != len(acts):
            continue
        asked = [set(str(c["tool_arguments"].get("ltcode") or "").split(",")) for c in calls]
        rows = [{str(x.get("LTCODE")) for x in e["data"] if isinstance(x, dict)} for _k, e in acts]
        if any(rw for rw in rows) and all(rw <= a for rw, a in zip(rows, asked)):
            for (_k, e), c in zip(acts, calls):
                e["_requested_ltcode"] = c["tool_arguments"].get("ltcode")
            return


def stage_file(run, paths):
    placed, skipped = [], []
    loaded = []
    for src in paths:
        try:
            with open(src, encoding="utf-8") as fh:
                d = json.load(fh)
        except ValueError as e:
            raise SystemExit("%s is not valid JSON (%s): the saved result was cut short or "
                             "altered. Re-run the call and file the harness's saved result again."
                             % (src, e))
        members = (list(d.items()) if isinstance(d, dict) and "status" not in d
                   and all(isinstance(v, dict) for v in d.values()) else [("", d)])
        short = [(k or "(whole file)", c) for k, env in members for c in [_cut_short(env)] if c]
        if short:
            raise SystemExit("%s: %s - fewer rows than the response's own meta.row_count, so it "
                             "was cut short. Nothing from this file was filed; re-run the call."
                             % (src, "; ".join("%s has %d of %d row(s)" % (k, c[0], c[1])
                                                for k, c in short)))
        loaded.append((src, members))
    for src, members in loaded:
        _tag_empty_members(run, members)
        stem = os.path.splitext(os.path.basename(src))[0][-24:]
        for key, env in members:
            kind = _kind(key, env)
            if kind is None or (isinstance(env, dict) and env.get("status") not in (None, "success")):
                skipped.append({"file": src, "member": key or "(whole file)",
                                "why": "unrecognised member" if kind is None
                                else "status %s" % env.get("status")})
                continue
            name = "%s_%s.json" % (stem, key or kind)
            if kind == "discovered":
                dest = os.path.join(run, "discovered.json")
                if os.path.exists(dest) and _is_transcribed(dest):
                    diff = _discovery_diff(dest, env)
                    with open(os.path.join(run, "discovery_check.json"), "w", encoding="utf-8") as fh:
                        json.dump(diff, fh, indent=1)
                    os.remove(dest)
                elif os.path.exists(dest):
                    skipped.append({"file": src, "member": key or "(whole file)",
                                    "why": "discovered.json is already the verbatim response"})
                    continue
            elif kind == "resolve":
                dest = os.path.join(run, "resolve.json")
            elif kind == "calls":
                dest = os.path.join(run, "calls.json")
            else:
                dest = os.path.join(run, kind, name)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            dest = _free(dest)
            with open(dest, "w", encoding="utf-8") as fh:
                json.dump(env, fh)
            _pr = _probe_state(run)
            if _pr.get("codes") and not _pr.get("answered") and (
                    kind == "annual" or (isinstance(env, dict) and env.get("data") == []
                                         and not env.get("notes"))):
                asked = set(str(env.get("_requested_ltcode") or "").split(",")) - {""}
                done = set(_pr.get("answered_codes") or []) | (asked & set(_pr["codes"]))
                _pr["answered_codes"] = sorted(done)
                _pr["answered"] = not asked or set(_pr["codes"]) <= done
                with open(os.path.join(run, PROBE), "w", encoding="utf-8") as fh:
                    json.dump(_pr, fh)
            meta = env.get("meta") or {}
            placed.append({"member": key or "(whole file)", "as": kind,
                           "to": os.path.relpath(dest, run),
                           "has_more": bool(meta.get("has_more"))})
    more = [p_["to"] for p_ in placed if p_["has_more"]]
    print(json.dumps({"status": "ok", "stage": "file", "placed": placed, "skipped": skipped,
                      "truncated": more,
                      "next": ("re-fetch the truncated calls with smaller batches or the next "
                               "offset (ingest refuses a page with has_more)") if more else
                      "run plan (after discovery and the sweep) or prepare (after the fetch)"},
                     indent=1))




def _is_transcribed(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return bool(json.load(fh).get("transcribed"))
    except (OSError, ValueError, AttributeError):
        return False


def _disc_rows(env):
    out = {}
    data = env.get("data") if isinstance(env, dict) else None
    for v in ((data or {}).get("all") or {}).values() if isinstance(data, dict) else []:
        for r in v or []:
            if isinstance(r, dict) and r.get("ltcode"):
                out[str(r["ltcode"]).strip()] = (str(r.get("name") or "").strip(),
                                                 str(r.get("tags") or "").strip())
    return out


def _discovery_diff(path, verbatim):
    with open(path, encoding="utf-8") as fh:
        typed = _disc_rows(json.load(fh))
    real = _disc_rows(verbatim)
    return {"codes_typed": len(typed), "codes_verbatim": len(real),
            "missing_from_typed": sorted(set(real) - set(typed)),
            "not_in_verbatim": sorted(set(typed) - set(real)),
            "name_or_tag_differs": sorted(c for c in set(real) & set(typed) if real[c] != typed[c]),
            "identical": real == typed}


def stage_discovery_lines(run, paths):
    if len(paths) != 1:
        raise SystemExit("discovery-lines takes one text file of ltcode|name|tags lines")
    m = _meta(run)
    rows = []
    with open(paths[0], encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.rstrip("\n")
            if not line.strip():
                continue
            parts = line.split("|")
            if len(parts) < 2 or not parts[0].strip():
                raise SystemExit("line %d is not ltcode|name|tags: %r" % (n, line[:80]))
            rows.append({"name": parts[1].strip(), "ltcode": parts[0].strip(),
                         "tags": "|".join(x.strip() for x in parts[2:] if x.strip())})
    dest = os.path.join(run, "discovered.json")
    if os.path.exists(dest) and not _is_transcribed(dest):
        raise SystemExit("discovered.json already holds the verbatim response; nothing to write")
    env = {"status": "success", "transcribed": True,
           "data": {"all": {str(m["ticker_id"]): rows}},
           "meta": {"ticker_id": str(m["ticker_id"]), "filter": None},
           "notes": ["written from ltcode|name|tags lines; replaced by the verbatim response "
                     "when it arrives in the first dense request"]}
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(env, fh)
    print(json.dumps({"status": "ok", "stage": "discovery-lines", "rows": len(rows),
                      "to": "discovered.json",
                      "next": "run plan; its first request re-fetches discovery verbatim"},
                     indent=1))


_MISSING_NOTE = __import__("re").compile(r"MISSING DATA[^:]*:\s*LTcode\(s\)\s*([^.]*?)\s+returned",
                                         __import__("re").I)


def _codes_with_rows(run):
    got, empty_members = set(), 0
    for f in _files(run, "responses/*.json"):
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        for _k, env in _members(d):
            data = env.get("data") if isinstance(env, dict) else None
            for note in ((env.get("notes") or []) if isinstance(env, dict) else []):
                for mm in _MISSING_NOTE.finditer(str(note)):
                    got |= {c.strip() for c in __import__("re").split(r"[,\s]+", mm.group(1)) if c.strip()}
            if isinstance(data, list):
                if not data:
                    empty_members += 1
                    got |= set(str(env.get("_requested_ltcode") or "").split(",")) - {""}
                for r in data:
                    if isinstance(r, dict) and (r.get("LTCODE") or r.get("ltcode")):
                        got.add(str(r.get("LTCODE") or r.get("ltcode")).strip())
    return got, empty_members


def _guidance_next_offset(run):
    pages = _files(run, "guidance/*.json", "guide*.json")
    if not pages:
        return 0
    metas = {}
    for f in pages:
        try:
            with open(f, encoding="utf-8") as fh:
                meta = json.load(fh).get("meta") or {}
        except (OSError, ValueError, AttributeError):
            continue
        metas[int(meta.get("offset") or 0)] = meta
    off = 0
    while off in metas:
        if not metas[off].get("has_more"):
            return None
        off += int(metas[off].get("limit") or 100)
    return off


def _schedule(run, m, plan):
    tid = str(m["ticker_id"])
    got, empties = _codes_with_rows(run)
    dense = []
    no_rows = 0
    for b in plan.get("batches") or []:
        codes = b.split(",")
        if set(codes) & got:
            continue
        no_rows += 1
        dense.append({"tool_name": "get_actuals",
                      "tool_arguments": {"ticker_id": tid, "ltcode": b,
                                         "fiscal_period": plan["periods_csv"], "limit": "100"}})
    side = []
    sweeps = []
    if not _files(run, "resolve*.json"):
        side.append({"tool_name": "search_tickerid",
                     "tool_arguments": {"ticker": m["ticker"],
                                        **({"company": m["company"]} if m.get("company") else {})}})
    if os.path.exists(os.path.join(run, "discovered.json")) and \
            _is_transcribed(os.path.join(run, "discovered.json")):
        side.append({"tool_name": "search_lineitem",
                     "tool_arguments": {"ticker_id": tid, "queries": "all"}})
    if not _files(run, "calls*.json"):
        qe = m.get("latest_quarter_end_date")
        if qe:
            import datetime as _dt
            end = (_dt.date.fromisoformat(qe) + _dt.timedelta(days=200)).isoformat()
            side.append({"tool_name": "get_call_date",
                         "tool_arguments": {"ticker_id": tid, "start_date": qe, "end_date": end}})
    off = _guidance_next_offset(run)
    if off is not None:
        side.append({"tool_name": "get_guidance",
                     "tool_arguments": dict({"ticker_id": tid, "latest": True, "limit": "100"},
                                            **({"offset": str(off)} if off else {}))})
    _done_q = set()
    for f in _files(run, "sweep/*.json", "sweep*.json"):
        try:
            with open(f, encoding="utf-8") as fh:
                _d = json.load(fh)
        except (OSError, ValueError):
            continue
        for _k, env in _members(_d):
            if isinstance(env, dict) and isinstance(env.get("data"), dict):
                _done_q |= {str(q).strip().lower() for q in env["data"]}
    for kb in plan.get("keyword_banks") or []:
        _first = str(kb["arguments"].get("queries") or "").split(",")[0].strip().lower()
        if _first and _first not in _done_q:
            sweeps.append({"tool_name": "search_lineitem", "tool_arguments": kb["arguments"]})
    if not _files(run, "annual/*.json"):
        for b in plan.get("annual_batches") or []:
            dense.append({"tool_name": "get_actuals",
                         "tool_arguments": {"ticker_id": tid, "ltcode": b,
                                            "fiscal_period": plan["annual_periods_csv"],
                                            "limit": "100"}})
    return _pack(sweeps + side, dense, plan, tid, defer=bool(sweeps))


SPILL_TARGET = 75000
_ROW_CHARS = 210
_SIDE_CHARS = {"search_tickerid": 1000, "get_call_date": 400,
               "get_guidance": 12000}


def _est_chars(call):
    a = call.get("tool_arguments") or {}
    name = call.get("tool_name")
    if name == "get_actuals":
        rows = len(str(a.get("ltcode") or "").split(",")) * \
            len(str(a.get("fiscal_period") or "").split(","))
        return min(rows, int(a.get("limit") or 100)) * _ROW_CHARS
    if name == "search_lineitem":
        return 16000 if str(a.get("queries")) == "all" else 6000
    return _SIDE_CHARS.get(name, 2000)


def _balance(reqs):
    calls = [c for r in reqs for c in r if not c.get("repeat")]
    if not calls:
        return []
    n = max(1, -(-len(calls) // 5))
    out = [[] for _ in range(n)]
    for c in sorted(calls, key=_est_chars, reverse=True):
        tgt = min((r for r in out if len(r) < 5),
                  key=lambda r: sum(_est_chars(x) for x in r))
        tgt.append(c)
    for r in out:
        r.sort(key=lambda c: c["tool_name"] == "get_actuals")
    return out


def _split_for_spill(reqs, pad):
    out, todo = [], [list(r) for r in reqs]
    while todo:
        r = todo.pop(0)
        real = [c for c in r if not c.get("repeat")]
        est = sum(_est_chars(c) for c in real) + (5 - len(real)) * pad
        if est >= SPILL_TARGET or len(real) < 2:
            out.append(r)
            continue
        real.sort(key=_est_chars, reverse=True)
        todo[:0] = [real[0::2], real[1::2]]
    return out


def _pack(side, dense, plan, tid, defer=False):
    reqs = []
    pool = [{"tool_name": "get_actuals",
             "tool_arguments": {"ticker_id": tid, "ltcode": b,
                                "fiscal_period": plan["periods_csv"], "limit": "100"}}
            for b in (plan.get("batches") or [])[:8]]
    dense = list(dense)
    rep = 0
    for sc in side:
        if defer and not dense and reqs:
            break
        calls = [sc]
        while len(calls) < 5 and dense:
            calls.append(dense.pop(0))
        while len(calls) < 5 and pool:
            calls.append(dict(pool[rep % len(pool)], repeat=True))
            rep += 1
        reqs.append(calls)
    while dense:
        calls, dense = dense[:5], dense[5:]
        while len(calls) < 4 and pool:
            calls.append(dict(pool[rep % len(pool)], repeat=True))
            rep += 1
        reqs.append(calls)
    if not defer:
        reqs = _balance(reqs)
    pool.sort(key=_est_chars, reverse=True)
    if pool:
        reqs = _split_for_spill(reqs, _est_chars(pool[0]))
    for calls in reqs:
        while sum(_est_chars(c) for c in calls) < SPILL_TARGET and len(calls) < 5 and pool:
            calls.append(dict(pool[rep % len(pool)], repeat=True))
            rep += 1
    out = []
    for calls in reqs:
        out.append({"tool_calls": [{"tool_name": c["tool_name"],
                                    "tool_arguments": c["tool_arguments"]} for c in calls],
                    "repeats": sum(1 for c in calls if c.get("repeat"))})
    return out


FILED = ("responses/*.json", "annual/*.json", "guidance/*.json", "sweep/*.json",
         "calls*.json", "resolve*.json", "discovered*.json")


def _respill(reqs, plan, tid, level, run=None):
    rows = {}
    for f in (_files(run, "responses/*.json") if run else []):
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        for _k, env in _members(d):
            for r in (env.get("data") if isinstance(env, dict) and isinstance(env.get("data"), list) else []):
                if isinstance(r, dict) and r.get("LTCODE"):
                    rows.setdefault(r["LTCODE"], set()).add(r.get("FISCALPERIOD"))
    dense = sorted(plan.get("batches") or [],
                   key=lambda b: -sum(len(rows.get(c, ())) for c in b.split(",")))
    pool = [{"tool_name": "get_actuals",
             "tool_arguments": {"ticker_id": tid, "ltcode": b,
                                "fiscal_period": plan["periods_csv"], "limit": "100"}}
            for b in dense[:8]]
    first = reqs[0]["tool_calls"]
    real = first[:len(first) - reqs[0].get("repeats", 0)]
    keep = 2 if level == 1 else 1
    out = []
    for part in (real[:keep], real[keep:]):
        if part:
            pads = [c for c in pool if c not in part][:5 - len(part)]
            out.append({"tool_calls": part + pads, "repeats": len(pads)})
    return out + reqs[1:]


def stage_plan(run, m):
    _check_saved(run)
    try:
        with open(os.path.join(run, "plan.json"), encoding="utf-8") as fh:
            prev = json.load(fh)
    except (OSError, ValueError):
        prev = {}
    sig = sorted("%s:%d" % (os.path.relpath(f, run), os.path.getsize(f)) for f in _files(run, *FILED))
    cmd = [os.path.join(HERE, "compute.py"), "plan", "--ticker", m["ticker"],
           "--latest-quarter", m["latest_quarter"], "--ticker-id", str(m["ticker_id"]),
           "--subindustry", m.get("subindustry") or "", "--json",
           "--discovered"] + _files(run, "discovered*.json")
    sw = _files(run, "sweep/*.json", "sweep*.json")
    if sw:
        cmd += ["--sweep"] + sw
    r = _step("plan", cmd, capture=os.path.join(run, "plan.json"))
    if r["rc"]:
        _stop([r])
    p = json.loads(r["stdout"])
    reqs = _schedule(run, m, p)
    level = 0 if not reqs else prev.get("inline_level", 0) + 1 if (
        prev.get("requests") and prev.get("filed_sig") == sig) else min(prev.get("inline_level", 0), 1)
    if level:
        reqs = _respill(reqs, p, str(m["ticker_id"]), level, run)
    p.update(requests=reqs, filed_sig=sig, inline_level=level)
    with open(os.path.join(run, "plan.json"), "w", encoding="utf-8") as fh:
        json.dump(p, fh, indent=1)
    print(json.dumps({"status": "ok", "stage": "plan", "plan": os.path.join(run, "plan.json"),
                      "codes_to_fetch": p["codes_to_fetch"], "batches": len(p["batches"]),
                      "periods_csv": p["periods_csv"],
                      "annual_periods_csv": p["annual_periods_csv"],
                      "annual_batches": len(p["annual_batches"]),
                      "keyword_banks_to_run": [b["bank"] for b in p["keyword_banks"]]
                      if not sw else [],
                      "requests_remaining": len(reqs),
                      "next_request": reqs[0]["tool_calls"] if reqs else None,
                      "note": ("send each request in plan.json 'requests' as one tool_calls "
                               "list, file the saved result, and re-run plan: it reads the run "
                               "folder and prints only what is still missing")
                      if reqs else "every planned call has been filed; run prepare"},
                     indent=1))


def _ingest_cmd(run, m):
    cmd = [os.path.join(HERE, "compute.py"), "ingest", os.path.join(run, "responses"),
           "--ticker", m["ticker"], "--latest-quarter", m["latest_quarter"],
           "--company", m.get("company") or m["ticker"], "--ticker-id", str(m["ticker_id"]),
           "--match-type", m["match_type"],
           "--sector", m.get("subindustry") or m.get("sector") or "",
           "--out", os.path.join(run, "raw.json"),
           "--discovered"] + _files(run, "discovered*.json")
    if m.get("report_date"):
        cmd += ["--report-date", m["report_date"]]
    sw = _files(run, "sweep/*.json", "sweep*.json")
    if sw:
        cmd += ["--sweep"] + sw
    g = _files(run, "guidance/*.json", "guide*.json")
    if g:
        cmd += ["--guidance"] + g
    a = _files(run, "annual/*.json")
    if a:
        cmd += ["--annual"] + a
    c = _files(run, "calls*.json")
    if c:
        cmd += ["--call-dates"] + c
    rv = _files(run, "resolve*.json")
    if rv:
        cmd += ["--resolve"] + rv
    _pr = _probe_state(run)
    if _pr.get("codes") and (_pr.get("answered") or _pr.get("answered_codes")):
        cmd += ["--annual-probed", ",".join(_pr["codes"] if _pr.get("answered")
                                            else _pr["answered_codes"])]
    return cmd


PROBE = "annual_probe.json"


def _probe_state(run):
    try:
        with open(os.path.join(run, PROBE), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _build_cmd(run, m, out):
    cmd = [os.path.join(HERE, "compute.py"), "build", os.path.join(run, "raw.json"),
           "--out-dir", out]
    if os.path.exists(os.path.join(run, "commentary.json")):
        cmd += ["--commentary", os.path.join(run, "commentary.json")]
    if m.get("prepared_by"):
        cmd += ["--prepared-by", m["prepared_by"]]
    return cmd


def _raw_sha(run):
    with open(os.path.join(run, "raw.json"), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


VERIFIED = "verified.json"


def stage_prepare(run, m):
    _check_saved(run)
    if os.path.exists(os.path.join(run, VERIFIED)):
        os.remove(os.path.join(run, VERIFIED))
    if not _files(run, "resolve*.json"):
        raise SystemExit("resolve.json is missing: the search_tickerid result rides in the first "
                         "request plan prints; file it so the company, ticker and match type "
                         "are read from the provider rather than typed")
    if _is_transcribed(os.path.join(run, "discovered.json")):
        raise SystemExit("discovered.json is still the transcribed copy: the verbatim "
                         "search_lineitem(queries='all') rides in the first request plan prints; "
                         "file it, then re-run plan and prepare")
    out = os.path.join(run, "out")
    res = [_step("ingest", _ingest_cmd(run, m))]
    if res[-1]["rc"]:
        _stop(res)
    vf = [os.path.join(HERE, "compute.py"), "verify", os.path.join(run, "raw.json")]
    if m.get("allow_no_calendar") is True:
        vf.append("--allow-no-calendar")
    res.append(_step("verify", vf, capture=os.path.join(run, "verify.json")))
    if res[-1]["rc"]:
        _stop(res)
    with open(os.path.join(run, VERIFIED), "w", encoding="utf-8") as fh:
        json.dump({"raw_sha256": _raw_sha(run)}, fh)
    res.append(_step("build", _build_cmd(run, m, out)))
    if res[-1]["rc"]:
        _stop(res)
    res.append(_step("dives", [os.path.join(HERE, "compute.py"), "dives",
                               os.path.join(run, "raw.json")],
                     capture=os.path.join(run, "dives.json")))
    if res[-1]["rc"]:
        _stop(res)
    comp = glob.glob(os.path.join(out, "*_inflection_computed.json"))[0]
    tmpl = os.path.join(run, "narrative_template.json")
    res.append(_step("narrative_template", [os.path.join(HERE, "render.py"), comp,
                                            "--narrative-template"], capture=tmpl))
    if res[-1]["rc"]:
        _stop(res)
    with open(comp, encoding="utf-8") as fh:
        p = json.load(fh)
    ing = json.loads(res[0]["stdout"])
    probe = (ing.get("annual_probe_needed") or {}).get("calls") or []
    _old = _probe_state(run)
    if probe and not _old.get("answered"):
        with open(os.path.join(run, PROBE), "w", encoding="utf-8") as fh:
            json.dump({"codes": sorted(set(_old.get("codes") or []) | {
                c for call in probe for c in
                str(call["tool_arguments"].get("ltcode") or "").split(",") if c}),
                       "answered_codes": _old.get("answered_codes") or [],
                       "answered": False}, fh)
    if probe:
        try:
            with open(os.path.join(run, "plan.json"), encoding="utf-8") as fh:
                _plan = json.load(fh)
            reqs = [_respill([{"tool_calls": [c], "repeats": 0}], _plan,
                             str(m["ticker_id"]), 2, run)[0] for c in probe]
            ing["annual_probe_needed"]["requests"] = reqs
            _plan["requests"] = reqs
            with open(os.path.join(run, "plan.json"), "w", encoding="utf-8") as fh:
                json.dump(_plan, fh, indent=1)
        except (OSError, ValueError, KeyError):
            pass
    print(json.dumps({"status": "ok", "stage": "prepare",
                      "ingest": ing,
                      "verify_ok": True,
                      "warnings": [w.get("message") for w in p.get("warnings") or []],
                      "deep_dives": json.loads(res[3]["stdout"])["deep_dives"],
                      "next": ("write commentary.json for the deep dives (step 4b) and "
                               "narrative.json from %s, then run publish" % tmpl)}, indent=1))


def stage_publish(run, m):
    out = os.path.join(run, "out")
    narr = os.path.join(run, "narrative.json")
    if not os.path.exists(narr):
        raise SystemExit("narrative.json is missing from %s" % run)
    try:
        with open(os.path.join(run, VERIFIED), encoding="utf-8") as fh:
            _v = json.load(fh)
    except (OSError, ValueError):
        raise SystemExit("verify has not passed for this run: run prepare and resolve what "
                         "verify refused before publishing")
    if _v.get("raw_sha256") != _raw_sha(run):
        raise SystemExit("raw.json changed after verify passed: run prepare again")
    cm = os.path.join(run, "commentary.json")
    cmx = ["--commentary", cm] if os.path.exists(cm) else []
    res = [_step("build", _build_cmd(run, m, out))]
    if res[-1]["rc"]:
        _stop(res)
    comp = glob.glob(os.path.join(out, "*_inflection_computed.json"))[0]
    rcmd = [os.path.join(HERE, "render.py"), comp, "--narrative", narr, "--out-dir", out] + cmx
    if m.get("prepared_by"):
        rcmd += ["--prepared-by", m["prepared_by"]]
    res.append(_step("render", rcmd, capture=os.path.join(out, "render.json")))
    if res[-1]["rc"]:
        _stop(res)
    html = glob.glob(os.path.join(out, "*_Inflection_Monitor_*.html"))[0]
    pdf = html[:-5] + ".pdf"
    if not os.path.exists(pdf):
        _stop(res, {"fix": "no PDF was produced: install a PDF engine (compute.py check) and "
                           "re-run publish; the HTML is never delivered"})
    chat = os.path.join(out, "chat.txt")
    res.append(_step("chat", [os.path.join(HERE, "compute.py"), "chat", comp], capture=chat))
    if res[-1]["rc"]:
        _stop(res)
    lint_json = os.path.join(out, "lint.json")
    lcmd = [os.path.join(HERE, "format_lint.py"), "--computed", comp, "--html", html,
            "--narrative", narr, "--chat", chat, "--json-out", lint_json] + cmx
    if os.path.exists(pdf):
        lcmd += ["--pdf", pdf]
    res.append(_step("lint", lcmd))
    if res[-1]["rc"]:
        _stop(res, {"fix": "read the named gates, fix the narrative or the inputs, re-run publish"})
    gt = os.path.join(out, "gt.json")
    res.append(_step("ground_truth", [os.path.join(HERE, "ground_truth.py"), "--raw",
                                      os.path.join(run, "raw.json"), "--computed", comp,
                                      "--json"], capture=gt))
    if res[-1]["rc"]:
        _stop(res)
    qcmd = [os.path.join(HERE, "qc_summary.py"), "--computed", comp, "--lint", lint_json,
            "--ground-truth", gt, "--render-json", os.path.join(out, "render.json"),
            "--out-dir", os.path.join(out, "qc")]
    res.append(_step("qc_summary", qcmd))
    if res[-1]["rc"]:
        _stop(res)
    qc = json.loads(res[-1]["stdout"])
    print(json.dumps({"status": "ok", "stage": "publish",
                      "deliverable": pdf if os.path.exists(pdf) else None,
                      "qc_status": qc.get("status"),
                      "chat": open(chat, encoding="utf-8").read()}, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("stage", choices=["file", "discovery-lines", "plan", "prepare", "publish"])
    ap.add_argument("paths", nargs="*", help="file: saved MCP result file(s) to file")
    a = ap.parse_args()
    run = os.path.abspath(a.run_dir)
    if a.stage == "file":
        if not a.paths:
            raise SystemExit("file needs at least one saved MCP result path")
        os.makedirs(run, exist_ok=True)
        return stage_file(run, a.paths)
    if a.stage == "discovery-lines":
        return stage_discovery_lines(run, a.paths)
    m = _meta(run)
    {"plan": stage_plan, "prepare": stage_prepare, "publish": stage_publish}[a.stage](run, m)


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    from _contract import run as _run_contract
    _run_contract(main, "run_pipeline")
