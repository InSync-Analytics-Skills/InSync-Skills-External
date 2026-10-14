#!/usr/bin/env python3
"""
self_check.py - v11 install and regression check. Python only; Node is used when present.

  python3 self_check.py              check the install (runtimes, files, docs, gates, golden page)
  python3 self_check.py <out.docx>   also check a rendered flash (render gate, table geometry, masthead)

Exits 0 with "SELF-CHECK OK", or 1 listing what failed.
"""
import glob
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
fails = []


def ok(label):
    print("  ok    " + label)


def bad(label, why=""):
    print("  FAIL  " + label + ((" - " + why) if why else ""))
    fails.append(label)


def read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


if sys.version_info >= (3, 9):
    ok("python %d.%d" % sys.version_info[:2])
else:
    bad("python", "3.9+ needed, found %d.%d" % sys.version_info[:2])
node = shutil.which("node")
_node_docx = bool(node) and (os.path.isfile(os.path.join(HERE, "vendor", "docx.cjs")) or subprocess.run(
    [node, "-e", "require('docx')"], cwd=HERE, capture_output=True).returncode == 0)
print("  info  node: %s" % ("present - pages drawn by _render_internal.js" if _node_docx
                            else ("present, no docx library - " if node else "absent - ")
                            + "pages drawn by _render_internal.py (same output)"))
print("  info  LibreOffice: %s" % ("present - PDF twin and measured one-page fit" if (shutil.which("soffice") or shutil.which("libreoffice"))
                                   else "absent - .docx only, one-page fit estimated"))

need = ["SKILL.md", "README.md", "assets/metric_registry.csv|assets/metric_registry.json",
        "assets/valuation_catalogue.json", "assets/insync-logo-mark.png", "common/brand.json",
        "references/writing-rules.md", "references/errors.md", "references/flash.py",
        "references/run_pipeline.py", "references/run_math.py", "references/_render_internal.py",
        "references/copy_check.py", "references/narrative_template.py",
        "references/render_gate.py", "references/check_table_widths.py", "references/check_masthead.py",
        "references/docx_template/word/styles.xml", "references/docx_template/word/numbering.xml"]
missing = [p for p in need if not any(os.path.isfile(os.path.join(ROOT, a)) for a in p.split("|"))]
ok("%d required files present" % len(need)) if not missing else bad("files", "missing: " + ", ".join(missing))
INTERNAL = all(os.path.exists(os.path.join(ROOT, p)) for p in
               ("references/vendor/docx.cjs", "references/tests/golden/adbe_v10.docx", "maintainer/CHANGELOG.md"))
print("  info  edition: %s" % ("internal (tests, bundled docx library, maintainer notes)" if INTERNAL
                               else "client (no tests or maintainer notes)"))

errs = []
for p in glob.glob(os.path.join(HERE, "*.py")) + glob.glob(os.path.join(HERE, "tests", "*.py")):
    try:
        py_compile.compile(p, doraise=True)
    except py_compile.PyCompileError as e:
        errs.append(os.path.basename(p) + ": " + str(e).splitlines()[0])
if node and os.path.isfile(os.path.join(HERE, "_render_internal.js")):
    r = subprocess.run([node, "--check", os.path.join(HERE, "_render_internal.js")], capture_output=True, text=True)
    if r.returncode:
        errs.append("_render_internal.js: " + r.stderr.strip()[:120])
ok("all scripts parse") if not errs else bad("scripts parse", "; ".join(errs))

