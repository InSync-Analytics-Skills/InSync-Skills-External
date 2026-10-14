#!/usr/bin/env python3
"""earnings-packet-3p, the Step 8 gauntlet in ONE call.

    python3 references/verify_all.py "<the built>.pdf" "<the built>.docx"
    python3 references/verify_all.py "<...>.pdf" "<...>.docx" --strict-precision
    python3 references/verify_all.py "<...>.pdf" "<...>.docx" --lenient-prose

WHAT THIS IS AND IS NOT
-----------------------
It is a RUNNER. It runs exactly the same four commands Step 8 has always named, in the
same order, with the same arguments, and it adds no check and relaxes none:

    compute.py --figures            publish the figure set (pass 3 refuses without it)
    validate_packet.py <pdf>        passes 1 to 6
    validate_rules_style.py <pdf>   passes 2, 5 and 6 re-derived from horizontal rules
    verify_delivered.py <docx>      pass 7, the only pass that opens the delivered file
    self_check.py <pdf>             armed with the PDF: masthead geometry + determinism

It exists because those were five tool calls and five context round-trips for one
question, not because anything about them needed changing. Unknown flags are passed
through to the pass that owns them, so an escape stays available.

TWO PROPERTIES THAT MAKE IT SAFE TO BATCH
-----------------------------------------
1. EVERY pass runs, even after one fails. A runner that stops at the first failure turns
   a seven-pass gauntlet into a one-pass gauntlet on exactly the runs that need it most,
   and hides the second defect behind the first.
2. The exit code is the WORST outcome, and the summary states every pass by name with its
   own verdict. A pass that did not run is reported as `did not run`, never omitted:
   a gate which goes quiet looks exactly like a gate that keeps passing. Verify the summary, never the exit code.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def run(cmd, cwd):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=600)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 99, "runner could not execute: %s" % e


REFUSAL = {
    "validate": ("figure set", "publish the figure set with compute.py --figures"),
}


def verdict(rc, out, kind):
    if rc == 99:
        return "DID NOT RUN", out.strip().splitlines()[:1] or ["the runner could not start it"]
    if rc == 0:
        for line in out.splitlines():
            low = line.lower()
            if "report" in low and "finding(s)" in low and " 0 finding(s)" not in low:
                return "PASSED, WITH FINDINGS", [l for l in out.splitlines()
                                                 if l.strip().startswith("[")][:6]
        return "PASSED", []
    marker = REFUSAL.get(kind)
    if marker:
        for line in out.splitlines():
            low = line.lower()
            if marker[0] in low and ("refus" in low or "cannot run" in low):
                return "DID NOT RUN", [marker[1]]
    return "FAILED", [l for l in out.splitlines() if l.strip()][-12:]


def main():
    args = [a for a in sys.argv[1:]]
    passthru = [a for a in args if a.startswith("-")]
    files = [a for a in args if not a.startswith("-")]
    if len(files) < 2:
        print(__doc__.split("WHAT THIS IS")[0].strip(), file=sys.stderr)
        print("\nsupply the built PDF and the built .docx", file=sys.stderr)
        return 2
    pdf, docx = files[0], files[1]
    cwd = os.getcwd()

    for f, what in ((pdf, "PDF"), (docx, ".docx")):
        if not os.path.exists(os.path.join(cwd, f)) and not os.path.exists(f):
            print("the %s does not exist where this was run: %s" % (what, f), file=sys.stderr)
            print("run this from the working directory holding packet_computed.json",
                  file=sys.stderr)
            return 2

    prose = [a for a in passthru if a in ("--lenient-prose", "--strict-prose")]
    prec = [a for a in passthru if a == "--strict-precision"]
    strict7 = [a for a in passthru if a == "--strict"]

    steps = [
        ("figures", "figure set   (compute.py --figures)",
         [PY, os.path.join(HERE, "compute.py"), "--figures"]),
        ("validate", "passes 1-6   (validate_packet.py)",
         [PY, os.path.join(HERE, "validate_packet.py"), pdf] + prose + prec),
        ("rules", "passes 2,5,6 (validate_rules_style.py, from horizontal rules)",
         [PY, os.path.join(HERE, "validate_rules_style.py"), pdf]),
        ("delivered", "pass 7       (verify_delivered.py, the delivered document)",
         [PY, os.path.join(HERE, "verify_delivered.py"), docx] + strict7),
        ("selfcheck", "masthead + determinism (self_check.py, armed with the PDF)",
         [PY, os.path.join(HERE, "self_check.py"), pdf]),
    ]

    results = []
    worst = 0
    print("=" * 78)
    for kind, name, cmd in steps:
        rc, out = run(cmd, cwd)
        v, tail = verdict(rc, out, kind)
        results.append((name, v, tail, out))
        worst = max(worst, 0 if v.startswith("PASSED") else (1 if v == "FAILED" else 2))
        print("\n----- %s -----" % name)
        if v != "PASSED" or os.environ.get("PACKET_VERBOSE") == "1":
            print(out.rstrip() or "(no output)")
        else:
            print("\n".join(l for l in out.rstrip().splitlines()
                            if not l.lstrip().startswith("ok ")) or "(no output)")

    print("\n" + "=" * 78)
    print("STEP 8 SUMMARY")
    print("=" * 78)
    for name, v, tail, _ in results:
        print("  %-62s %s" % (name, v))
        for line in tail:
            print("        " + line[:110])

    ran = sum(1 for _, v, _, _ in results if v != "DID NOT RUN")
    withf = sum(1 for _, v, _, _ in results if v == "PASSED, WITH FINDINGS")
    print("\n  %d of %d gates ran; %d passed%s."
          % (ran, len(results),
             sum(1 for _, v, _, _ in results if v.startswith("PASSED")),
             (", %d of them reporting findings that did not block" % withf) if withf else ""))
    if withf:
        print("  A gate that reports without blocking is still a gate that found something.")
    if worst == 2:
        print("  A GATE DID NOT RUN. That is not a pass. Fix the cause and run again.")
    print("\n  Mechanical passes do not read the page. Now read the PDF yourself, page by\n"
          "  page, as an image: a caveat naming a pruned row, a sentence running into an\n"
          "  auto-appended dropNote with no full stop, a unit prefix leaking out of a\n"
          "  pruned label, a figure quoted at a different precision from the cell beside\n"
          "  it, a heading with no table under it, a header row wrapped to two lines.\n"
          "  Every one of those has shipped from a build that passed all six PDF passes.")
    return worst


if __name__ == "__main__":
    sys.exit(main())
