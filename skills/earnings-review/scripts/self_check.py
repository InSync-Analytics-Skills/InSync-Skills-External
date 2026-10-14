"""Release gate: every check, each exit code captured on its own line (no pipes, no &&: a pipe reports the LAST command's
status and turns a red build green). Run before EVERY release; maintainer/build_client.py runs it with --pre-build.
  1 compile        every script compiles
  2 version        scripts/version.py == SKILL.md == CHANGELOG head
  3 registry       every documented metric code is in the polarity registry
  4 docs           tests/test_docs.py (docs vs code, docs vs docs)
  5 fixture        the bundled fixture reproduces its expected data.json; the refusal case exits 2
  6 audit classes  tests/test_audit_classes.py (one test per audit class)
  7 corpus         seven synthetic cases render; computed tokens equal tests/corpus/expected_tokens.json
  8 gates          tests/test_gates.py on the rendered control case
  9 public scan    maintainer/public_release_scan.py
 10 manifest       the folder matches MANIFEST.json (skipped with --pre-build, i.e. while building)
Usage: python scripts/self_check.py [--pre-build] [--update-golden]   exit 0 only when every check passes"""
import json, os, re, shutil, subprocess, sys, tempfile, glob, py_compile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); SC = os.path.join(ROOT, "scripts"); PY = sys.executable
ENV = dict(os.environ, PYTHONUTF8="1"); RES = []
def step(name, rc, detail=""):
    RES.append((name, rc)); print(f"[{'PASS' if rc == 0 else 'FAIL'}] {name} (exit {rc}) {detail}".rstrip(), flush=True)
def run(*cmd, cwd=None): return subprocess.run([PY, *map(str, cmd)], capture_output=True, text=True, env=ENV, cwd=cwd or ROOT)

