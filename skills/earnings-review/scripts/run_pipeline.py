"""Earnings Review - run_pipeline.py: apply_overrides (if overrides.json) -> compute -> build_html -> render_pdf -> validate.
Prints stdout AND stderr of every step (the validator's included), times each phase, and writes <workdir>/status.json.
On failure the last stdout line is the JSON envelope {"status":"error","step":...,"message":...} (class 9b).
Usage: python run_pipeline.py <workdir> [--no-pdf]   (--no-pdf deletes any older PDF and tells the validator the PDF was skipped on purpose)"""
import subprocess, sys, os, json, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from version import VERSION

def main():
    W = os.path.abspath(next((a for a in sys.argv[1:] if not a.startswith("--")), ".")); nopdf = "--no-pdf" in sys.argv
    py = sys.executable; env = dict(os.environ, PYTHONUTF8="1"); status = {"version": VERSION, "workdir": W, "steps": []}
    def done(code, msg=None, step=None):
        status["status"] = "ok" if code == 0 else "error"
        if msg: status["message"] = msg
        if os.path.isdir(W): json.dump(status, open(os.path.join(W, "status.json"), "w", encoding="utf-8"), indent=1)
        if code and msg: print(json.dumps({"status": "error", "step": step, "code": code, "message": msg}, ensure_ascii=False))
        sys.exit(code)
    def run(name, *a):
        t0 = time.time(); r = subprocess.run([py, os.path.join(HERE, a[0])] + list(a[1:]), cwd=HERE, env=env, text=True, capture_output=True)
        status["steps"].append({"step": name, "exit": r.returncode, "seconds": round(time.time() - t0, 2)})
        if r.stdout.strip(): print("\n".join(l for l in r.stdout.strip().splitlines() if not l.startswith('{"status": "error"')))
        if r.stderr.strip(): print(r.stderr.strip())
        if r.returncode != 0:
            env_line = next((l for l in reversed(r.stdout.splitlines()) if l.startswith('{"status": "error"')), None)
            msg = json.loads(env_line)["message"] if env_line else (r.stderr.strip().splitlines() or [f"{name} exited {r.returncode}"])[-1]
            done(r.returncode, msg, name)
        return r
    if not all(os.path.exists(os.path.join(W, f)) for f in ("data.json", "review_config.json", "prose.json")):
        done(1, "workdir must hold data.json, review_config.json and prose.json (see references/pipeline-contracts.md)", "run_pipeline")
    if os.path.exists(os.path.join(W, "overrides.json")): run("apply_overrides", "apply_overrides.py", W)
    run("compute", "compute.py", W); run("build_html", "build_html.py", W)
    d = json.load(open(os.path.join(W, "data.json"), encoding="utf-8")); cfg = json.load(open(os.path.join(W, "review_config.json"), encoding="utf-8"))
    stem = f"{d['company']['ticker']}_{d['period']['label']}_Earnings_Review"; pdf = os.path.join(W, stem + ".pdf")
    if nopdf:
        if os.path.exists(pdf): os.remove(pdf); print(f"removed older {os.path.basename(pdf)} (--no-pdf)")
    else:
        foot = cfg.get("footer") or (f"{d['company']['ticker']} {d['period']['label']} Earnings Review  |  Source: company release, transcript, InSync, InSync prices  |  "
                                     f"InSync consensus as of {d['consensus']['frozen_date']}, market {d.get('valuation', {}).get('asof') or d['consensus']['post_print_date']}")
        run("render_pdf", "render_pdf.py", os.path.join(W, stem + ".html"), pdf, foot)
    t0 = time.time(); r = subprocess.run([py, os.path.join(HERE, "validate_review.py"), W] + (["--no-pdf"] if nopdf else []), cwd=HERE, env=env, text=True, capture_output=True)
    status["steps"].append({"step": "validate", "exit": r.returncode, "seconds": round(time.time() - t0, 2)})
    print(r.stdout.strip())
    if r.stderr.strip(): print(r.stderr.strip())
    status["validator"] = {"errors": sum(1 for l in r.stdout.splitlines() if l.startswith("ERROR")), "warnings": sum(1 for l in r.stdout.splitlines() if l.startswith("WARN"))}
    if r.returncode != 0:
        last = next((l for l in r.stdout.splitlines() if l.startswith("ERROR")), None) or (r.stderr.strip().splitlines() or ["validator failed"])[-1]
        done(r.returncode, f"validator: {status['validator']['errors']} error(s); first: {last}", "validate")
    done(0)

if __name__ == "__main__": main()
