#!/usr/bin/env python3
"""Build a branded single-file HTML contact sheet of rendered charts.

Use it to review a report's exhibits before they go into the document, or to
circulate a set of charts without attaching a folder of images.

    python render_chart.py --spec charts.json --out-dir charts/ > manifest.json
    python build_gallery.py --manifest manifest.json --out charts/gallery.html

    # or point it at a directory of already-rendered charts
    python build_gallery.py --dir charts/ --out charts/gallery.html

The output is one self-contained .html file: images are embedded as base64, no
CDN, no external assets, no JavaScript beyond the filter and the lightbox. It
carries the same masthead, palette and trust mark as the charts themselves.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import glob
import html
import io
import json
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from insync_charts import registry, theme

MAX_EMBED_WIDTH = 1100


def _downscale(png_bytes: bytes, max_w: int) -> bytes:
    try:
        from PIL import Image
    except Exception:
        return png_bytes
    try:
        im = Image.open(io.BytesIO(png_bytes))
        if im.width <= max_w:
            return png_bytes
        h = int(im.height * max_w / im.width)
        im = im.convert("RGB").resize((max_w, h), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception:
        return png_bytes


def _b64_png(path: str, max_w: int) -> Optional[str]:
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    return base64.b64encode(_downscale(data, max_w)).decode("ascii")


def _family(chart_type: str) -> str:
    try:
        return registry.resolve(chart_type)["family"] or "Other"
    except KeyError:
        return "Other"


def _title_of(chart_type: str) -> str:
    try:
        return registry.resolve(chart_type)["title"]
    except KeyError:
        return chart_type.replace("_", " ").title()


def collect_from_manifest(path: str) -> List[Dict[str, Any]]:
    with (sys.stdin if path == "-" else open(path, "r", encoding="utf-8")) as fh:
        data = json.load(fh)
    charts = data.get("charts", data if isinstance(data, list) else [])
    out = []
    for c in charts:
        if not c.get("ok"):
            continue
        png = (c.get("files") or {}).get("png")
        if not png:
            continue
        out.append({
            "chart_type": c.get("chart_type", ""),
            "title": c.get("title") or _title_of(c.get("chart_type", "")),
            "ticker": c.get("ticker", ""),
            "png": png,
            "svg": (c.get("files") or {}).get("svg"),
            "warnings": c.get("warnings") or [],
        })
    return out


def collect_from_dir(d: str) -> List[Dict[str, Any]]:
    known = sorted(registry.REGISTRY, key=len, reverse=True)
    out = []
    for png in sorted(glob.glob(os.path.join(d, "*.png"))):
        stem = os.path.splitext(os.path.basename(png))[0]
        low = stem.lower()
        ct = next((k for k in known if k in low), "")
        svg = os.path.splitext(png)[0] + ".svg"
        out.append({
            "chart_type": ct,
            "title": _title_of(ct) if ct else stem.replace("_", " "),
            "ticker": "", "png": png,
            "svg": svg if os.path.exists(svg) else None,
            "warnings": [],
        })
    return out


CSS = """
:root {{
  --ink: {ink}; --orange: {orange}; --orange-dk: {orange_dk};
  --grey: {grey}; --grey-lt: {grey_lt}; --hairline: {hairline};
  --grid: {grid}; --bg: {bg};
  --serif: Georgia, Caladea, "Liberation Serif", "Times New Roman", serif;
  --sans: Calibri, Carlito, "Liberation Sans", "Helvetica Neue", Arial, sans-serif;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; padding: 32px 36px 56px; background: var(--bg); color: var(--ink);
  font-family: var(--sans); font-size: 14px; line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}}
.masthead {{ display: flex; align-items: flex-start; justify-content: space-between;
  gap: 24px; max-width: 1360px; margin: 0 auto; }}
h1 {{ font-family: var(--serif); font-size: 25px; font-weight: 700; margin: 0 0 4px;
  letter-spacing: -0.2px; }}
.sub {{ color: var(--grey); font-size: 13px; margin: 0; }}
.mark {{ display: flex; align-items: center; gap: 8px; white-space: nowrap;
  padding-top: 4px; }}
.mark img {{ height: 26px; width: 26px; display: block; }}
.mark span {{ font-size: 12px; font-weight: 700; letter-spacing: 0.3px; }}
.mark .i {{ color: var(--orange); }}
.rule {{ max-width: 1360px; margin: 12px auto 0; height: 2px; background: var(--orange); }}
.bar {{ max-width: 1360px; margin: 18px auto 0; display: flex; flex-wrap: wrap;
  align-items: center; gap: 10px; }}
