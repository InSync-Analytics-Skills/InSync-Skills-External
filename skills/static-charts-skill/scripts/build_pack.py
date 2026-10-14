#!/usr/bin/env python3
"""Build a reproducible chart pack: images, a gallery, and a manifest.

    python build_pack.py --spec charts.json --out-root packs/
    python build_pack.py --verify packs/EXSW_3Q26_charts/manifest.json

Layout produced:

    EXSW_3Q26_charts/
      index.html              the gallery — what a human opens
      manifest.json           machine index + provenance + the spec of every chart
      png/EXSW_revenue_trend.png
      svg/EXSW_revenue_trend.svg

Three decisions are baked in, and each one is load-bearing:

* **The pack directory carries the ticker and the period.** Re-running after the
  next print writes a new pack instead of destroying the last one.
* **Filenames are stable.** No ordinal prefix, so inserting an exhibit never
  renames another one — the image links in a drafted document keep working and
  git shows one added file rather than a wall of renames. Exhibit order lives in
  the manifest and drives the gallery.
* **The manifest embeds every chart's spec.** That is what makes the pack
  reproducible rather than merely documented: `--verify` re-renders from the
  manifest alone and compares SHA-256 digests, so a pack can prove it matches
  the specs it claims to come from.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build_gallery
from insync_charts import (SpecError, __version__, load_specs,
                           registry, render_chart, render_figure)
from insync_charts import theme
from insync_charts.spec import SOURCE_NOT_STATED, VALID_FORMATS

SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def slug(text: str, fallback: str = "chart") -> str:
    s = SAFE.sub("_", str(text or "").strip()).strip("_")
    return s or fallback


def digest(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return "sha256:" + h.hexdigest()


def spec_digest(spec: Dict[str, Any]) -> str:
    canon = json.dumps(spec, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()


def family_of(chart_type: str) -> str:
    try:
        return registry.resolve(chart_type)["family"] or "Other"
    except KeyError:
        return "Other"


def pack_name(specs: List[Dict[str, Any]], explicit: Optional[str]) -> str:
    if explicit:
        return slug(explicit)
    first = specs[0] if specs else {}
    comp = first.get("company") or {}
    if isinstance(comp, str):
        comp = {"ticker": comp}
    ticker = slug(str(comp.get("ticker") or "charts")).upper()
    period = first.get("period") or first.get("fiscal_period")
    if not period:
        pers = first.get("periods") or []
        period = pers[-1] if pers else None
    tag = slug(period) if period else dt.date.today().isoformat()
    return f"{ticker}_{tag}_charts"


def _stem(sp: Dict[str, Any]) -> str:
    comp = sp.get("company") or {}
    if isinstance(comp, str):
        comp = {"ticker": comp}
    ticker = slug(str(comp.get("ticker") or "")).upper()
    ct = slug(sp.get("chart_type") or "chart")
    return f"{ticker}_{ct}" if ticker else ct


def assign_basenames(specs: List[Dict[str, Any]]) -> List[str]:
    explicit = [bool((sp.get("output") or {}).get("basename")) for sp in specs]
    stems = [slug((sp.get("output") or {})["basename"]) if ex else _stem(sp)
             for sp, ex in zip(specs, explicit)]
    counts: Dict[str, int] = {}
    for s in stems:
        counts[s] = counts.get(s, 0) + 1

    names: List[str] = []
    taken: set = set()
    for i, (sp, stem, ex) in enumerate(zip(specs, stems, explicit)):
        if ex or counts[stem] == 1:
            base = stem
        else:
            hint = slug(sp.get("title") or "").lower().strip("_")[:32].strip("_")
            base = f"{stem}__{hint}" if hint else f"{stem}__{i + 1}"
        candidate, n = base, 2
        while candidate in taken:
            candidate = f"{base}_{n}"
            n += 1
        taken.add(candidate)
        names.append(candidate)
    return names


def _fmt_date(value: Any) -> str:
    if not value:
        return ""
    try:
        return dt.date.fromisoformat(str(value)[:10]).strftime("%b %d, %Y")
    except ValueError:
        return str(value)


def _validation_page(exhibits: List[Dict[str, Any]], meta: Dict[str, Any],
                     preset: Dict[str, float]):
    import matplotlib.pyplot as plt
    from insync_charts import chrome as _chrome

    theme.set_scale(preset["w"])
    fig = plt.figure(figsize=(preset["w"], preset["h"]), dpi=preset["dpi"],
                     facecolor=theme.BG)
    rule_y = _chrome.masthead(
        fig, product="Data Validation",
        company=meta.get("company") or meta.get("ticker") or "",
        ticker=meta.get("ticker") or "", period=meta.get("period") or "",
        rows=[("Prepared", _fmt_date(meta.get("report_date"))),
              ("Data as of", _fmt_date(meta.get("as_of")))],
        y_top=0.965, height=0.175)

    L, R = theme.MARGIN["left"], theme.MARGIN["right"]
    fs = theme.FS_SOURCE * 1.06
    y = rule_y - 0.055

    incomplete = [e for e in exhibits
                  if not (e.get("validation") or {}).get("complete", True)]
    peers = [(e.get("validation") or {}).get("peer_count") for e in exhibits]
    peers = [p for p in peers if p]
    head = (f"{len(exhibits)} exhibits  ·  "
            f"{len(exhibits) - len(incomplete)} at 100% of expected history  ·  "
            f"{len(incomplete)} below 100%"
            + (f"  ·  peer set {max(peers)}" if peers else "")
            + f"  ·  data as of {meta.get('as_of') or 'n/a'}")
    fig.text(L, y, head, ha="left", va="top", color=theme.INK,
             fontfamily=theme.SANS, fontsize=theme.FS_SUBTITLE)
    y -= 0.062

    cols = [(L, "EXHIBIT", "left"), (L + 0.250, "SERIES", "left"),
            (L + 0.470, "HISTORY", "left"), (L + 0.578, "COMPLETE", "left"),
            (L + 0.672, "FIRST", "left"), (L + 0.760, "LAST", "left"),
            (R, "STATUS", "right")]
    for x, label, ha in cols:
        fig.text(x, y, label, ha=ha, va="top", color=theme.GREY_LT,
                 fontfamily=theme.SANS, fontsize=theme.FS_SOURCE * 0.95,
                 fontweight="bold")
    y -= 0.030
    fig.add_artist(plt.Line2D([L, R], [y + 0.008] * 2, transform=fig.transFigure,
                              color=theme.HAIRLINE, linewidth=0.8))

    step = 0.0335
    for e in exhibits:
        v = e.get("validation") or {}
        rows = v.get("series") or []
        for j, row in enumerate(rows):
            if y < 0.075:
                fig.text(L, y, "…further exhibits listed in manifest.json",
                         ha="left", va="top", color=theme.GREY_LT,
                         fontfamily=theme.SANS, fontsize=fs, style="italic")
                y -= step
                break
            pct = row.get("completeness_pct")
            full = pct is not None and pct >= 100.0
            if row.get("derived"):
                status = "derived" if full else f"derived, short {row.get('missing', 0)}"
            else:
                status = "complete" if full else f"short {row.get('missing', 0)}"
            colour = theme.GREY if full else theme.NEG
            cells = [
                (e["chart_type"] if j == 0 else "", theme.INK if j == 0 else theme.GREY),
                (row.get("series", ""), theme.GREY),
                (f"{row.get('reported')} of {row.get('expected')}", theme.GREY),
                (f"{pct:.0f}%" if pct is not None else "n/a", colour),
                (str(row.get("first_reported") or "n/a"), theme.GREY),
                (str(row.get("last_reported") or "n/a"), theme.GREY),
                (status, colour),
            ]
            for (x, _, ha), (txt, col) in zip(cols, cells):
                fig.text(x, y, txt, ha=ha, va="top", color=col,
                         fontfamily=theme.SANS, fontsize=fs)
            y -= step
        else:
            continue
        break

    notes = []
    if any((e.get("validation") or {}).get("finding_codes") for e in exhibits):
        codes = sorted({c for e in exhibits
                        for c in (e.get("validation") or {}).get("finding_codes", [])})
        notes.append("findings: " + ", ".join(codes))
    notes.append("'derived' series are computed by the engine from other series "
                 "on the same chart")
    fig.text(L, theme.SOURCE_Y + 0.030, "  |  ".join(notes), ha="left",
             va="bottom", color=theme.GREY_LT, fontfamily=theme.SANS,
             fontsize=theme.FS_SOURCE)
    fig.text(L, theme.SOURCE_Y, f"Source: {meta.get('source', '')}", ha="left",
             va="bottom", color=theme.GREY_LT, fontfamily=theme.SANS,
             fontsize=theme.FS_SOURCE)
    return fig


def build_pdf(prepared: List[Dict[str, Any]], exhibits: List[Dict[str, Any]],
              meta: Dict[str, Any], path: str,
              include_validation: bool = False) -> Optional[str]:
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    by_name = {e["name"]: e for e in exhibits}
    ordered = [sp for sp in prepared
               if sp.get("output", {}).get("basename") in by_name]
    if not ordered:
        return None

    p = theme.preset(ordered[0].get("output", {}).get("preset"))
    with PdfPages(path) as pdf:
        theme.set_scale(p["w"])
        cover = plt.figure(figsize=(p["w"], p["h"]), dpi=p["dpi"],
                           facecolor=theme.BG)
        L = theme.MARGIN["left"]
        from insync_charts import chrome as _chrome
        rows = [("Prepared", _fmt_date(meta.get("report_date")))]
        if meta.get("as_of"):
            rows.append(("Data as of", _fmt_date(meta["as_of"])))
        rule_y = _chrome.masthead(
            cover, product="Chart Pack",
            company=meta.get("company") or meta.get("ticker") or "",
            ticker=meta.get("ticker") or "", period=meta.get("period") or "",
            rows=rows, y_top=0.965, height=0.175)

        sub = "  ·  ".join([x for x in [meta.get("sector"),
                                        f"{len(ordered)} exhibits",
                                        f"Static Charts · {meta['engine']}"] if x])
        cover.text(L, rule_y - 0.045, sub, ha="left", va="top", color=theme.GREY,
                   fontfamily=theme.SANS, fontsize=theme.FS_SUBTITLE)
        y = rule_y - 0.115
        cover.text(L, y, "CONTENTS", ha="left", va="top", color=theme.GREY_LT,
                   fontfamily=theme.SANS, fontsize=theme.FS_SOURCE,
                   fontweight="bold")
        y -= 0.055
        for i, sp in enumerate(ordered, start=1):
            e = by_name[sp["output"]["basename"]]
            cover.text(L, y, f"{i}.", ha="left", va="top", color=theme.GREY_LT,
                       fontfamily=theme.SANS, fontsize=theme.FS_TICK)
            cover.text(L + 0.03, y, e["title"] or e["chart_type"], ha="left",
                       va="top", color=theme.INK, fontfamily=theme.SANS,
                       fontsize=theme.FS_TICK)
            cover.text(theme.MARGIN["right"], y, e["family"], ha="right",
                       va="top", color=theme.GREY_LT, fontfamily=theme.SANS,
                       fontsize=theme.FS_SOURCE)
            y -= 0.048
        foot = (f"Source: {meta['source']}"
                + (f"  |  data as of {meta['as_of']}" if meta.get("as_of") else ""))
        cover.text(L, theme.SOURCE_Y, foot, ha="left", va="bottom",
                   color=theme.GREY_LT, fontfamily=theme.SANS,
                   fontsize=theme.FS_SOURCE)
        pdf.savefig(cover, facecolor=theme.BG)
        plt.close(cover)

        for sp in ordered:
            fig, _s, _m = render_figure(sp)
            pdf.savefig(fig, facecolor=theme.BG)
            plt.close(fig)

        if include_validation:
            vp = _validation_page(exhibits, meta, p)
            if vp is not None:
                pdf.savefig(vp, facecolor=theme.BG)
                plt.close(vp)

        d = pdf.infodict()
        d["Title"] = meta["title"]
        d["Author"] = "InSync Analytics"
        d["Subject"] = f"Chart pack — {meta.get('ticker') or ''}".strip()
        d["Creator"] = meta["engine"]
        d["Producer"] = meta["engine"]
        d["CreationDate"] = dt.datetime(2016, 1, 1)
        d["ModDate"] = dt.datetime(2016, 1, 1)
    return path


def build(specs: List[Dict[str, Any]], out_root: str, *,
          name: Optional[str] = None, formats: Optional[List[str]] = None,
          preset: Optional[str] = None, title: Optional[str] = None,
          gallery: bool = True, pdf: bool = True,
          validation: bool = False) -> Dict[str, Any]:
    if not specs:
        raise SpecError("no chart specs supplied")

    pack = pack_name(specs, name)
    root = os.path.join(out_root, pack)
    os.makedirs(root, exist_ok=True)
    clear_previous(root)

    bases = assign_basenames(specs)
    prepared: List[Dict[str, Any]] = []
    for sp, base in zip(specs, bases):
        sp = json.loads(json.dumps(sp))
        out = dict(sp.get("output") or {})
        out["basename"] = base
        out["formats"] = formats or out.get("formats") or ["png", "svg"]
        out["format_subdirs"] = True
        if preset:
            out["preset"] = preset
        sp["output"] = out
        prepared.append(sp)

    exhibits: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    for order, sp in enumerate(prepared, start=1):
        res = render_chart(sp, root, strict=False)
        if not res.get("ok"):
            failures.append({"order": order, "chart_type": sp.get("chart_type"),
                             "error": res.get("error")})
            continue
        rel = {ext: os.path.relpath(p, root).replace(os.sep, "/")
               for ext, p in res["files"].items()}
        exhibits.append({
            "order": order,
            "name": sp["output"]["basename"],
            "chart_type": res["chart_type"],
            "title": res.get("title") or "",
            "family": family_of(res["chart_type"]),
            "ticker": res.get("ticker") or "",
            "files": rel,
            "digests": {ext: digest(p) for ext, p in res["files"].items()},
            "spec_sha256": spec_digest(sp),
            "warnings": res.get("warnings") or [],
            "observation": res.get("observation") or "",
            "data_quality": res.get("data_quality") or "info",
            "findings": res.get("findings") or [],
            "plot_fraction": res.get("plot_fraction"),
            "validation": res.get("validation") or {},
            "spec": sp,
        })

    first = specs[0]
    comp = first.get("company") or {}
    if isinstance(comp, str):
        comp = {"ticker": comp}
    heading = title or (f"{comp.get('name') or comp.get('ticker') or 'Chart pack'}"
                        f" — chart pack")
    manifest = {
        "pack": {
            "name": pack,
            "title": heading,
            "ticker": str(comp.get("ticker") or ""),
            "company": str(comp.get("name") or ""),
            "sector": str(comp.get("sector") or ""),
            "period": str(first.get("period") or ""),
            "source": str(first.get("source") or "").strip() or SOURCE_NOT_STATED,
            "as_of": first.get("as_of"),
            "report_date": first.get("report_date") or dt.date.today().isoformat(),
            "generated_utc": dt.datetime.now(dt.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"),
            "engine": f"insync-charts {__version__}",
            "layout": "format_subdirs",
            "exhibit_count": len(exhibits),
            "failed_count": len(failures),
            "data_quality": (
                "error" if any(e["data_quality"] == "error" for e in exhibits)
                else "warn" if any(e["data_quality"] == "warn" for e in exhibits)
                else "info"),
            "exhibits_with_findings": sum(1 for e in exhibits if e["findings"]),
            "exhibits_complete": sum(
                1 for e in exhibits
                if (e.get("validation") or {}).get("complete")),
            "exhibits_incomplete": sum(
                1 for e in exhibits
                if not (e.get("validation") or {}).get("complete", True)),
            "rebuild": f"python build_pack.py --verify {pack}/manifest.json",
        },
        "exhibits": exhibits,
        "failures": failures,
    }
    mpath = os.path.join(root, "manifest.json")
    with open(mpath, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")

    pdf_path = None
    if pdf and exhibits:
        try:
            pdf_path = build_pdf(prepared, exhibits, manifest["pack"],
                                 os.path.join(root, f"{pack}.pdf"),
                                 include_validation=validation)
        except Exception as e:
            manifest["pack"]["pdf_error"] = f"{type(e).__name__}: {e}"
    if pdf_path:
        manifest["pack"]["pdf"] = os.path.basename(pdf_path)
    if pdf and exhibits:
        with open(mpath, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
            fh.write("\n")

    index = None
    if gallery and exhibits:
        items = [{"chart_type": e["chart_type"], "title": e["title"] or e["name"],
                  "ticker": e["ticker"],
                  "png": os.path.join(root, e["files"].get("png", "")),
                  "svg": None, "warnings": e["warnings"]}
                 for e in exhibits if e["files"].get("png")]
        if items:
            sub = (f"{len(items)} exhibits · {manifest['pack']['company'] or pack}"
                   f" · prepared {manifest['pack']['report_date']}")
            doc = build_gallery.build_html(
                items, heading=heading, subtitle=sub,
                source=manifest["pack"]["source"],
                max_w=build_gallery.MAX_EMBED_WIDTH)
            index = os.path.join(root, "index.html")
            with open(index, "w", encoding="utf-8") as fh:
                fh.write(doc)

    return {"ok": not failures, "pack": pack, "root": os.path.abspath(root),
            "manifest": mpath, "index": index, "pdf": pdf_path,
            "exhibits": len(exhibits), "failed": len(failures),
            "failures": failures}


def _listed_files(man: Dict[str, Any]) -> List[str]:
    out = [rel for e in man.get("exhibits", []) for rel in (e.get("files") or {}).values()]
    pdf_name = (man.get("pack") or {}).get("pdf")
    if pdf_name:
        out.append(pdf_name)
    return out + ["index.html", "manifest.json"]


def _inside(root: str, rel: str) -> Optional[str]:
    full = os.path.realpath(os.path.join(root, rel))
    base = os.path.realpath(root)
    try:
        inside = os.path.commonpath([full, base]) == base and full != base
    except ValueError:
        inside = False
    return full if inside else None


def clear_previous(root: str) -> List[str]:
    mpath = os.path.join(root, "manifest.json")
    if not os.path.isfile(mpath):
        return []
    try:
        with open(mpath, "r", encoding="utf-8") as fh:
            old = json.load(fh)
    except (OSError, ValueError):
        return []
    removed = []
    for rel in _listed_files(old):
        full = _inside(root, rel)
        if full and os.path.isfile(full):
            os.remove(full)
            removed.append(rel)
    return removed


def unlisted_files(root: str, man: Dict[str, Any]) -> List[str]:
    listed = {os.path.normcase(os.path.normpath(r)) for r in _listed_files(man)}
    extra = []
    for ext in sorted(VALID_FORMATS):
        d = os.path.join(root, ext)
        if not os.path.isdir(d):
            continue
        for dirpath, _dirs, names in os.walk(d):
            for n in sorted(names):
                rel = os.path.relpath(os.path.join(dirpath, n), root)
                if os.path.normcase(os.path.normpath(rel)) not in listed:
                    extra.append(rel.replace(os.sep, "/"))
    return extra


def verify(manifest_path: str) -> Dict[str, Any]:
    with open(manifest_path, "r", encoding="utf-8") as fh:
        man = json.load(fh)
    root = os.path.dirname(os.path.abspath(manifest_path))
    tmp = tempfile.mkdtemp(prefix="insync_pack_verify_")
    checked = matched = 0
    problems: List[Dict[str, Any]] = []
    try:
        for e in man.get("exhibits", []):
            spec = e.get("spec")
            if not spec:
                problems.append({"name": e.get("name"), "issue": "no spec embedded"})
                continue
            if spec_digest(spec) != e.get("spec_sha256"):
                problems.append({"name": e.get("name"),
                                 "issue": "embedded spec does not match its own digest"})
            res = render_chart(spec, tmp, strict=False)
            if not res.get("ok"):
                problems.append({"name": e.get("name"),
                                 "issue": f"re-render failed: {res.get('error')}"})
                continue
            for ext, rel in (e.get("files") or {}).items():
                on_disk = os.path.join(root, rel)
                fresh = res["files"].get(ext)
                if not os.path.exists(on_disk):
                    problems.append({"name": e["name"], "issue": f"missing file {rel}"})
                    continue
                if not fresh:
                    continue
                checked += 1
                if digest(on_disk) == digest(fresh):
                    matched += 1
                else:
                    problems.append({"name": e["name"],
                                     "issue": f"{ext} differs from a fresh render"})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for rel in unlisted_files(root, man):
        problems.append({"name": rel, "issue": "file in the pack but not in the manifest"})
    return {"ok": not problems, "pack": man.get("pack", {}).get("name"),
            "files_checked": checked, "files_identical": matched,
            "problems": problems}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", help="chart spec file (single, list, or {'charts':[...]})")
    ap.add_argument("--out-root", default=".", help="where the pack directory is created")
    ap.add_argument("--name", help="override the pack directory name")
    ap.add_argument("--title", help="override the gallery / manifest title")
    ap.add_argument("--format", help="override output formats, e.g. png,svg")
    ap.add_argument("--preset", help="override the size preset for every exhibit")
    ap.add_argument("--no-gallery", action="store_true", help="skip index.html")
    ap.add_argument("--no-pdf", action="store_true", help="skip the chart-pack PDF")
    ap.add_argument("--validation", action="store_true",
                    help="append the internal Data Validation page. OFF by default: "
                         "it is a QA artefact, not client collateral.")
    ap.add_argument("--verify", metavar="MANIFEST",
                    help="re-render a pack from its manifest and compare digests")
    args = ap.parse_args(argv)

    if args.verify:
        out = verify(args.verify)
        print(json.dumps(out, indent=2))
        return 0 if out["ok"] else 1

    if not args.spec:
        ap.error("--spec is required (or use --verify)")

    try:
        specs = load_specs(args.spec)
        fmts = ([f.strip().lstrip(".") for f in args.format.split(",") if f.strip()]
                if args.format else None)
        out = build(specs, args.out_root, name=args.name, formats=fmts,
                    preset=args.preset, title=args.title,
                    gallery=not args.no_gallery, pdf=not args.no_pdf,
                    validation=args.validation)
    except (SpecError, OSError, json.JSONDecodeError) as e:
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}))
        return 2
    print(json.dumps(out, indent=2))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
