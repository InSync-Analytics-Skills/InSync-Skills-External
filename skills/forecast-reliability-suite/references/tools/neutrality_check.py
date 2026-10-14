#!/usr/bin/env python3
"""neutrality_check.py - assert an edit moved NO computed figure.

Renders every shipped fixture through a reference build and the working build and compares the
screener payload field by field. This is the control that has to exist BEFORE an edit, not after:
defects such as a strict-polarity block or a `pct` typing shift are invisible to review and
obvious on execution.

    python3 neutrality_check.py <reference_pkg> <working_pkg>
    python3 neutrality_check.py <reference_pkg> <working_pkg> --ignore reconciliation.checks_run

Keys that are EXPECTED to differ are named explicitly with --ignore, one per flag, so that a
deliberate change is a visible argument in the command line rather than a silent tolerance in the
code. `version` is always ignored: it changes every release and is not part of the numeric contract.

Exit 0 = every computed figure identical. Exit 1 = drift (the edit is not neutral; read it, do not
refresh past it). Exit 2 = could not run.

NEVER pipe this into head/tail and never chain it with &&: a pipe reports the LAST command's status
and turns a red build green.
"""
import glob
import json
import os
import subprocess
import sys
import tempfile

ALWAYS_IGNORE = {"version"}


def _render(pkg, fixture, outdir):
    rnd = os.path.join(pkg, "references", "render_consensus_portal.py")
    if not os.path.exists(rnd):
        print(f"  ERROR: no renderer at {rnd}")
        return None
    os.makedirs(outdir, exist_ok=True)
    r = subprocess.run([sys.executable, rnd, fixture, "--nopdf", "--out", outdir],
                       capture_output=True, text=True)
    m = os.path.join(outdir, "forecast_reliability_suite_metrics.json")
    if not os.path.exists(m):
        print(f"  ERROR: {os.path.basename(fixture)} produced no metrics json "
              f"(exit {r.returncode}): {(r.stdout or r.stderr)[:160]}")
        return None
    return json.load(open(m, encoding="utf-8"))


def _flat(o, p=""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from _flat(v, f"{p}.{k}")
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from _flat(v, f"{p}[{i}]")
    else:
        yield p.lstrip("."), o


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    ref_pkg, work_pkg = os.path.abspath(argv[0]), os.path.abspath(argv[1])
    ignore = set(ALWAYS_IGNORE)
    for i, a in enumerate(argv):
        if a == "--ignore" and i + 1 < len(argv):
            ignore.add(argv[i + 1])

    fixtures = sorted(glob.glob(os.path.join(work_pkg, "references", "tests", "*_fixture.json")))
    if not fixtures:
        print("ERROR: no fixtures found; nothing to compare")
        return 2

    tmp = tempfile.mkdtemp()
    drift, checked = [], 0
    for fx in fixtures:
        name = os.path.basename(fx).replace("_fixture.json", "")
        a = _render(ref_pkg, os.path.join(ref_pkg, "references", "tests", os.path.basename(fx)),
                    os.path.join(tmp, "ref", name))
        b = _render(work_pkg, fx, os.path.join(tmp, "work", name))
        if a is None or b is None:
            drift.append((name, "RENDER FAILED", "", ""))
            continue
        checked += 1
        fa, fb = dict(_flat(a)), dict(_flat(b))
        moved = [(k, fa[k], fb[k]) for k in fa
                 if k in fb and fa[k] != fb[k] and not any(k.startswith(g) for g in ignore)]
        gone = [k for k in fa if k not in fb and not any(k.startswith(g) for g in ignore)]
        added = [k for k in fb if k not in fa and not any(k.startswith(g) for g in ignore)]
        status = "NEUTRAL" if not (moved or gone) else "DRIFT"
        print(f"  {name:6} {status:8} moved={len(moved)} removed={len(gone)} added={len(added)}")
        for k, x, y in moved[:8]:
            print(f"        MOVED   {k}: {x!r} -> {y!r}")
        for k in gone[:8]:
            print(f"        REMOVED {k}")
        if moved or gone:
            drift.append((name, moved, gone, added))

    print()
    if drift:
        print(f"NOT NEUTRAL: {len(drift)} of {len(fixtures)} fixtures drifted. "
              f"Read every line above before touching a baseline.")
        return 1
    print(f"NEUTRAL: {checked} fixtures, no computed figure moved and no field removed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
