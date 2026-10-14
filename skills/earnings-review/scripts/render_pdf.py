"""HTML -> PDF with headless Edge/Chrome/Chromium, then stamp the house footer ("InSync Analytics | ... " left, "Page X of Y"
right) with PyMuPDF, because Chromium ignores CSS @page margin boxes. The footer's dates are never cut (render_pdf_fit.py).
Browser search order: $BROWSER_PATH, then Edge and Chrome on Windows, macOS and Linux, then chromium on PATH.
In a container running as root, point BROWSER_PATH at a wrapper that adds --no-sandbox (see tests/ENV.md).
Usage: python render_pdf.py in.html out.pdf "footer text" """
import subprocess, sys, os, shutil, pathlib, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from erlib import envelope, guarded
from render_pdf_fit import fit_footer

def main():
    if len(sys.argv) < 4: envelope("render_pdf", "usage: render_pdf.py in.html out.pdf \"footer text\"")
    try: import pymupdf as fitz
    except ImportError: import fitz
    src, out, foot = sys.argv[1], sys.argv[2], sys.argv[3]
    cands = [os.environ.get("BROWSER_PATH"),
             r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
             r"C:\Program Files\Google\Chrome\Application\chrome.exe",
             "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
             "/usr/bin/microsoft-edge", "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium", "/usr/bin/chromium-browser",
             shutil.which("chromium") or "", shutil.which("google-chrome") or "", shutil.which("msedge") or ""]
    browsers = list(dict.fromkeys(c for c in cands if c and os.path.exists(c)))
    if not browsers: envelope("render_pdf", "no Chromium browser found; install Edge/Chrome/Chromium or set BROWSER_PATH")
    fd, tmp = tempfile.mkstemp(prefix="_raw_", suffix=".pdf", dir=os.path.dirname(os.path.abspath(out))); os.close(fd)
    url = pathlib.Path(os.path.abspath(src)).as_uri(); last_err = ""
    try:
        for browser in browsers:
            if os.path.exists(tmp): os.remove(tmp)
            cmd = [browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--print-to-pdf-no-header", f"--print-to-pdf={tmp}", url]
            try: r = subprocess.run(cmd, capture_output=True, text=True, timeout=120); last_err = f"{os.path.basename(browser)}: {r.stderr[-300:]}"
            except Exception as e: last_err = f"{os.path.basename(browser)}: {e}"; continue
            if os.path.exists(tmp) and os.path.getsize(tmp) > 1000: break
        if not (os.path.exists(tmp) and os.path.getsize(tmp) > 1000): envelope("render_pdf", "render failed with every browser: " + last_err)
        doc = fitz.open(tmp); n = len(doc); GREY = (0x6E / 255, 0x6E / 255, 0x6E / 255); changed = False
        for i, page in enumerate(doc):
            w, h = page.rect.width, page.rect.height
            txt = f"Page {i + 1} of {n}"; tw = fitz.get_text_length(txt, fontname="helv", fontsize=6.5)
            lines = fit_footer(foot, w - 72 - tw - 14)
            if lines != ["InSync Analytics  |  " + "  |  ".join(s.strip() for s in foot.split("|"))]: changed = True
            y = h - 0.33 * 72 - (7.5 if len(lines) > 1 else 0)
            for j, line in enumerate(lines): page.insert_text((0.5 * 72, y + 7.5 * j), line, fontsize=6.5, fontname="helv", color=GREY)
            page.insert_text((w - 0.5 * 72 - tw, h - 0.33 * 72), txt, fontsize=6.5, fontname="helv", color=GREY)
        doc.save(out, garbage=3, deflate=True); doc.close()
    finally:
        if os.path.exists(tmp): os.remove(tmp)
    if changed: print("WARN footer shortened to fit (source list shortened or wrapped; dates kept)")
    print(f"pdf ok: {out}  pages={n}")

if __name__ == "__main__": guarded("render_pdf", main)