def main():
    pre = "--pre-build" in sys.argv; upd = "--update-golden" in sys.argv; TMP = tempfile.mkdtemp(prefix="er_self_")
    bad = []
    for f in glob.glob(os.path.join(ROOT, "scripts", "*.py")) + glob.glob(os.path.join(ROOT, "tests", "*.py")) + glob.glob(os.path.join(ROOT, "tests", "corpus", "*.py")) + glob.glob(os.path.join(ROOT, "maintainer", "*.py")):
        try: py_compile.compile(f, doraise=True)
        except py_compile.PyCompileError as e: bad.append(f"{os.path.basename(f)}: {e.msg[:80]}")
    step("compile", 1 if bad else 0, "; ".join(bad))
    sys.path.insert(0, SC); import version
    sk = re.search(r"^version:\s*(\S+)", open(os.path.join(ROOT, "SKILL.md"), encoding="utf-8").read(), re.M).group(1)
    ch = re.search(r"^## (\S+)", open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8").read(), re.M).group(1)
    step("version", 0 if version.VERSION == sk == ch else 1, f"version.py {version.VERSION}, SKILL.md {sk}, CHANGELOG {ch}")
    r = run(os.path.join(SC, "polarity.py"), "--lint"); step("registry", r.returncode, (r.stdout.splitlines() or [""])[0])
    if not (os.path.isdir(os.path.join(ROOT, "tests")) and os.path.isdir(os.path.join(ROOT, "maintainer"))):
        for n in ("docs", "fixture", "refusal", "audit classes", "corpus", "gates", "public scan", "manifest"):
            print(f"[SKIP] {n} (maintainer edition only: tests/ and maintainer/ are not in the client edition)")
        shutil.rmtree(TMP, ignore_errors=True); failed = [n for n, rc in RES if rc]
        print(f"self_check: {len(RES) - len(failed)}/{len(RES)} passed (client edition)" + (f"; FAILED: {', '.join(failed)}" if failed else ""))
        sys.exit(1 if failed else 0)
    r = run(os.path.join(ROOT, "tests", "test_docs.py")); step("docs", r.returncode, (r.stdout.strip().splitlines() or [""])[-1])
    if r.returncode: print(r.stdout[-1500:])
    W = os.path.join(TMP, "fx"); shutil.copytree(os.path.join(ROOT, "tests", "fixture_build_dataset"), W)
    exp = json.load(open(os.path.join(W, "data.json"), encoding="utf-8")); args = json.load(open(os.path.join(W, "build_args.json")))
    r = run(os.path.join(SC, "build_dataset.py"), W, *args); got = json.load(open(os.path.join(W, "data.json"), encoding="utf-8"))
    step("fixture", 0 if (r.returncode == 0 and got == exp) else 1, "" if got == exp else "output differs from tests/fixture_build_dataset/data.json")
    ra = list(args)
    for k, v in (("--period", "4Q26"), ("--next", "1Q27"), ("--periods", "2Q26,3Q26,4Q26")): ra[ra.index(k) + 1] = v
    W2 = os.path.join(TMP, "fx2"); shutil.copytree(os.path.join(ROOT, "tests", "fixture_build_dataset"), W2)
    r = run(os.path.join(SC, "build_dataset.py"), W2, *ra); step("refusal", 0 if r.returncode == 2 and "REFUSED" in r.stdout + r.stderr else 1, f"exit {r.returncode}")
    r = run(os.path.join(ROOT, "tests", "test_audit_classes.py")); step("audit classes", r.returncode, (r.stdout.strip().splitlines() or [""])[-1])
    if r.returncode: print("\n".join(l for l in r.stdout.splitlines() if l.startswith("FAIL"))[:2000])
    C = os.path.join(TMP, "corpus"); O = os.path.join(TMP, "out")
    run(os.path.join(ROOT, "tests", "corpus", "make_corpus.py"), C)
    r = run(os.path.join(ROOT, "tests", "run_corpus.py"), C, O, "--skill", ROOT)
    toks = {c: json.load(open(os.path.join(O, c, "computed.json"), encoding="utf-8"))["tokens"] for c in sorted(os.listdir(O)) if os.path.exists(os.path.join(O, c, "computed.json"))}
    gp = os.path.join(ROOT, "tests", "corpus", "expected_tokens.json")
    if upd or not os.path.exists(gp):
        json.dump(toks, open(gp, "w", encoding="utf-8"), indent=0, ensure_ascii=False, sort_keys=True); step("corpus", 0, f"golden tokens written ({sum(len(v) for v in toks.values())}); review the diff before committing")
    else:
        g = json.load(open(gp, encoding="utf-8")); diffs = [(c, k, g.get(c, {}).get(k), toks.get(c, {}).get(k)) for c in set(g) | set(toks) for k in set(g.get(c, {})) | set(toks.get(c, {})) if g.get(c, {}).get(k) != toks.get(c, {}).get(k)]
        step("corpus", 1 if diffs or len(toks) != 7 else 0, f"{len(toks)} cases, {len(diffs)} token difference(s) from the golden file" + ("; run tests/compare_runs.py and --update-golden only for intended changes" if diffs else ""))
        for dd in diffs[:15]: print("   ", dd)
    A = os.path.join(O, "A_control")
    r = run(os.path.join(ROOT, "tests", "test_gates.py"), A) if os.path.isdir(A) else None
    step("gates", r.returncode if r else 1, (r.stdout.strip().splitlines() or [""])[-1] if r else "control case missing")
    r = run(os.path.join(ROOT, "maintainer", "public_release_scan.py"), ROOT); step("public scan", r.returncode, (r.stdout.strip().splitlines() or [""])[-1])
    if pre: print("[SKIP] manifest (pre-build)")
    else:
        r = run(os.path.join(ROOT, "maintainer", "build_client.py"), "--check", ROOT); step("manifest", r.returncode, (r.stdout.strip().splitlines() or [""])[-1])
    shutil.rmtree(TMP, ignore_errors=True)
    failed = [n for n, rc in RES if rc]
    print(f"self_check: {len(RES) - len(failed)}/{len(RES)} passed" + (f"; FAILED: {', '.join(failed)}" if failed else ""))
    sys.exit(1 if failed else 0)

if __name__ == "__main__": main()