try:
    sm = read("SKILL.md")
    fm = re.match(r"^---\n(.*?)\n---\n", sm, re.S).group(1)
    name = re.search(r"^name:\s*(.+)$", fm, re.M).group(1).strip()
    desc = re.search(r'^description:\s*"?(.*?)"?$', fm, re.M).group(1)
    version = re.search(r"^version:\s*([0-9.]+)", fm, re.M).group(1)
    ok("name gate (%s)" % name) if len(name) <= 64 and not re.search("anthropic|claude", name, re.I) else bad("name gate", name)
    ok("description gate (%d/1024 chars)" % len(desc)) if 0 < len(desc) <= 1024 else bad("description gate", str(len(desc)))
    n = len(sm)
    ok("SKILL.md is %d characters (limit 25,000, target 8,000)" % n) if n < 25000 else bad("SKILL.md size", "%d chars" % n)
    total = n + len(read("references", "writing-rules.md")) + len(read("references", "errors.md"))
    ok("all run-time instructions %d characters (limit 25,000)" % total) if total < 25000 else bad("instruction size", str(total))
    for ref in sorted(set(re.findall(r"`(references/[\w./-]+)`", sm))):
        if not os.path.exists(os.path.join(ROOT, ref)):
            bad("SKILL.md link", ref + " does not exist")
    where = {
        "SKILL.md reply line": "earnings-flash version v%s" % version in sm,
        "SKILL.md banner": "**SKILL VERSION: v%s " % version in sm,
        "README.md": "*Version %s*" % version in read("README.md"),
        "maintainer/CHANGELOG.md": (not INTERNAL) or re.search(r"^## v%s " % re.escape(version), read("maintainer", "CHANGELOG.md"), re.M) is not None,
        "run_pipeline.py": '"skill_version": "v%s"' % version in read("references", "run_pipeline.py"),
    }
    miss = [k for k, v in where.items() if not v]
    ok("version %s consistent across SKILL.md, README, CHANGELOG and run_pipeline.py" % version) if not miss \
        else bad("version consistency", "missing in " + ", ".join(miss))
except Exception as e:
    bad("SKILL.md", str(e))

try:
    import run_pipeline
    code = sorted(run_pipeline._BANNED_ADJECTIVES)
    wr = read("references", "writing-rules.md")
    i = wr.index("Banned adjectives")
    doc = sorted(re.findall(r'"([a-z-]+)"', wr[i:i + 400]))
    ok("banned words in writing-rules.md match the linter (%d)" % len(code)) if doc == code \
        else bad("banned words drift", "doc=%s code=%s" % (doc, code))
    src = read("references", "run_math.py") + read("references", "run_pipeline.py")
    er = read("references", "errors.md")
    stop = er[er.index("## Warnings that must be cleared"):er.index("\"Cleared\" means")]
    toks = re.findall(r"`([^`]+)`", stop)
    absent = [t for t in toks if t.split(" when")[0] not in src]
    ok("every STOP warning in errors.md is raised by the code (%d)" % len(toks)) if not absent \
        else bad("STOP list drift", "not in code: " + ", ".join(absent))
    for label, needle in [("broker disclosure lint", "def _lint_broker_disclosure"),
                          ("fact coverage lint", "def fact_coverage_lint"),
                          ("copy check wired in", "copy_check_failed"),
                          ("renderer choice wired in", "_render_internal.py"),
                          ("render gate stage 9b", "render_gate_failed"),
                          ("ticker identity gate", "ticker_identity_failed")]:
        ok(label) if needle in src else bad(label, "not found in the pipeline")
except Exception as e:
    bad("rule checks", str(e))

_tests = [("gate behaviour (gate_check.py)", [sys.executable, os.path.join(HERE, "gate_check.py")]),
          ("metric registry (registry_check.py)", [sys.executable, os.path.join(HERE, "registry_check.py")]),
          ("mcp guard replay (replay_live_run.py)", [sys.executable, os.path.join(HERE, "replay_live_run.py")])]