.stat {{ font-size: 12px; color: var(--grey); border: 1px solid var(--hairline);
  padding: 5px 10px; }}
.stat b {{ color: var(--ink); font-weight: 700; }}
button.f {{ font-family: var(--sans); font-size: 12px; color: var(--grey);
  background: var(--bg); border: 1px solid var(--hairline); padding: 5px 11px;
  cursor: pointer; }}
button.f:hover {{ border-color: var(--grey-lt); color: var(--ink); }}
button.f[aria-pressed="true"] {{ background: var(--ink); border-color: var(--ink);
  color: #fff; }}
input.q {{ font-family: var(--sans); font-size: 12px; padding: 5px 9px;
  border: 1px solid var(--hairline); min-width: 190px; color: var(--ink); }}
input.q:focus {{ outline: none; border-color: var(--orange); }}
.grid {{ max-width: 1360px; margin: 20px auto 0; display: grid;
  grid-template-columns: repeat(auto-fill, minmax(430px, 1fr)); gap: 22px; }}
figure {{ margin: 0; border: 1px solid var(--hairline); background: var(--bg); }}
figure > button {{ display: block; width: 100%; padding: 0; border: 0;
  background: var(--bg); cursor: zoom-in; }}
figure img {{ display: block; width: 100%; height: auto; }}
figcaption {{ border-top: 1px solid var(--grid); padding: 9px 12px 10px;
  display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }}
.ct {{ font-family: ui-monospace, "SF Mono", Consolas, monospace; font-size: 11px;
  color: var(--orange-dk); }}
.fam {{ font-size: 11px; color: var(--grey-lt); text-align: right; }}
.warn {{ padding: 0 12px 10px; font-size: 11px; color: var(--grey-lt); }}
.none {{ max-width: 1360px; margin: 40px auto; color: var(--grey-lt);
  font-size: 13px; text-align: center; }}
footer {{ max-width: 1360px; margin: 40px auto 0; padding-top: 12px;
  border-top: 1px solid var(--grid); font-size: 11px; color: var(--grey-lt);
  display: flex; justify-content: space-between; gap: 16px; flex-wrap: wrap; }}
dialog {{ border: 0; padding: 0; max-width: 96vw; max-height: 94vh; background: var(--bg);
  box-shadow: 0 10px 44px rgba(0,0,0,.28); }}
dialog::backdrop {{ background: rgba(26,26,26,.72); }}
dialog img {{ display: block; max-width: 96vw; max-height: 86vh; width: auto; height: auto; }}
dialog .cap {{ display: flex; justify-content: space-between; gap: 16px;
  padding: 9px 13px; border-top: 1px solid var(--grid); font-size: 12px;
  color: var(--grey); }}
dialog button.x {{ font-family: var(--sans); font-size: 12px; background: var(--bg);
  border: 1px solid var(--hairline); padding: 3px 10px; cursor: pointer; }}
@media print {{
  body {{ padding: 0; }}
  .bar, dialog {{ display: none !important; }}
  .grid {{ grid-template-columns: repeat(2, 1fr); gap: 14px; }}
  figure {{ break-inside: avoid; }}
}}
"""

JS = """
const cards = Array.from(document.querySelectorAll('figure'));
const buttons = Array.from(document.querySelectorAll('button.f'));
const q = document.querySelector('input.q');
const empty = document.querySelector('.none');
let fam = 'all';

function apply() {
  const term = (q.value || '').trim().toLowerCase();
  let shown = 0;
  cards.forEach(c => {
    const okFam = fam === 'all' || c.dataset.family === fam;
    const okTerm = !term || c.dataset.search.includes(term);
    const vis = okFam && okTerm;
    c.style.display = vis ? '' : 'none';
    if (vis) shown++;
  });
  empty.style.display = shown ? 'none' : '';
}
buttons.forEach(b => b.addEventListener('click', () => {
  fam = b.dataset.family;
  buttons.forEach(o => o.setAttribute('aria-pressed', String(o === b)));
  apply();
}));
q.addEventListener('input', apply);

const dlg = document.querySelector('dialog');
const dimg = dlg.querySelector('img');
const dcap = dlg.querySelector('.cap span');
cards.forEach(c => c.querySelector('button').addEventListener('click', () => {
  dimg.src = c.querySelector('img').src;
  dcap.textContent = c.dataset.title;
  dlg.showModal();
}));
dlg.querySelector('button.x').addEventListener('click', () => dlg.close());
dlg.addEventListener('click', e => { if (e.target === dlg) dlg.close(); });
"""


def build_html(items: List[Dict[str, Any]], *, heading: str, subtitle: str,
               source: str, max_w: int) -> str:
    logo_b64 = ""
    lp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "assets", theme.LOGO_FILE)
    if os.path.exists(lp):
        with open(lp, "rb") as fh:
            logo_b64 = base64.b64encode(fh.read()).decode("ascii")

    cards, families, skipped = [], [], 0
    for it in items:
        b64 = _b64_png(it["png"], max_w)
        if b64 is None:
            skipped += 1
            continue
        fam = _family(it["chart_type"])
        if fam not in families:
            families.append(fam)
        search = " ".join([it["title"], it["chart_type"], it["ticker"], fam]).lower()
        warn = ""
        real = [w for w in it["warnings"] if "derive" not in w]
        if real:
            warn = (f'<div class="warn">{html.escape("; ".join(real[:2]))}</div>')
        cards.append(
            f'<figure data-family="{html.escape(fam)}" '
            f'data-search="{html.escape(search)}" '
            f'data-title="{html.escape(it["title"])}">'
            f'<button type="button" aria-label="Enlarge {html.escape(it["title"])}">'
            f'<img src="data:image/png;base64,{b64}" '
            f'alt="{html.escape(it["title"])}" loading="lazy"></button>'
            f'<figcaption><span class="ct">{html.escape(it["chart_type"] or "—")}</span>'
            f'<span class="fam">{html.escape(fam)}</span></figcaption>{warn}</figure>')

    filters = ['<button class="f" data-family="all" aria-pressed="true">All</button>']
    filters += [f'<button class="f" data-family="{html.escape(f)}">{html.escape(f)}</button>'
                for f in sorted(families)]

    mark = ""
    if logo_b64:
        mark = (f'<div class="mark"><img src="data:image/png;base64,{logo_b64}" alt="">'
                f'<span><span class="i">INSYNC</span> ANALYTICS</span></div>')
    else:
        mark = '<div class="mark"><span><span class="i">INSYNC</span> ANALYTICS</span></div>'

    t = theme.token_map()
    css = CSS.format(ink=t["ink"], orange=t["orange"], orange_dk=t["orange_dark"],
                     grey=t["grey"], grey_lt=t["grey_light"],
                     hairline=t["hairline"], grid=t["grid"], bg=t["background"])
    note = f" · {skipped} image(s) unreadable and omitted" if skipped else ""

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(heading)}</title>
<style>{css}</style></head><body>
<div class="masthead">
  <div><h1>{html.escape(heading)}</h1><p class="sub">{html.escape(subtitle)}</p></div>
  {mark}
</div>
<div class="rule"></div>
<div class="bar">
  <span class="stat"><b>{len(cards)}</b> exhibits</span>
  <span class="stat"><b>{len(families)}</b> families</span>
  {''.join(filters)}
  <input class="q" type="search" placeholder="Filter by name or ticker…"
         aria-label="Filter exhibits">
</div>
<div class="grid">{''.join(cards)}</div>
<p class="none" style="display:none">No exhibit matches that filter.</p>
<footer><span>Source: {html.escape(source)}{note}</span>
<span>Rendered by the InSync static-charts engine · one chart engine, one house style</span></footer>
<dialog><img alt=""><div class="cap"><span></span>
<button class="x" type="button">Close</button></div></dialog>
<script>{JS}</script></body></html>
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--manifest", help="render_chart.py manifest JSON, or '-' for stdin")
    src.add_argument("--dir", help="directory of already-rendered charts")
    ap.add_argument("--out", required=True, help="HTML file to write")
    ap.add_argument("--title", default="Chart gallery")
    ap.add_argument("--subtitle", default="")
    ap.add_argument("--source", default="Source not stated")
    ap.add_argument("--max-width", type=int, default=MAX_EMBED_WIDTH,
                    help=f"cap embedded image width in px (default {MAX_EMBED_WIDTH})")
    args = ap.parse_args(argv)

    items = (collect_from_manifest(args.manifest) if args.manifest
             else collect_from_dir(args.dir))
    if not items:
        print(json.dumps({"ok": False, "error": "no rendered PNG charts found"}),
              file=sys.stderr)
        return 1

    subtitle = args.subtitle or (
        f"{len(items)} exhibits · prepared {dt.date.today().isoformat()}")
    doc = build_html(items, heading=args.title, subtitle=subtitle,
                     source=args.source, max_w=args.max_width)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(doc)
    print(json.dumps({"ok": True, "out": os.path.abspath(args.out),
                      "exhibits": len(items),
                      "bytes": os.path.getsize(args.out)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
