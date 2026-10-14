#!/usr/bin/env python3
"""
ingest_raw.py - standardized raw-result ingestion for the Revision Analysis skill.

  python ingest_raw.py <source-file> <raw_dir> [outname]

Copies a saved MCP tool-result file (e.g. the harness's tool-results/*.txt) into the
run's raw/ folder VERBATIM (byte-for-byte - no data passes through the model) and
prints one line per result object found at any nesting depth:
  rows / has_more / remaining_items
so pagination gaps are visible immediately, plus a single warning when any result
still has more pages. It never overwrites: re-ingesting an identical file is a no-op,
and a different file under a name already taken is saved as <name>_2.json (warned).

WHY THIS EXISTS: ad-hoc `cp ... && jq ...` shell chains vary on every call, so each one
re-triggers a permission prompt. This script gives every ingest the SAME command
shape - `python <skill>/scripts/ingest_raw.py <src> raw/ <name>` - so one
"Always allow" covers the entire run. Standard library only.
"""
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jscompat import UNDEFINED, _JSErr, _js_read_json, js_keys, js_stdio, js_str

VERSION = '2.5.0'


def _pj(*parts):
    return os.path.normpath(os.path.join(*parts))


def _s(v):
    if v is UNDEFINED:
        return "undefined"
    if isinstance(v, list):
        return ",".join("" if (x is None or x is UNDEFINED) else _s(x) for x in v)
    if isinstance(v, dict):
        return "[object Object]"
    return js_str(v)


def _truthy(v):
    if v is None or v is UNDEFINED or v is False:
        return False
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return not (v == 0 or v != v)
    if isinstance(v, str):
        return v != ""
    return True


_SUR = re.compile("[\ud800-\udfff]")


def _log(s):
    sys.stdout.write(_SUR.sub("�", s) + "\n")


def main():
    js_stdio()
    argv = sys.argv[1:] + [None, None, None]
    src, raw_dir, out_name = argv[0], argv[1], argv[2]
    if not src or not raw_dir:
        sys.stderr.write("usage: python ingest_raw.py <source-file> <raw_dir> [outname]\n")
        sys.stderr.flush()
        sys.exit(1)
    if not os.path.exists(src):
        sys.stderr.write("ingest_raw: source not found: %s\n" % src)
        sys.stderr.flush()
        sys.exit(1)
    os.makedirs(raw_dir, exist_ok=True)
    base = out_name or re.sub(r"\.txt\Z", ".json", os.path.basename(src), count=1, flags=re.I)
    if not re.search(r"\.[A-Za-z0-9]+\Z", base):
        base += ".json"
    dest = _pj(raw_dir, base)
    if os.path.exists(dest):
        with open(src, "rb") as f:
            src_buf = f.read()

        def same(p):
            with open(p, "rb") as f:
                return f.read() == src_buf
        ext = os.path.splitext(base)[1]
        stem = base[:len(base) - len(ext)]
        taken = [dest]
        i = 2
        while os.path.exists(_pj(raw_dir, "%s_%d%s" % (stem, i, ext))):
            taken.append(_pj(raw_dir, "%s_%d%s" % (stem, i, ext)))
            i += 1
        twin = next((t for t in taken if same(t)), None)
        if twin:
            _log("already saved (identical): %s" % twin)
            dest = twin
        else:
            alt = _pj(raw_dir, "%s_%d%s" % (stem, len(taken) + 1, ext))
            _log("WARNING: %s already exists with different content; saved as %s instead (nothing overwritten)." % (dest, alt))
            dest = alt
    if not os.path.exists(dest):
        shutil.copyfile(src, dest)

    try:
        j = _js_read_json(dest)
    except _JSErr as e:
        _log("saved %s (not parseable as JSON: %s)" % (dest, e))
        sys.stdout.flush()
        sys.exit(0)

    lines = []

    def walk(o, key):
        if not _truthy(o) or not isinstance(o, (dict, list)):
            return
        meta = o.get("meta", UNDEFINED) if isinstance(o, dict) else UNDEFINED
        if _truthy(meta) and isinstance(meta, dict) and (
                meta.get("row_count") is not None or meta.get("has_more") is not None):
            g = lambda k: meta.get(k, UNDEFINED)
            lines.append("%s: rows=%s has_more=%s remaining=%s" % (
                key or "result", _s(g("row_count")), _s(g("has_more")), _s(g("remaining_items"))))
        if isinstance(o, list):
            for v in o:
                walk(v, key)
            return
        for k in js_keys(o):
            if k != "data" and k != "meta":
                walk(o[k], k)
    walk(j, None)

    _log("saved %s" % dest)
    for ln in lines:
        _log("  " + ln)
    more = [ln for ln in lines if "has_more=true" in ln]
    if more:
        _log("WARNING: %d result(s) have more pages — refetch with offset += limit and ingest the next page too." % len(more))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