if INTERNAL:
    _tests.append(("golden page matches v10 (tests/golden_check.py)",
                   [sys.executable, os.path.join(HERE, "tests", "golden_check.py")]))
    _tests.append(("fetch driver scenarios: KPI ladder, release-day guidance (tests/scenarios_check.py)",
                   [sys.executable, os.path.join(HERE, "tests", "scenarios_check.py")]))
    _tests.append(("Track B: guide tags, bench check, withdrawn hint, document read (tests/trackb_check.py)",
                   [sys.executable, os.path.join(HERE, "tests", "trackb_check.py")]))
    _tests.append(("test-run fixes: verify text, consensus paging, moved codes, release day (tests/fixes_check.py)",
                   [sys.executable, os.path.join(HERE, "tests", "fixes_check.py")]))
    _tests.append(("data fixes: guide currency and scale, one-offs, price listing, wide paging (tests/datafix_check.py)",
                   [sys.executable, os.path.join(HERE, "tests", "datafix_check.py")]))
    _tests.append(("review batch 1: foreign KPIs, call count, stale flag, period sort, call date, KPI repeat, net income (tests/batch1_check.py)",
                   [sys.executable, os.path.join(HERE, "tests", "batch1_check.py")]))
    _tests.append(("review batch 2: guide units and periods, cost lines, decimals, direction, company figures, unit errors (tests/batch2_check.py)",
                   [sys.executable, os.path.join(HERE, "tests", "batch2_check.py")]))
    _tests.append(("review batch 3: guide format, financial summary, half probe, cut-off zones, warnings, prose minus (tests/batch3_check.py)",
                   [sys.executable, os.path.join(HERE, "tests", "batch3_check.py")]))
    _tests.append(("compact and app-saved MCP responses give the same run (tests/compact_check.py)",
                   [sys.executable, os.path.join(HERE, "tests", "compact_check.py")]))
    _tests.append(("v11.2.0 consensus fetch, pipe rows, combined KPI search: same numbers as v11.1.3 (tests/v112_check.py)",
                   [sys.executable, os.path.join(HERE, "tests", "v112_check.py")]))
if INTERNAL and node:
    _tests.append(("renderer parity js = py (tests/renderer_parity.py)",
                   [sys.executable, os.path.join(HERE, "tests", "renderer_parity.py")]))
for label, cmd in _tests:
    if not os.path.isfile(cmd[1]):
        print("  info  %s skipped (internal edition only)" % label)
        continue
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if r.returncode == 0:
        ok(label)
    else:
        lines = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
        bad(label, (lines[-1][:200] if lines else "exit %d" % r.returncode))

try:
    if not INTERNAL:
        raise StopIteration
    import copy
    import copy_check
    inp = json.load(open(os.path.join(HERE, "tests", "golden", "adbe_input.json"), encoding="utf-8"))
    b0, _ = copy_check.check(inp)
    x = copy.deepcopy(inp)
    x["mcp_responses"]["get_estimates"]["data"][1]["vl"] = "$6.8024B"
    b1, _ = copy_check.check(x)
    ok("copy check: clean payload passes, a mistyped consensus is caught") if not b0 and b1 \
        else bad("copy check", "clean=%d blocks, slip=%d blocks" % (len(b0), len(b1)))
except StopIteration:
    print("  info  copy-check test skipped (its test data is in the internal edition)")
except Exception as e:
    bad("copy check", str(e))

if len(sys.argv) > 1:
    docx = sys.argv[1]
    if not os.path.isfile(docx):
        bad("rendered file", "no such file: " + docx)
    else:
        base = docx[:-5]
        pdf = base + ".pdf" if os.path.isfile(base + ".pdf") else None
        r = subprocess.run([sys.executable, os.path.join(HERE, "render_gate.py"), docx] + (["--pdf", pdf] if pdf else []),
                           capture_output=True, text=True)
        ok("render gate - " + (r.stdout.strip().splitlines() or [""])[0]) if r.returncode == 0 else bad("render gate", r.stdout.strip()[-200:])
        r = subprocess.run([sys.executable, os.path.join(HERE, "check_table_widths.py"), docx], capture_output=True, text=True)
        ok("table geometry") if r.returncode == 0 else bad("table geometry", r.stdout.strip()[-200:])
        if pdf:
            r = subprocess.run([sys.executable, os.path.join(HERE, "check_masthead.py"), pdf], capture_output=True, text=True)
            tail = (r.stdout.strip().splitlines() or [""])[-1]
            if r.returncode == 0:
                ok("masthead geometry - " + tail)
            elif r.returncode == 2:
                print("  info  masthead UNVERIFIED - Georgia is not installed here; geometry passed in the substitute face")
            else:
                bad("masthead geometry", tail)

print("\nSELF-CHECK OK" if not fails else "\nSELF-CHECK FAILED: %d item(s): %s" % (len(fails), ", ".join(fails)))
sys.exit(1 if fails else 0)
