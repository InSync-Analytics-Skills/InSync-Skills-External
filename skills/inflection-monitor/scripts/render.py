#!/usr/bin/env python3
"""
inflection-monitor renderer.

Turns computed.json (every figure) plus narrative.json (prose with no digits)
into the branded HTML dashboard and the PDF note.

The narrative contract is the safety mechanism: the model's prose may not contain
a digit, and any figure it wants to cite must arrive as a {{placeholder}} token
that this script resolves from computed.json. A number the engine did not compute
therefore cannot appear on the page at all - not as a matter of discipline, but
because there is no path for it.

Usage
-----
  python render.py computed.json --narrative narrative.json --out-dir ./out
  python render.py computed.json --out-dir ./out          # skeleton, no prose
  python render.py --narrative-template computed.json     # print the prose contract
"""

from __future__ import annotations

import argparse
import html
import json
import os
import math
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from compute import ticker_slug, fmt_number
from binding import required_binding_words

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(os.path.dirname(HERE), "assets")

BIN_COLOR = {
    "vneg": "#8C5A1E", "neg": "#BE8B4E", "sneg": "#E4CFAE",
    "flat": "#F1F1F1",
    "spos": "#C6D8E8", "pos": "#6E9BC2", "vpos": "#2E5F8A",
    "na": "#FFFFFF",
}
BIN_LABEL = {
    "vneg": "sharp deceleration", "neg": "deceleration", "sneg": "mild deceleration",
    "flat": "no material change", "spos": "mild acceleration",
    "pos": "acceleration", "vpos": "sharp acceleration", "na": "not available",
}

NARRATIVE_KEYS = ["setup", "dd1", "dd2", "dd3", "divergence", "debate", "guidance"]

CSS = """
:root{--orange:#E8850C;--black:#1A1A1A;--body:#222222;--muted:#666666;
      --hair:#DDDDDD;--fill:#F7F7F7;--bg:#FFFFFF;
      --pos:#22703F;--neg:#B3311F;
      --sans:"Liberation Sans",Arial,Helvetica,sans-serif;
      --serif:"Liberation Serif","Times New Roman",Georgia,serif;
      --mono:"DejaVu Sans Mono",Consolas,monospace;}
*{box-sizing:border-box;}
body{margin:0;background:#e9e9e9;font-family:var(--sans);color:var(--body);}
.page{max-width:794px;margin:24px auto;background:var(--bg);padding:34px 40px 38px;
      box-shadow:0 1px 6px rgba(0,0,0,.15);}

/* ---- Masthead: the house header, brand left, product right, in a bordered box ---- */
.mast{border:1px solid var(--hair);padding:11px 15px;display:flex;
      align-items:flex-start;justify-content:space-between;gap:18px;}
.mast .brandline{display:flex;align-items:center;gap:8px;}
.mast .brandline img{height:26px;width:auto;}
.mast .wm{font-size:19px;line-height:1;}
.mast .wm b{color:var(--orange);font-weight:bold;}
.mast .wm span{color:var(--black);}
.mast .co{font-family:var(--serif);font-size:19px;font-weight:bold;color:var(--black);
          margin-top:4px;line-height:1.1;}
.mast .co em{font-style:normal;color:var(--orange);}
.mast .right{text-align:right;white-space:nowrap;}
.mast .prod{font-family:var(--serif);font-size:18px;font-weight:bold;color:var(--black);
            line-height:1.12;}
/* TWO META ROWS, in the house label-and-value form. The header carried five -
   Prepared, Prepared by, Reporting currency, Latest reported, Scan period - and
   three of them belong elsewhere in the note:
     the BYLINE is colophon and sits in the footer, beside the source line;
     the SCAN PERIOD is already stated in Methodology, under Window;
     the REPORTING CURRENCY is stated in Methodology too, which is where a
       research note states it as a matter of standard practice.
   That last one is not a deletion. It is the one unit on the page the reader
   has to supply from outside it, and leaving it unstated ANYWHERE is the v4.34
   defect - a Japanese automaker's yen read as dollars, wrong by about 150x, with sixty gates
   green. It moved; it did not go. */
.dvg .brg{font-size:9.5px;color:var(--ink,#222);margin-top:3px;}
.mast .prodsub{font-size:9.5px;font-style:italic;color:var(--muted);margin:1px 0 4px;}
.mast .meta{font-size:9.5px;font-weight:bold;color:var(--black);margin-top:3px;}
.mast .meta span{font-weight:normal;color:var(--muted);}
/* v4.47 header fields: label and value, two columns, left-aligned. */
/* v4.48.1: one column of five fields, each fact once. */
.mast .mgrid{display:grid;grid-template-columns:auto;row-gap:2px;
             text-align:left;white-space:nowrap;align-self:center;}
.mast .mf{font-size:9.2px;color:var(--muted);line-height:1.3;}
.mast .mf .k{font-weight:bold;color:var(--black);}
.mast .prodsub{font-style:normal;font-weight:bold;color:var(--black);}
.takeaways .tsub{font-size:9px;font-weight:bold;text-transform:uppercase;letter-spacing:.04em;
                 color:var(--orange);margin:4px 0 1px;}
.takeaways .debate{font-size:9.8px;margin:1px 0 2px 14px;}
.takeaways ul.pp li{font-size:9.6px;margin:0 0 2px;line-height:1.35;}
.takeaways .tsub + ul li{margin-bottom:3px;}
.purpose{font-size:8.4px;color:#666;margin:4px 0 6px;}
.mastrule{height:2px;background:var(--orange);margin:0;}

.scope{font-family:var(--serif);font-size:10px;color:#2E2E2E;
       margin:10px 0 0;line-height:1.45;}
.scope b{font-weight:700;}
.scope .sep{color:#9A9A9A;padding:0 3px;}
/* THE CAVEATS, AS METADATA RATHER THAN PROSE. They were one italic block with
   the headline counts, and on a bank with four unresolved units and two coarse
   ratios it ran to a hundred and eighty words before the reader reached a
   finding. Same facts, smaller type, one clause each. */
.scopenote{font-family:var(--serif);font-style:italic;font-size:8.6px;color:#6B6B6B;
       margin:4px 0 0;padding-bottom:9px;border-bottom:1px solid var(--hair);
       line-height:1.45;}

/* ---- Sections: orange label, orange rule, bold headline ---- */
.label{font-size:10px;font-weight:bold;letter-spacing:1.2px;text-transform:uppercase;
       color:var(--orange);margin-top:17px;
       /* A heading never breaks from what it introduces. Delta shipped
          "Provenance And Method" alone at the foot of page three with its table
          on page four, which a reader reads as a section with nothing in it -
          reported three times as content sitting after the closing bullets.
          break-after and page-break-after are both set because the PDF engine
          may be weasyprint, chromium or wkhtmltopdf and they do not agree on
          which property they honour. orphans/widows keep a paragraph from
          leaving one line behind. */
       break-after:avoid; page-break-after:avoid; break-inside:avoid;}
.rule{height:2px;background:var(--orange);margin:3px 0 10px;
      break-after:avoid; page-break-after:avoid;}
/* The wrapper is what wkhtmltopdf actually honours. */
.sect{break-inside:avoid; page-break-inside:avoid;}
/* The source description, under the name it belongs to. */
/* RELATIVE, not absolute. At 8.4px it rendered LARGER than the 7.2px
   provenance cell that contains it, so "Total Revenue" looked like the heading
   and "Revenue" like the footnote - the reverse of the intent. */
.pbasis{font-size:0.9em;color:var(--grey);margin-top:1px;}
/* A table keeps its own header with its first rows. */
thead{break-inside:avoid; page-break-inside:avoid; break-after:avoid;
      page-break-after:avoid;}
tr{break-inside:avoid; page-break-inside:avoid;}
p, li{orphans:2; widows:2;}
h2.sec{font-size:14px;font-weight:bold;color:var(--black);margin:2px 0 6px;}
p.prose{font-family:var(--serif);font-size:11.2px;line-height:1.5;margin:0 0 9px;}
.blt{font-family:var(--serif);font-size:11.2px;line-height:1.45;margin:2px 0 9px;
padding-left:14px;list-style:none;}
.blt li{position:relative;margin:0 0 3px;}
.blt li:before{content:"";position:absolute;left:-10px;top:5.5px;width:3px;height:3px;
background:var(--orange);}
.stand{font-family:var(--serif);font-size:11.5px;color:#333;
       margin:13px 0 4px;line-height:1.52;}

/* ---- Stat tiles ---- */
.strip{display:flex;gap:10px;margin:12px 0 4px;}
.tile{flex:1;border:1px solid var(--hair);padding:9px 11px;}
.tile .k{font-size:8.5px;letter-spacing:.9px;text-transform:uppercase;color:var(--muted);}
.tile .v{font-family:var(--mono);font-size:17px;font-weight:bold;color:var(--black);
         margin-top:3px;}
.tile .v.pos{color:var(--pos);} .tile .v.neg{color:var(--neg);}
.tile .s{font-size:8.5px;color:var(--muted);margin-top:1px;line-height:1.3;}

/* ---- Takeaways ---- */
.takeaways{border:1px solid var(--hair);padding:11px 14px;margin:12px 0 4px;}
.takeaways .tt{font-size:9px;font-weight:bold;letter-spacing:1.1px;text-transform:uppercase;
               color:var(--orange);margin-bottom:7px;}
.takeaways ol{margin:0;padding-left:17px;}
.takeaways li{margin:0 0 5px;font-size:10.4px;line-height:1.45;}
.takeaways li::marker{color:var(--orange);font-weight:bold;}
.takeaways li b{color:var(--black);}

/* ---- Tables ---- */
table{width:100%;border-collapse:collapse;margin:8px 0 4px;}
thead th{font-size:7.6px;font-weight:bold;letter-spacing:.2px;text-transform:uppercase;
         color:var(--muted);text-align:right;padding:3px 5px;line-height:1.15;
         border-bottom:1.5px solid var(--hair);}
thead th:first-child{text-align:left;}
/* EVERY CELL IN A ROW ALIGNS TO THE ROW'S FIRST LINE.
   Cells default to vertical-align:middle, so a metric cell that wraps onto a
   second line - a long name plus two markers - centres itself against the
   one-line numeric cells. The result on the page is the marker appearing BELOW
   the row's figures, detached from the name it belongs to: "Consolidated
   Passenger Load Factor PRIMARY", then the numbers, then "BASE-SENSITIVE"
   floating underneath with nothing to attach it to. Raised in review three
   times as "orphaned metadata labels", and it was never a content or ordering
   problem - it was this one property. Top alignment keeps a wrapped marker
   directly under its own name. */
tbody td{font-size:9.6px;padding:5px 6px;border-bottom:1px solid var(--hair);text-align:right;
         vertical-align:top;}
tbody td:first-child{text-align:left;}
tbody tr:nth-child(even){background:var(--fill);}
td.name{max-width:230px;}
.num{font-family:var(--mono);}
.pos{color:var(--pos);font-weight:bold;} .neg{color:var(--neg);font-weight:bold;}
.tag{display:inline-block;font-size:7.4px;letter-spacing:.4px;text-transform:uppercase;
     border:1px solid var(--hair);border-radius:2px;padding:0 3px;color:var(--muted);
     margin-left:4px;vertical-align:1px;}

/* ---- Client reference page ----
   The orange identity, used as a rule and a heading colour rather than as fill:
   this is the page a reader consults rather than reads, so it wants white space
   and one accent, not decoration. */
/* The paired table has TWO metric columns, and only the first cell of a row is
   left-aligned by the base rule - so the second pair's names sat hard against
   the middle of the page and short ones ("Revenue") read as a different column
   from long ones. Alignment here follows the KIND of cell, not its position. */
table.keysrc td.name{max-width:150px;} table.keysrc th, table.keysrc td{width:25%;}
table.keysrc td.name, table.keysrc th:nth-child(3){text-align:left;}
.sublab{font-size:9.4px;font-weight:bold;letter-spacing:.4px;text-transform:uppercase;
        color:var(--orange);margin:14px 0 5px;}
.mbox{border:1px solid var(--hair);border-left:3px solid var(--orange);
      padding:9px 13px 4px;margin:15px 0 10px;background:var(--fill);}
.mbox .mh{font-size:9.4px;font-weight:bold;letter-spacing:.4px;text-transform:uppercase;
          color:var(--orange);margin:0 0 6px;}
.mbox p{font-size:8.6px;line-height:1.5;color:var(--black);margin:0 0 5px;}

/* ---- Heatmap ---- */
.hm td.cell{width:36px;text-align:center;font-family:var(--mono);font-size:8.4px;white-space:nowrap;
            border:1px solid #fff;color:var(--black);}
.legend{font-size:8.2px;color:var(--muted);margin-top:4px;}
.legend span{display:inline-block;width:11px;height:9px;margin:0 3px 0 0;
             vertical-align:-1px;border:1px solid #ccc;}
.legend i{font-style:normal;white-space:nowrap;margin-right:11px;display:inline-block;}

/* ---- Deep dives ---- */
.dive{border-left:3px solid var(--orange);padding:2px 0 2px 12px;margin:12px 0;
      page-break-inside:avoid;break-inside:avoid;}
/* Title row: metric on the left, the call on the right. The implication was
   the sixth of seven middot-separated facts in a monospace line, at the same
   weight as the streak count; it is the column this product exists to fill in.
   Floated rather than flexed, because the PDF engine is not a variable worth
   introducing for a two-item row. */
.dive .dh{font-size:11.5px;font-weight:bold;color:var(--black);
          overflow:hidden;line-height:1.35;}
.dive .dh .nm{float:left;max-width:74%;}
.dive .dh .rt{float:right;text-align:right;white-space:nowrap;padding-left:8px;}
.vd{font-family:var(--sans);font-size:8.4px;font-weight:bold;letter-spacing:.05em;
    text-transform:uppercase;padding:1px 4px;border:0.7px solid;margin-left:5px;}
.vd.pos{color:var(--pos);border-color:var(--pos);}
.vd.neg{color:var(--neg);border-color:var(--neg);}
.vd.neu{color:var(--muted);border-color:var(--hair);}
.cf{font-size:8.2px;font-weight:normal;color:var(--muted);text-transform:uppercase;
    letter-spacing:.05em;margin-left:5px;}
/* Measurement strip: labelled facts in the body font, change in pace emphasised
   because that is the quantity the whole product is about. */
.dive .dm{font-family:var(--sans);font-size:9.2px;color:#444;
          margin:3px 0 6px;line-height:1.5;}
.dive .dm b{color:var(--black);}
.dive .dm .k{color:var(--muted);text-transform:uppercase;letter-spacing:.05em;
             font-size:8px;padding-right:3px;font-weight:normal;}
.dive .dm .sep{color:var(--hair);padding:0 6px;font-weight:normal;}
/* A thin rule, not a filled panel. Three shaded boxes down one page read as
   callouts competing with the prose; the forward test is a footnote to the
   block it sits in. */
.fwd{font-size:9px;color:#444;border-left:2px solid var(--orange);
     padding:0 0 0 7px;margin:6px 0 2px;line-height:1.45;}
.fwd .k{color:var(--muted);text-transform:uppercase;letter-spacing:.05em;
        font-size:8.2px;padding-right:3px;}
.quote{border-left:2px solid var(--hair);padding:4px 0 4px 11px;margin:8px 0 2px;}
.quote p{font-family:var(--serif);font-style:italic;font-size:10.5px;margin:0;color:#2C2C2C;}
.quote .attr{font-size:8px;font-weight:bold;letter-spacing:.4px;text-transform:uppercase;
             color:var(--muted);margin-top:4px;}

.note{font-size:8.4px;color:var(--muted);line-height:1.45;margin:5px 0 0;}
.note b{color:var(--black);}
.partial{border:1px solid var(--orange);background:#FDF6EC;padding:7px 11px;
         margin:11px 0 2px;font-size:9.4px;color:#6B3E05;line-height:1.45;}
.partial b{color:#8A4E02;letter-spacing:.3px;text-transform:uppercase;font-size:9px;}
.guard{border:1px solid var(--neg);background:#FDF3F1;padding:9px 11px;margin:12px 0;
       font-size:10px;color:#7A2216;}
.footer{margin-top:26px;padding-top:10px;border-top:1px solid var(--hair);
        font-size:8px;color:var(--muted);line-height:1.55;}
.stk{padding-right:5px;}
.spark{vertical-align:middle;}
/* No forced page breaks. Fixed breaks guarantee a section starts on a fresh page
   and guarantee the previous page ends in white space, which is the wrong trade
   for a four-page monitor: the reader wants density and section order, not a
   title page per section. Blocks are kept from splitting instead. */
.brk{height:0;}
.tier{display:inline-block;font-size:6.6px;font-weight:bold;color:#8A8A8A;
      margin-left:5px;vertical-align:1px;letter-spacing:.3px;text-transform:uppercase;}
.impf{color:#1F6B3B;font-weight:bold;} .impu{color:#A8291A;font-weight:bold;}
.impm{color:#8C5A1E;font-weight:bold;} .impa{color:#666;}
.attrib{font-size:8.6px;color:#444;margin:5px 0 0;line-height:1.4;}
/* Subordinate to a dive title: a divergence is a finding ABOUT a pair, not a
   metric heading, and it was rendering at the same weight. */
.dvg{border-left:2px solid var(--hair);padding:1px 0 1px 10px;margin:8px 0;
     page-break-inside:avoid;break-inside:avoid;}
.dvg .hl{font-size:10px;font-weight:bold;color:var(--black);line-height:1.35;}
.dvg .legs{font-size:9.2px;color:#444;margin-top:2px;line-height:1.5;}
.dvg .legs .m{color:var(--black);}
.dvg .legs .sep{color:var(--hair);padding:0 6px;}
.dvg .hl{font-size:10px;font-weight:bold;color:var(--black);line-height:1.35;}
.dvg .legs{font-family:var(--sans);font-size:9.1px;color:#444;margin-top:2px;
           line-height:1.5;}
.dvg .legs .m{color:var(--black);}
.dvg .legs .sep{color:var(--hair);padding:0 7px;}
.attrib b{color:var(--black);}
td.src{font-size:7px;color:var(--muted);max-width:215px;line-height:1.25;}
table.prov tbody td{font-size:7.2px;padding:2.6px 5px;}
table.prov thead th{font-size:7.6px;padding:4px 5px;}
.takeaways ul{margin:0;padding-left:17px;}
/* ---- The quarter strip: four facts before the inflections ---- */
table.qs{width:100%;border-collapse:collapse;margin:10px 0 2px;table-layout:fixed;}
table.qs td{border:1px solid var(--hair);padding:6px 8px;vertical-align:top;text-align:left !important;}
.qs-l{font-size:7.6px;letter-spacing:.6px;text-transform:uppercase;color:var(--muted);}
.qs-v{font-size:13px;font-weight:bold;color:var(--black);margin:2px 0 1px;}
.qs-g{font-size:8.6px;color:var(--body);}
.qs-x{font-size:8px;color:var(--muted);margin-top:1px;}
.sec{font-size:9px;color:var(--muted);margin-top:2px;letter-spacing:.3px;}
sup.mk{font-size:8px;color:var(--orange);margin-left:1px;}
sup.cm{color:var(--muted);}
.mf{font-size:7.8px;color:var(--muted);}
.takeaways ul li{margin:0 0 5px;font-size:10.4px;line-height:1.45;list-style:disc;}
.takeaways ul li::marker{color:var(--orange);}
@media print{
  body{background:#fff;}
  .page{box-shadow:none;margin:0;max-width:none;padding:0;}
  .label,h2.sec{page-break-after:avoid;}
  table,.dive,.strip,.takeaways{page-break-inside:avoid;}
}
@page{size:Letter;margin:0.5in;}
"""



DIGIT = re.compile(r"\d")
_NUM_WORD = (r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
             r"fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|"
             r"sixty|seventy|eighty|ninety|hundred|thousand|million|billion|trillion|dozen|half)")
_NUM_UNIT = (r"(?:per\s*cent|percent|percentage[\s-]+points?|basis[\s-]+points?|points|bps|pp|"
             r"billion|million|thousand|trillion|times|fold|dollars?|cents?|euros?|pounds?|yen)")
WORD_FIGURE = re.compile(
    r"\b" + _NUM_WORD + r"(?:[\s-]+(?:and[\s-]+)?(?:a[\s-]+)?" + _NUM_WORD + r")*[\s-]+"
    + _NUM_UNIT + r"\b"
    r"|\bhalf[\s-]+an?[\s-]+(?:billion|million|thousand|trillion|percent|"
    r"percentage[\s-]+point|basis[\s-]+point)\b"
    r"|\b(?:doubled|tripled|quadrupled|halved|(?:two|three|four|five|ten)fold)\b(?![\s-]+down\b)"
    r"|\b(?:a|one|two)[\s-]+(?:half|third|quarter|fifth)\s+(?:above|below|higher|lower|more|less)\b"
    r"|\b(?:low|mid|high|upper)[\s-]+(?:single|double)[\s-]+digits?\b"
    r"|\b(?:low|mid|high|upper)[\s-]+(?:teens|twenties|thirties|forties|fifties|sixties|"
    r"seventies|eighties|nineties)\b"
    r"|\b(?:single|double|triple)[\s-]+digits?\b", re.I)
TOKEN = re.compile(r"\{\{([a-zA-Z0-9_.]+)\}\}")


def resolve_narrative(narr: dict, placeholders: dict):
    out, problems = {}, []
    for k in sorted(narr or {}):
        if k.startswith("_") or k in NARRATIVE_KEYS:
            continue
        if k == "watch":
            problems.append("watch is retired: the What To Monitor Next table carries the "
                            "questions, and a second list repeated them")
        else:
            problems.append("%s: not a narrative section (the sections are %s)"
                            % (k, ", ".join(NARRATIVE_KEYS)))

    def one(key, text, label):
        unknown = [t for t in TOKEN.findall(text) if t not in placeholders]
        if unknown:
            problems.append(f"{label}: unknown placeholder(s) "
                            f"{', '.join(sorted(set(unknown)))}")
        stripped = TOKEN.sub("", text)
        if DIGIT.search(stripped):
            bad = sorted(set(DIGIT.findall(stripped)))
            problems.append(
                f"{label}: prose contains literal digit(s) {''.join(bad)}. Every figure "
                f"must arrive as a {{{{placeholder}}}} so the engine owns the number.")
        _wf = WORD_FIGURE.search(stripped)
        if _wf:
            problems.append(
                f"{label}: prose states a figure in words ({_wf.group(0)!r}). A figure written "
                f"out is still a figure: use a {{{{placeholder}}}}, or describe the move "
                f"without a quantity.")
        return TOKEN.sub(lambda m: str(placeholders.get(m.group(1), m.group(0))), text)

    for key in NARRATIVE_KEYS:
        v = narr.get(key)
        if isinstance(v, (list, tuple)):
            items = [str(b).strip() for b in v if str(b).strip()]
            out[key] = [one(key, b, f"{key}[{i}]") for i, b in enumerate(items)] or ""
            continue
        text = (v or "").strip()
        out[key] = one(key, text, key) if text else ""
    return out, problems


def narrative_template(payload):
    ph = payload["placeholders"]
    guide = {
        "_contract": (
            "Prose only. No digits and no figures in words (sixty percent, doubled, high "
            "teens) anywhere: write every figure as a {{token}} from "
            "_available_placeholders and the renderer substitutes the engine's value. "
            "Describe what changed and what it implies; never restate a whole table."
        ),
        "_shape": (
            "This is a monitor, not a report, and the caps are ENFORCED - render.py "
            "refuses prose that exceeds them. setup, dd1-dd3 and divergence: at most "
            "45 words each, which is two sentences. guidance: a JSON LIST of at most 4 "
            "bullets, at most 22 words per bullet. debate: one question of at most 30 "
            "words. There is no watch section and no dd4 or dd5; an unknown key is "
            "refused. A bullet is a finding or a test, not a sentence with a bullet in "
            "front of it."
        ),
        "setup": ("One or two sentences on the SHAPE of what the scan found, naming "
                  "the lines that moved via {{placeholders}} rather than in your own "
                  "words. The coverage counts are printed above this line already; "
                  "do not restate them. Be specific about what rose and what fell: "
                  "'the books behind it contracted' names nothing a reader can check."),
        "divergence": "Read the divergences the engine flagged. Where do the series disagree, and why does that matter.",
        "guidance": ["LIST of at most 4 short bullets. Does management's outlook continue "
                     "the measured moves or reverse them, and which row matters most. Where "
                     "no guide exists, say what stands in for the missing test - never that "
                     "it is missing, which the engine's own line above already says."],
        "_available_placeholders": ph,
    }
    dives = payload.get("deep_dive") or []
    _bind = {}
    for i, r in enumerate(dives, 1):
        guide[f"dd{i}"] = (
            "Two sentences on %s, and on that line only: what inflected and what "
            "it implies for the business." % (r.get("display") or r.get("name")))
        _bind[f"dd{i}"] = sorted(required_binding_words(r))
    if _bind:
        guide["_binding_words"] = _bind
    cap = ((payload.get("constants") or {}).get("deep_dive_rows")) or 3
    if len(dives) < cap:
        guide["_dive_count"] = (
            "This scan supported %d dive(s), not %d: one written paragraph per "
            "concept, and the material lines did not cover more. Do not write a "
            "dd%d - there is no row for it."
            % (len(dives), cap, len(dives) + 1))
    return guide



def esc(s):
    return html.escape(str(s if s is not None else ""))


FOOTNOTE_DIGIT = re.compile(r"(?<=[a-z\)])(\d)(?=[,.;:]|\s|$)")


def src_text(t):
    return esc(FOOTNOTE_DIGIT.sub("", str(t if t is not None else "")))


def guide_text(s):
    t = str(s if s is not None else "")
    t = t.replace("+/-", "\u00b1").replace("$", "")
    t = re.sub(r"\s*-\s*(?=\u00b1)", " ", t)
    return html.escape(re.sub(r"\s{2,}", " ", t).strip())


def fnum(v, dp=1):
    return fmt_number(v, dp)


def flevel(r):
    v = r.get("display_value", r.get("latest_value"))
    if v is None:
        return "n/a"
    if r.get("kind") == "pre_growth":
        return f"{v:,.1f}%"
    lab = r.get("unit_label")
    dp = r.get("unit_decimals", 2)
    status = r.get("unit_status")
    if status == "conflicted":
        return '<span class="tag">level withheld, unit unresolved</span>'
    if lab == "%":
        return f"{v:,.2f}%"
    if lab == "x":
        return f"{v:,.2f}x"
    if lab in ("billions", "millions", "thousands"):
        if r.get("latest_value") is not None:
            v = r["latest_value"]
        else:
            v = v * {"billions": 1e9, "millions": 1e6, "thousands": 1e3}[lab]
        lab = "currency"
    if lab == "count" and r.get("currency"):
        lab = "currency"
    if lab == "count":
        a = abs(v)
        for cut, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
            if a >= cut:
                return f"{v / cut:,.2f}{suf}"
        return f"{v:,.0f}"
    if lab and lab != "currency":
        if abs(v) >= 1e6:
            for cut, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
                if abs(v) >= cut:
                    return f"{v / cut:,.2f}{suf} {esc(lab)}"
        return f"{v:,.{dp}f} {esc(lab)}"
    _c = esc(r.get("currency") or "")
    a = abs(v)
    sg = "-" if v < 0 else ""
    for cut, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= cut:
            return f"{sg}{_c}{a / cut:,.2f}{suf}"
    return f"{sg}{_c}{a:,.2f}"


def fabs_change(r):
    if r.get("unit_status") == "conflicted":
        return '<span class="tag">withheld</span>'
    v = r.get("absolute_change")
    if v is None:
        return "n/a"
    lab = r.get("unit_label")
    dp = r.get("unit_decimals", 2)
    if lab == "x":
        return f"{v:+,.2f}x"
    if lab == "%":
        return f"{v * 100:+,.0f} bps"
    if lab == "count":
        a = abs(v)
        for cut, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
            if a >= cut:
                return f"{v / cut:+,.2f}{suf}"
        return f"{v:+,.0f}"
    if lab and lab != "currency":
        return f"{v:+,.{dp}f} {esc(lab)}"
    _c = esc(r.get("currency") or "")
    sg = "+" if v >= 0 else "-"
    a = abs(v)
    for cut, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= cut:
            return f"{sg}{_c}{a / cut:,.2f}{suf}"
    return f"{sg}{_c}{a:,.2f}"


def _dp_for(unit, dp):
    return 0 if unit != "pct" else dp


def fgrowth(v, unit, dp=1):
    if v is None:
        return "n/a"
    return fmt_number(v, _dp_for(unit, dp), signed=True) + ('%' if unit == 'pct' else ' bps')


def faccel(v, unit, dp=1):
    if v is None:
        return "n/a"
    return fmt_number(v, _dp_for(unit, dp), signed=True) + (' pp' if unit == 'pct' else ' bps')


UNUSUAL_BANDS = [(2.5, "Very unusual"), (1.5, "Unusual"), (0.75, "Notable"),
                 (0.0, "Normal range")]


def unusual_label(z):
    if z is None:
        return "n/a"
    a = round(abs(z), 1)
    for cut, word in UNUSUAL_BANDS:
        if a >= cut:
            return word
    return "Normal range"


def funusual(z):
    if z is None:
        return "n/a"
    return unusual_label(z) + " (" + ("%.1f" % abs(z)) + " SD)"


def table_legend(rows):
    base = any(r.get("comp_flag") for r in rows)
    return ('<p class="note"><b>Reading the table.</b> Y/Y now and Y/Y last Q are '
            'the growth rates this quarter and last; pace change is the gap between '
            'them, and Direction says whether growth sped up or slowed. Read on pace '
            'says whether that change is good or bad for the business; Level says '
            'which way the line itself moved year over year. vs own history sizes the '
            'pace change against the line\u2019s own history, in standard deviations '
            '(SD) of its past quarter-to-quarter changes in pace: notable from 0.75 SD, '
            'unusual from 1.5 SD, very unusual from 2.5 SD.'
            + (' <sup class="mk cm">*</sup> InSync calculation from company-reported figures; the company does not publish this exact number.'
               if any(r.get("provenance") == "derived" for r in rows) else '')
            + (' <sup class="mk">\u2020</sup> Last quarter\u2019s growth rate was in the '
               'top or bottom quartile of this line\u2019s history, so part of the change '
               'in pace is that comparison.' if base else '') + '</p>')


UNUSUAL_NOTE = ""


def marks_legend(rows):
    bits = []
    if any(r.get("provenance") == "derived" for r in rows):
        bits.append('<sup class="mk cm">*</sup> InSync calculation from company-reported figures; the company does not publish this exact number')
    if any(r.get("comp_flag") for r in rows):
        bits.append('<sup class="mk">\u2020</sup> last quarter\u2019s growth rate was in the top '
                    'or bottom quartile of this line\u2019s history, so part of the change in '
                    'pace is that comparison')
    if any(r.get("adjustment_effect") for r in rows):
        bits.append('<sup class="mk aq">\u2021</sup> includes a one-time, policy or acquisition '
                    'item that management sized; see Adjustment quality')
    return ('<p class="note"><b>Marks.</b> ' + "; ".join(bits) + '.</p>') if bits else ""


def _aq_sentence(e, move=None):
    src = " ".join(x for x in (esc(e.get("quarter")), esc(e.get("document"))) if x)
    pg = (", p" + esc(e["page"])) if str(e.get("page") or "").strip() else ""
    tail = " (" + esc(e["kind"]) + "; " + src + pg + ")"
    if e.get("carried_from"):
        return ("includes the " + esc(e["size_display"]) + " from " + esc(e["label"])
                + " that management sized in the " + esc(e["carried_from"]) + tail
                + "; it sits in this margin too unless the line excludes it")
    if e.get("charge"):
        return ("includes a charge of " + esc(e["size_display"]) + " for " + esc(e["label"])
                + ", per management" + tail + ((", against a move of " + esc(move) + " y/y") if move else ""))
    return ("management attributes " + esc(e["size_display"])
            + ((" of the " + esc(move) + " y/y") if move else "")
            + " to " + esc(e["label"]) + tail)


def _aq_dive(r):
    e = r.get("adjustment_effect")
    if not e:
        return ""
    t = _aq_sentence(e, fgrowth(r.get("latest_growth"), r.get("unit") or "pct")
                     if r.get("latest_growth") is not None else None)
    if e.get("also"):
        t += ". It also " + _aq_sentence(e["also"])
    if e.get("ex_level_display"):
        t += (". Excluding " + ("both" if e.get("ex_both") else "it") + ", about "
              + esc(e["ex_level_display"]) + " (" + esc(e.get("ex_move_display") or "") + " y/y), "
              "InSync arithmetic on management's figure")
    return '<p class="note"><b>Adjustment quality.</b> ' + t[:1].upper() + t[1:] + '.</p>'


def adjustment_quality_note(p, rows=None):
    _dd = {r.get("ltcode") for r in (p.get("deep_dive") or [])}
    _qs = {c.get("ltcode") for c in ((p.get("quarter_strip") or {}).get("cells") or [])
           if c.get("adjustment_effect") and c.get("ltcode") not in _dd}
    items = [e for e in (p.get("adjustment_quality") or [])
             if e.get("ltcode") not in _dd and e.get("ltcode") not in _qs]
    if not items:
        return ""
    return ('<p class="note aqnote"><b>Adjustment quality.</b> ' + " ".join(
        esc(e.get("display")) + ": " + _aq_sentence(e, e.get("move_display")) + "."
        + (" The reported move is shown as reported; most of it is this item."
           if (e.get("share_of_move") or 0) >= 0.5 else "")
        for e in items[:3]) + '</p>')



PROSE_CAP = {"setup": 45, "dd1": 45, "dd2": 45, "dd3": 45, "dd4": 45, "dd5": 45,
             "divergence": 45, "debate": 30}
BULLET_SECTIONS = ("guidance",)
BULLET_CAP = {"max_bullets": 4, "max_words": 22}


def _words(t):
    return len(re.findall(r"[\w'-]+", t or ""))


def check_prose_shape(prose):
    bad = []
    if prose.get("watch"):
        bad.append("watch is retired: the What To Monitor Next table carries the "
                   "questions, and a second list repeated them")
    for k, cap in PROSE_CAP.items():
        v = prose.get(k)
        if isinstance(v, str) and _words(v) > cap:
            bad.append("%s: %d words, cap %d" % (k, _words(v), cap))
    for k in BULLET_SECTIONS:
        v = prose.get(k)
        if not v:
            continue
        if isinstance(v, str):
            bad.append("%s must be a list of short bullets, not a paragraph" % k)
            continue
        if len(v) > BULLET_CAP["max_bullets"]:
            bad.append("%s: %d bullets, cap %d" % (k, len(v), BULLET_CAP["max_bullets"]))
        for b in v:
            if _words(b) > BULLET_CAP["max_words"]:
                bad.append("%s bullet of %d words, cap %d: %r"
                           % (k, _words(b), BULLET_CAP["max_words"], str(b)[:50]))
    return bad


def bullets(v, cls="blt"):
    if not v:
        return ""
    if isinstance(v, str):
        return '<p class="prose">' + esc(v) + "</p>"
    return ('<ul class="' + cls + '">'
            + "".join("<li>" + esc(str(b)) + "</li>" for b in v) + "</ul>")


def fz(v):
    return "n/a" if v is None else f"{v:+.1f}"


def cls(v):
    if v is None:
        return ""
    return "pos" if v > 0 else ("neg" if v < 0 else "")


def sparkline(vals, w=54, h=14):
    pts = [v for v in vals if v is not None]
    if len(pts) < 2:
        return ""
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1.0
    n = len(vals)
    coords, zero_y = [], None
    for i, v in enumerate(vals):
        if v is None:
            continue
        x = (i / max(1, n - 1)) * (w - 2) + 1
        y = h - 1 - ((v - lo) / span) * (h - 2)
        coords.append(f"{x:.1f},{y:.1f}")
    if lo <= 0 <= hi:
        zero_y = h - 1 - ((0 - lo) / span) * (h - 2)
    base = (f'<line x1="1" y1="{zero_y:.1f}" x2="{w-1}" y2="{zero_y:.1f}" '
            f'stroke="#CCC" stroke-width="0.6"/>') if zero_y is not None else ""
    last = pts[-1]
    dot_col = BIN_COLOR["vpos"] if last > 0 else BIN_COLOR["vneg"]
    lx, ly = coords[-1].split(",")
    return (f'<svg class="spark" width="{w}" height="{h}" viewBox="0 0 {w} {h}">{base}'
            f'<polyline points="{" ".join(coords)}" fill="none" stroke="#555" '
            f'stroke-width="1"/><circle cx="{lx}" cy="{ly}" r="1.6" fill="{dot_col}"/></svg>')



def flag_tags(r, with_grade=False):
    out = ""
    if r.get("provenance") == "derived" and not r.get("insync_calculated"):
        out += ('<sup class="mk cm" title="InSync calculation from company-reported figures; the company does not publish this exact number">*</sup>')
    if r.get("period_basis") == "unverified":
        out += '<span class="tag" title="the series cannot prove it is quarterly">period unverified</span>'
    if r.get("comp_flag") in ("easy_base", "hard_base"):
        out += ('<sup class="mk" title="last quarter\'s growth rate was in the top or '
                'bottom quarter of this line\'s history">\u2020</sup>')
    if r.get("adjustment_effect"):
        out += ('<sup class="mk aq" title="includes a one-time, policy or acquisition item '
                'sized by management">\u2021</sup>')
    if r.get("provenance") == "spoken":
        out += '<span class="tag" title="at least one quarter came from an earnings call rather than a filing">call-sourced</span>'
    if r.get("small_base"):
        out += '<span class="tag" title="the growth rate is measured off an unusually small base">small base</span>'
    if r.get("small_line"):
        out += '<span class="tag" title="below one percent of quarterly revenue">small</span>'
    if re.search(r"financing activit|\bcff\b|debt issu|repayment|share repurchase|"
                 r"buyback|dividends? paid", str(r.get("name") or ""), re.I):
        out += '<span class="tag" title="moves with issuance, repayment, buybacks and dividends">capital structure</span>'
    if (r.get("unit_status") == "validated"
            and "reproduces this line" in str(r.get("unit_source") or "")):
        _c = str(r.get("confidence") or "").strip().lower() if with_grade else ""
        out += ('<span class="tag" title="scale taken from the issuer\'s own arithmetic; '
                'confidence capped at moderate">unit-derived'
                + (', ' + esc(_c) if _c else "") + '</span>')
    return out



def logo_tag():
    for name in ("insync_mark_b64.txt", "insync_logo_b64.txt"):
        path = os.path.join(ASSETS, name)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                b64 = fh.read().strip()
            if b64:
                return '<img class="logo" src="data:image/png;base64,' + b64 + '" alt="InSync Analytics">'
    raise SystemExit("STOP: the InSync logo (assets/insync_mark_b64.txt) is missing. Install the "
                     "full skill folder - SKILL.md, scripts/, references/ and assets/ - and render again.")


def _prepared_by(p):
    return (p.get("prepared_by") or "").strip()


def _sector_shown(p):
    r0 = p.get("sector_routing") or {}
    return (r0.get("template_label") if r0.get("template")
            else (r0.get("insync_subindustry") or p.get("sector") or ""))


def masthead(p):
    co = esc(p.get("company") or p["ticker"])
    cb = p.get("comparison_basis") or {}
    lq = esc(p["latest_quarter"])
    sub = (p.get("sector_routing") or {}).get("insync_subindustry")
    sec = esc(sub or _sector_shown(p) or "")
    qe = _qend_full(p)
    ed = p.get("earnings_date")
    rows = [("Sector", sec or "not classified"),
            ("Quarter End", (esc(qe) if qe else "not in source") + " (fiscal " + lq + ")"),
            ("Earnings Date", esc(_dmy(ed)) if ed else "not in source"),
            ("Comparison", (esc(cb["year_ago"]) + " (year ago) &middot; " + esc(cb["last_quarter"])
                            + " (last quarter)") if cb.get("year_ago") and cb.get("last_quarter") else "n/a"),
            ("Prepared", esc(_dmy(p["generated_utc"][:10])))]
    grid = "".join('<div class="mf"><span class="k">' + k + ':</span> <span>' + v + '</span></div>'
                   for k, v in rows)
    return ('<div class="mast"><div>'
            '<div class="brandline">' + logo_tag() +
            '<span class="wm"><b>InSync</b> <span>Analytics</span></span></div>'
            '<div class="co">' + co + ' <em>(' + esc(p["ticker"]) + ')</em></div>'
            '<div class="prod">Inflection Monitor</div>'
            '<div class="prodsub">' + esc(PRODUCT_DESCRIPTOR) + ' | ' + lq + '</div>'
            '</div><div class="right mgrid">' + grid + '</div>'
            '</div><div class="mastrule"></div>'
            '<p class="purpose">' + esc(PURPOSE_STATEMENT) + '</p>')


def _dmy(d):
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(d or ""))
    if not m or not (1 <= int(m.group(2)) <= 12):
        return str(d or "")
    return "%d %s %s" % (int(m.group(3)), _MONTHS[int(m.group(2)) - 1], m.group(1))


def _qend_full(p):
    return _dmy(p.get("latest_quarter_end")) if p.get("latest_quarter_end") else ""


PURPOSE_STATEMENT = ("Identifies changes in reported financial and operating momentum relative "
                     "to each metric's own historical pattern. Uses reported company data and "
                     "does not compare results with consensus estimates, valuation, or "
                     "share-price performance.")


PRODUCT_DESCRIPTOR = "Reported Financial & Operating Momentum"
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _qend(p):
    m = re.match(r"^(\d{4})-(\d{2})-\d{2}$", str(p.get("latest_quarter_end") or ""))
    if not m or not (1 <= int(m.group(2)) <= 12):
        return ""
    return ", quarter ended " + _MONTHS[int(m.group(2)) - 1] + " " + m.group(1)


WORD_N = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
          7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven",
          12: "twelve", 13: "thirteen"}
SCAN_BREADTH_FLOOR = 25


def scan_scope_banner(p):
    c = p["coverage"]
    ss = p.get("scan_status") or {}
    planned, scanned = c.get("series_planned"), c["series_scanned"]
    if not ss.get("partial"):
        return ""
    got, plan = (ss.get("primary_supporting_retrieved"),
                 ss.get("primary_supporting_planned"))
    core = ""
    if got and plan:
        core = (' Of the lines a conclusion normally rests on, this run read '
                + esc(got) + ' of ' + esc(plan) + '.')
    return ('<div class="partial"><b>Scope limitation.</b> This note covers '
            + esc(scanned) + ' of the ' + esc(planned or scanned) + ' metrics '
            'this company discloses.' + core + ' Every figure below is a '
            'reported actual, and the direction and summary describe these '
            'lines rather than the company: a line that inflected outside them '
            'would not appear here. The metrics not read are listed at the '
            'back.</div>')


def coverage_line(p):
    return ""


def coverage_statement(p):
    c = p["coverage"]
    scanned = c["series_scanned"]
    planned = c.get("series_planned")
    r0 = p.get("sector_routing") or {}
    shown = (r0.get("template_label") if r0.get("template")
             else (r0.get("insync_subindustry") or p.get("sector") or "General"))
    sector = (", " + esc(shown)) if shown else ""
    tmpl = ""
    _ss = p.get("scan_status") or {}
    _partial = _ss.get("partial")
    if _partial is None:
        _partial = bool(planned and scanned < planned)
    if _partial:
        breadth = ('Scanned ' + esc(scanned) + ' of ' + esc(planned) +
                   ' planned metrics - a slice of the disclosed surface, not a '
                   'company-wide scan.')
    elif (p.get("not_retrieved") or {}).get("missing_count") or (planned and scanned < planned):
        _miss = (p.get("not_retrieved") or {}).get("missing_count")
        if _miss is None:
            _miss = (planned or 0) - (scanned or 0)
        breadth = (esc(_miss) + (' metric was' if _miss == 1 else ' metrics were')
                   + ' requested and returned no data; '
                   + ('it is' if _miss == 1 else 'they are') + ' named under Sources.')
    elif scanned < SCAN_BREADTH_FLOOR:
        breadth = ('Scanned ' + esc(scanned) + ' metrics - a slice of the disclosed '
                   'surface, so an inflection outside it is not visible here.')
    else:
        breadth = 'Every metric this scan asked for returned data.'
    ss = p.get("scan_status") or {}
    if ss.get("scan_confidence"):
        n_unres = ss.get("unresolved_units") or 0
        why = []
        if ss.get("partial"):
            got, plan = (ss.get("primary_supporting_retrieved"),
                         ss.get("primary_supporting_planned"))
            why.append("primary and supporting coverage below the seventy per cent "
                       "floor at " + esc(got) + " of " + esc(plan)
                       if got and plan else
                       "primary and supporting coverage below the floor")
        if n_unres:
            named = list(ss.get("unresolved_unit_lines") or [])
            if named and len(named) == n_unres:
                which = (" (" + ", ".join(named[:2])
                         + (" and " + WORD_N.get(len(named) - 2, str(len(named) - 2))
                            + " others" if len(named) > 2 else "") + ")")
            else:
                which = ""
            why.append(("one line carries an unresolved unit, so its level is "
                        "withheld" + which) if n_unres == 1 else
                       (WORD_N.get(n_unres, str(n_unres))
                        + " lines carry unresolved units, so their levels are "
                        "withheld" + which))
        if (c.get("quarters") or 0) < 13:
            why.append("the lookback is shorter than " + WORD_N.get(13, "thirteen")
                       + " quarters")
        _qz = ss.get("quantised_lines") or []
        if _qz:
            _step = max((q.get("step_bps") or 0) for q in _qz)
            why.append(("one ratio is" if len(_qz) == 1 else
                        WORD_N.get(len(_qz), str(len(_qz))) + " ratios are")
                       + " reported only to the nearest "
                       + ("%g" % (_step / 100.0)) + " per cent")
        if why:
            _w = esc("; ".join(why))
            breadth += " " + _w[:1].upper() + _w[1:] + "."
        _uns = p.get("unscoreable_primary") or []
        if _uns:
            _by = {}
            for u in _uns:
                _by.setdefault(str(u.get("reason") or "not scored"), []).append(u)
            _clauses = []
            for _why, _grp in _by.items():
                _nm = [u.get("display") or u["name"] for u in _grp[:4]]
                _lead = (", ".join(_nm[:-1]) + " and " + _nm[-1]) if len(_nm) > 1 else _nm[0]
                _clauses.append(esc(_lead)
                                + (" and " + WORD_N.get(len(_grp) - 4, str(len(_grp) - 4))
                                   + " others" if len(_grp) > 4 else "")
                                + ", because " + esc(_why))
            _scored_disp = [str(r.get("display") or "") for k in
                            ("headline_moves", "accelerating", "decelerating", "deep_dive")
                            for r in (p.get(k) or [])]
            _rel_notes = []
            for u in _uns:
                ud = str(u.get("display") or "").strip()
                if not ud:
                    continue
                _stem = re.sub(r"^(reported|basic|diluted)\s+", "", ud, flags=re.I).lower()
                for sd in _scored_disp:
                    sl = sd.lower()
                    if sd == ud or sd in _rel_notes:
                        continue
                    if (sl.startswith(_stem + " ") or sl.startswith("adjusted " + _stem)
                            or (re.search(r"\beps\b", _stem) and re.match(r"adjusted eps", sl))):
                        _rel_notes.append(sd)
                        break
            _eps_note = "".join(" " + esc(sd) + " is scored on its own history."
                                for sd in _rel_notes[:3])
            breadth = ("Not scored for inflection: " + "; ".join(_clauses)
                       + (". It still appears as a reported figure where relevant."
                          if len(_uns) == 1 else
                          ". They still appear as reported figures where relevant.")
                       + _eps_note + " " + breadth)
        inc = ss.get("unresolved_units_in_counts")
        if inc:
            mat = [x for x in inc if x.get("population") == "material"]
            ctx = [x for x in inc if x.get("population") == "primary_context"]
            if mat:
                _mn = [x["metric"] for x in mat]
                _mtxt = (_mn[0] + " and " + WORD_N.get(len(_mn) - 1, str(len(_mn) - 1))
                         + " other") if len(_mn) == 2 else \
                        (_mn[0] + " and " + WORD_N.get(len(_mn) - 1, str(len(_mn) - 1))
                         + " others") if len(_mn) > 2 else _mn[0]
                breadth += (" " + esc(_mtxt)
                            + (" is" if len(mat) == 1 else " are")
                            + " still counted above: the level is withheld but "
                            "the percentage change is not, because a percentage "
                            "does not depend on the unit"
                            + ("." if all(x["displayed"] for x in mat)
                               else ". " + ("It ranks" if len(mat) == 1 else "They rank")
                               + " below the display cap."))
            elif ctx:
                breadth += (" " + esc(", ".join(x["metric"] for x in ctx))
                            + " appears only as primary context; no count rests on it.")
        elif inc == []:
            breadth += ((" It did not clear the materiality bars, so no count "
                         "here rests on a withheld level.") if n_unres == 1 else
                        (" None of them cleared the materiality bars, so no count "
                         "here rests on a withheld level."))
    return ('<p class="scope"><b>' + esc(p.get("company") or p["ticker"]) + sector
            + tmpl + '.</b> ' + esc(scanned) + ' metrics scanned'
            + '<span class="sep">|</span>' + esc(c["series_ranked"]) + ' scored'
            + '<span class="sep">|</span>' + esc(c.get("material_total", 0))
            + ' material inflections'
            + '<span class="sep">|</span>' + esc(c.get("primary_context", 0))
            + ' contextual movements.</p>'
            '<p class="scopenote"><b>Coverage.</b> '
            + breadth.strip() + '</p>')



def prevq(r):
    ps = r.get("period_series") or []
    return ps[-2] if len(ps) >= 2 else (r.get("prev_period") or "last quarter")


def yy_pair(r, g="latest_growth", pg="prev_growth"):
    u = r.get("unit", "pct")
    out = fgrowth(r.get(g), u) + ("" if r.get("kind") == "pre_growth" else " y/y")
    if r.get(pg) is not None:
        out += " (" + esc(prevq(r)) + ": " + fgrowth(r.get(pg), u) + ")"
    return out


def not_scored_note(p):
    _uns = p.get("unscoreable_primary") or []
    if not _uns:
        return ""
    by = {}
    for u in _uns:
        by.setdefault(str(u.get("reason") or "not scored"), []).append(u.get("display") or u["name"])
    parts = []
    for why, names in by.items():
        nm = names[:4]
        lead = (", ".join(nm[:-1]) + " and " + nm[-1]) if len(nm) > 1 else nm[0]
        if len(names) > 4:
            lead += " and " + WORD_N.get(len(names) - 4, str(len(names) - 4)) + " others"
        parts.append(esc(lead) + ": " + esc(why))
    return ('<p class="note"><b>Not scored for a change in pace.</b> '
            + "; ".join(parts) + ". They still appear as reported figures where relevant.</p>")


def non_operating_window_text(items):
    parts = []
    for it in items or []:
        parts.append(esc(it.get("period")) + " (" + esc(it.get("role")) + ") included "
                     + esc(it.get("value_display")) + " of " + esc(it.get("display"))
                     + ", " + esc("%.1f" % float(it.get("share_of_operating_profit_pct") or 0))
                     + "% of that quarter's operating profit")
    if not parts:
        return ""
    return ("; ".join(parts) + ". The change in pace is measured against "
            + ("that quarter" if len(parts) == 1 else "those quarters") + ".")


def quarter_strip(p):
    q = p.get("quarter_strip") or {}
    cells = q.get("cells") or []
    g = q.get("guide")
    if not cells and not g:
        return ""
    tds = []
    for c in cells:
        u = c.get("unit", "pct")
        if c.get("kind") == "pre_growth":
            _vs = [v for v in (c.get("latest_growth"), c.get("prev_growth"), c.get("year_ago_rate"))
                   if v is not None]
            _dp = c.get("rate_decimals", 0 if _vs and all(float(v).is_integer() for v in _vs) else 1)
            prev = ((esc(c.get("prev_period")) + ": " + frate(c.get("prev_growth"), _dp))
                    if c.get("prev_growth") is not None else "")
            ya = ((esc(c.get("year_ago_period")) + ": " + frate(c.get("year_ago_rate"), _dp))
                  if c.get("year_ago_rate") is not None else "")
            tds.append('<td><div class="qs-l">' + esc((c.get("label") if c.get("label") not in
                                                        (None, "Revenue", "EPS", "Key metric")
                                                        and not str(c.get("display") or "").startswith("InSync-Calculated")
                                                        else None)
                                                       or c.get("display") or c.get("name")) + '</div>'
                       '<div class="qs-v">' + frate(c.get("latest_growth"), _dp) + '</div>'
                       '<div class="qs-g">' + " &middot; ".join(x for x in (prev, ya) if x) + '</div></td>')
            continue
        yy = (fgrowth(c.get("latest_growth"), u) + " y/y") if c.get("latest_growth") is not None else "y/y not scored"
        last = ((" &middot; " + esc(c.get("prev_period")) + ": " + fgrowth(c.get("prev_growth"), u))
                if c.get("prev_growth") is not None and c.get("latest_growth") is not None else "")
        ex = c.get("exclusion") or {}
        extra = ""
        _ce = c.get("adjustment_effect") or {}
        if _ce and c.get("ltcode") in {r.get("ltcode") for r in (p.get("deep_dive") or [])}:
            _ce = {}
        if _ce.get("size_display"):
            extra += ('<div class="qs-x">includes ' + ("a charge of " if _ce.get("charge") else "")
                      + esc(_ce["size_display"]) + (" for " if _ce.get("charge") else " from ")
                      + esc(_ce["label"]) + ' (' + esc(_ce["kind"]) + ', per management)</div>')
        if ex.get("excl_display_value"):
            item = re.sub(r"in-?process r\s*&\s*d", "IPR&D", str(ex.get("item") or ""), flags=re.I)
            extra = ('<div class="qs-x">includes ' + esc(item[:1].lower() + item[1:])
                     + '; ' + esc(ex["excl_display_value"]) + ' excluding it (InSync line)</div>')
        _nw = [it for it in (c.get("non_operating_window") or []) if it.get("role") == "last quarter"]
        if _nw and last:
            extra += ('<div class="qs-x">' + esc(_nw[0]["period"]) + ' included '
                      + esc(_nw[0]["value_display"]) + ' of ' + esc(_nw[0]["display"]) + '</div>')
        tds.append('<td><div class="qs-l">' + esc((c.get("label") if c.get("label") not in
                                                    (None, "Revenue", "EPS", "Key metric")
                                                    and not str(c.get("display") or "").startswith("InSync-Calculated")
                                                    else None)
                                                   or c.get("display") or c.get("name")) + '</div>'
                   '<div class="qs-v">' + flevel(c) + '</div>'
                   '<div class="qs-g">' + yy + last + '</div>' + extra + '</td>')
    if g and g.get("items_only"):
        _io = g["items_only"]
        tds.append('<td><div class="qs-l">' + esc(_io.get("adjusted_display")) + ' guide, FY'
                   + esc(g.get("target_period")) + '</div>'
                   '<div class="qs-v">' + esc(_io.get("adjusted_guide")) + '</div>'
                   '<div class="qs-g">Maintained in ' + esc(g.get("changed_in") or "") + '; '
                   + esc(g.get("display") or "reported") + ' guide ' + esc(g.get("latest_display") or "")
                   + ' includes one-time items</div></td>')
    elif g:
        tds.append('<td><div class="qs-l">' + esc(g.get("display") or g.get("name")) + ' guide, FY'
                   + esc(g.get("target_period")) + '</div>'
                   '<div class="qs-v">' + esc(g.get("latest_display") or g.get("latest_text")) + '</div>'
                   '<div class="qs-g">' + esc(str(g.get("direction") or "")[:1].upper()
                                              + str(g.get("direction") or "")[1:])
                   + ((' in ' + esc(g.get("changed_in"))) if g.get("changed_in") else '')
                   + ' from ' + esc(_as_range(str(g.get("first_display") or g.get("first_text") or "")))
                   + ((', repeated ' + esc(g.get("latest_source_period")))
                      if g.get("changed_in") and g.get("latest_source_period")
                      and g.get("latest_source_period") != g.get("changed_in") else '')
                   + '</div>' + _tile_component_caveat(p, g) + '</td>')
    return '<table class="qs"><tr>' + "".join(tds) + "</tr></table>"


def _tile_component_caveat(p, g):
    nm = re.sub(r"[^a-z]", "", str(g.get("name") or g.get("display") or "").lower())
    for c in (p.get("source_corrections") or []):
        if str(c.get("target_period")) == str(g.get("target_period")) and "eps" in nm \
                and "eps" in re.sub(r"[^a-z]", "", str(c.get("metric") or "").lower()):
            return ('<div class="qs-x">source labels exchanged so the parts add up; '
                    'see guidance</div>')
    for c in ((p.get("guidance_check") or {}).get("component_checks") or []):
        base = re.sub(r"[^a-z]", "", str(c.get("metric") or "").lower())
        if base and (base in nm or ("eps" in base and "eps" in nm)) \
                and str(c.get("target_period")) == str(g.get("target_period")):
            return ('<div class="qs-x">the release\u2019s components sum to about '
                    + esc(c.get("implied_display")) + '; see guidance</div>')
    if re.search(r"\beps\b|earnings per share", str(g.get("name") or g.get("display") or ""), re.I):
        for n in ((p.get("guidance_check") or {}).get("outlook_notes") or []):
            if n.get("headline_annual") and str(n.get("target_period")) == str(g.get("target_period")) \
                    and re.search(r"earnings per share|\beps\b", str(n.get("text") or ""), re.I) \
                    and re.search(r"\bexclud", str(n.get("text") or ""), re.I):
                return ('<div class="qs-x">excludes items named in the ' + esc(n.get("source_period"))
                        + ' outlook note; see guidance</div>')
    return ""


def standfirst(prose):
    txt = (prose or {}).get("setup", "")
    return ('<p class="stand">' + esc(txt) + "</p>") if txt.strip() else ""


def summary_bullets(p, prose):
    used = set()

    def _row_bullet(r):
        _tag = (", " + esc(r["pace_only"])) if r.get("pace_only") else ""
        _xt = r.get("exclusion_twin") or {}
        _xtxt = ""
        _adj = next((c for c in ((p.get("quarter_strip") or {}).get("cells") or [])
                     if re.search(r"adjusted eps", str(c.get("display") or ""), re.I)
                     and c.get("ltcode") != r.get("ltcode")), None)
        if _adj and _adj.get("latest_growth") is not None and re.fullmatch(
                r"(diluted |reported |basic )?eps", str(r.get("display") or r.get("name") or "").strip(), re.I):
            _xtxt = (" Adjusted EPS " + flevel(_adj)
                     + ", " + fgrowth(_adj["latest_growth"], "pct")
                     + " y/y; the reported figure includes items the company excludes from it.")
            _no = p.get("non_operating") or {}
            if _no.get("value_display"):
                _xtxt += (" Reported " + esc(_no["display"]) + " was " + esc(_no["value_display"])
                          + " this quarter"
                          + ((", against " + esc(_no["adjusted_value_display"]) + " in the adjusted line")
                             if _no.get("adjusted_value_display") else "") + ".")
        if _xt.get("growth_display"):
            _xi = re.sub(r"in-?process r\s*&\s*d", "IPR&D", str(_xt.get("item") or ""), flags=re.I)
            _xtxt = " Excluding " + esc(_xi) + ": " + esc(_xt["growth_display"]) + " y/y."
        _yc = r.get("year_ago_comparison") or {}
        _strong = (_yc.get("which") == "strong" and (r.get("latest_accel") or 0) < 0)
        if (_yc.get("which") == "weak" or _strong) and _yc.get("two_year_change_display"):
            _xtxt += ((" Strong comparison: " if _strong else " Weak comparison: ")
                      + esc(_yc["year_ago_period"]) + " was "
                      + esc(_yc["year_ago_growth_display"]) + " y/y; against "
                      + esc(_yc["two_year_period"]) + " it is " + esc(_yc["two_year_change_display"]) + ".")
        _ae = r.get("adjustment_effect") or {}
        _al = _ae.get("also") or {}
        if _al.get("size_display"):
            _xtxt += (" Includes " + esc(_al["size_display"]) + " from " + esc(_al["label"])
                      + " (" + esc(_al["kind"]) + ", carried from the gross margin) and")
        if _ae.get("size_display"):
            _xtxt += ((" a charge of " if _al else " Includes a charge of ") if _ae.get("charge")
                      else (" " if _al else " Includes "))
            _xtxt += (esc(_ae["size_display"]) + (" for " if _ae.get("charge") else " from ")
                      + esc(_ae["label"]) + " (" + esc(_ae["kind"]) + ", "
                      + ("carried from the gross margin" if _ae.get("carried_from") else "per management") + ")"
                      + ((", about " + esc(_ae["ex_move_display"]) + " y/y excluding "
                          + ("both" if _ae.get("ex_both") else "it"))
                         if _ae.get("ex_move_display") else "") + ".")
            _sh = _ae.get("share_net", _ae.get("share_of_move")) or 0
            if _sh >= 0.5:
                _tag += (", but most of the move is one-time" if (_ae.get("kind") == "one-time" and not _al)
                         else ", but most of the move comes from items management sized")
        return ('<b>' + esc(r.get("display") or r["name"]) + '</b> ' + yy_pair(r)
                + '; pace ' + faccel(r["latest_accel"], r["unit"]) + ', '
                + esc(funusual(r["z_display"])).lower().replace(" sd)", " SD)") + '. '
                + esc(r.get("implication")) + ' on pace' + _tag + '.' + _xtxt)

    prim = []
    for r in p.get("headline_moves", []):
        if len(prim) >= 3:
            break
        if r.get("ltcode") in used:
            continue
        used.add(r.get("ltcode"))
        prim.append(_row_bullet(r))
    ctxb = []
    story = p.get("primary_story")
    if story:
        legs = story.get("metrics") or []
        leg_txt = "; ".join(
            esc(m.get("display") or m.get("name")) + " " + yy_pair(m, "growth", "prev_growth")
            for m in legs[:2] if m.get("growth") is not None)
        if (story.get("bridge") or {}).get("text"):
            leg_txt = esc(story["bridge"]["text"]).rstrip(".")
        ctxb.append('<b>' + esc(story.get("headline") or "") + '.</b> ' + (leg_txt + '.' if leg_txt else '')
                    + ((' ' + esc(story.get("relationship")) + '.') if story.get("relationship") else ''))
    for ex in (p.get("company_exclusions") or [])[:1]:
        if len(ctxb) >= 2:
            break
        if ex.get("insync_calculated") and ex.get("item_per_share_display") \
                and (ex.get("growth_gap_pp") or 0) >= 2.0:
            ctxb.append('<b>' + esc(ex["item"]) + ' is inside ' + esc(ex["base_display"]) + '.</b> '
                        + 'It adds ' + esc(ex["item_per_share_display"]) + ' per share this quarter'
                        + ((' (' + esc(ex["prior_period"]) + ': ' + esc(ex["prior_item_per_share_display"]) + ')')
                           if ex.get("prior_item_per_share_display") else '')
                        + '. InSync-Calculated ' + esc(ex["base_display"]) + ' excluding it: '
                        + esc(ex["excl_display_value"]) + ', ' + esc(ex["excl_growth_display"])
                        + ' y/y, against ' + esc(ex["base_display_value"]) + ', '
                        + esc(ex["base_growth_display"]) + ' reported.')
            continue
        if ex.get("base_value") and (abs(ex.get("gap") or 0) >= 0.10 * abs(ex["base_value"])
                                     or (ex.get("growth_gap_pp") or 0) >= 2.0):
            ctxb.append('<b>' + esc(ex["item"]) + ' is inside ' + esc(ex["base_display"]) + '.</b> '
                        + 'The company-reported ' + esc(ex["base_display"]) + ' of '
                        + esc(ex["base_display_value"]) + ' includes it; ' + esc(ex["excl_display_value"])
                        + ' excluding it is an InSync line, not a company measure. The '
                        + esc(ex["gap_display"]) + ' difference compares with '
                        + (esc(ex["prior_gap_display"]) + ' in ' + esc(ex["prior_period"])
                           if ex.get("prior_gap_display") else 'no matching year-ago figure')
                        + '.' + ((' Growth excluding it: ' + esc(ex["excl_growth_display"])
                                  + ' y/y, against ' + esc(ex["base_growth_display"]) + ' reported.')
                                 if ex.get("excl_growth_display") and (ex.get("growth_gap_pp") or 0) >= 2.0 else ''))
    _rates = [r for r in (p.get("rate_kpis") or []) if r.get("z_display") is not None
              and abs(r["z_display"]) >= 0.75 and r.get("ltcode") not in used
              and r.get("rate_primary")]
    for r in _rates[:1]:
        if len(ctxb) >= 2:
            break
        cur, prev, pq, ya, yq, dq, dy = _rate_parts(r)
        used.add(r.get("ltcode"))
        ctxb.append('<b>' + esc(r.get("display") or r["name"]) + '</b> ' + rate_pair(r)
                    + ': ' + faccel(dq, "pct") + ' vs last quarter'
                    + ((', ' + faccel(dy, "pct") + ' vs a year ago') if dy is not None else '')
                    + '; ' + esc(funusual(r["z_display"])).lower().replace(" sd)", " SD)") + '. '
                    + esc(r.get("implication")) + ' on the change'
                    + ((', still below a year ago' if dy < 0 else ', above a year ago')
                       if dy is not None and dq is not None and (dy < 0) != (dq < 0) else '')
                    + '.')
    if not story and len(ctxb) < 2:
        ctxb.insert(0, '<b>No cross-metric relationship stood out;</b> the findings are '
                       'line by line.')
    proofs = []
    for m in (p.get("monitor") or [])[:3]:
        _parts = [x.strip() for x in str(m.get("next_proof") or "").split(";")]
        _np = "; ".join([_parts[0]] + [x for x in _parts[1:] if x.startswith("excluding the item")])
        proofs.append('<b>' + esc(m.get("variable")) + ':</b> ' + esc(_np) + '.')
    parts = []
    if prim:
        parts.append('<div class="tsub">Primary inflections</div><ul>'
                     + "".join("<li>" + x + "</li>" for x in prim) + '</ul>')
    if ctxb:
        parts.append('<div class="tsub">Context</div><ul>'
                     + "".join("<li>" + x + "</li>" for x in ctxb[:2]) + '</ul>')
    if prose.get("debate"):
        parts.append('<div class="tsub">Key research debate</div><p class="debate">'
                     + esc(prose["debate"]) + '</p>')
    if proofs:
        parts.append('<div class="tsub">Next-quarter proof points</div><ul class="pp">'
                     + "".join("<li>" + x + "</li>" for x in proofs) + '</ul>')
    return ('<div class="takeaways"><div class="tt">Executive summary</div>'
            + "".join(parts) + '</div>')


def headline_table(p):
    rows = p.get("headline_moves") or []
    if not rows:
        return '<p class="note">No movement cleared the materiality bar this quarter.</p>'
    head = ("<thead><tr><th>Metric</th><th>Latest</th><th>Current YoY Change</th>"
            "<th>Prior Quarter's YoY Change</th><th>Change in YoY Growth Pace</th>"
            "<th>Momentum Direction</th><th>Read on Pace</th><th>Current Level Direction</th>"
            "<th>Historical Unusualness</th></tr></thead>")
    body = "".join(
        "<tr>"
        '<td class="name">' + esc(r.get("display") or r["name"]) + flag_tags(r) + "</td>"
        '<td class="num">' + flevel(r) + "</td>"
        '<td class="num">' + fgrowth(r["latest_growth"], r["unit"]) + "</td>"
        '<td class="num">' + fgrowth(r["prev_growth"], r["unit"]) + "</td>"
        '<td class="num">' + faccel(r["latest_accel"], r["unit"]) + "</td>"
        "<td>" + esc(direction_word(r)) + "</td>"
        '<td class="' + imp_class(r.get("implication")) + '">' + esc(r.get("implication")) + "</td>"
        "<td>" + level_cell(r) + "</td>"
        '<td class="num">' + esc(funusual(r["z_display"])) + "</td></tr>" for r in rows)
    qual_note = presentation_pointer(rows)
    _no = p.get("non_operating") or {}
    _rep = next((x for x in rows if re.fullmatch(r"(diluted |reported |basic )?eps",
                                                  str(x.get("display") or "").strip(), re.I)), None)
    if _no.get("value_display") and _rep is not None:
        qual_note += ('<p class="note"><b>Below operating profit.</b> ' + esc(_rep.get("display"))
                      + ' includes reported ' + esc(_no["display"]) + ' of ' + esc(_no["value_display"])
                      + ' this quarter' + ((', against ' + esc(_no["adjusted_value_display"])
                                            + ' in the adjusted line') if _no.get("adjusted_value_display") else '')
                      + '.</p>')
    _nwr = [x for x in rows if x.get("non_operating_window")]
    if _nwr:
        qual_note += ('<p class="note nowin"><b>Below operating profit, in a comparison quarter.</b> '
                      + esc(", ".join(str(x.get("display") or x.get("name")) for x in _nwr)) + ': '
                      + non_operating_window_text(_nwr[0]["non_operating_window"]) + '</p>')
    _ev = [e for e in (p.get("label_evidence") or [])
           if e.get("ltcode") in {x.get("ltcode") for x in rows}]
    if _ev:
        qual_note += ('<p class="note"><b>Label.</b> ' + " ".join(esc(e["text"]) + "." for e in _ev)
                      + "</p>")
    return ("<table>" + head + "<tbody>" + body + "</tbody></table>"
            + qual_note + adjustment_quality_note(p, rows) + adjusted_pair_note(rows, (p.get("accelerating") or []) + (p.get("decelerating") or []))
            + exclusion_twin_note(rows) + one_time_note(p)
            + cash_period_note(p, rows)
            + large_move_note(rows) + marks_legend(rows) + small_base_note(rows))


def frate(v, dp=1):
    if v is None:
        return "n/a"
    return fmt_number(v, dp).replace(",", "") + "%"


def _rate_parts(r):
    rv = r.get("rate_view")
    if rv:
        return (rv.get("latest"), rv.get("prev"), rv.get("prev_period") or "last Q",
                rv.get("year_ago"), rv.get("year_ago_period") or "a year ago",
                rv.get("vs_last_q"), rv.get("vs_year_ago"))
    vs = r.get("value_series") or []
    ps = r.get("period_series") or []
    cur = vs[-1] if vs else r.get("latest_growth")
    prev = vs[-2] if len(vs) >= 2 else r.get("prev_growth")
    ya = vs[-5] if len(vs) >= 5 else None
    dq = (cur - prev) if (cur is not None and prev is not None) else None
    dy = (cur - ya) if (cur is not None and ya is not None) else None
    return (cur, prev, (ps[-2] if len(ps) >= 2 else "last Q"), ya,
            (ps[-5] if len(ps) >= 5 else "a year ago"), dq, dy)


def rate_pair(r):
    cur, prev, pq, ya, yq, dq, dy = _rate_parts(r)
    dp = r.get("rate_decimals", 1)
    bits = []
    if prev is not None:
        bits.append(esc(pq) + ": " + frate(prev, dp))
    if ya is not None:
        bits.append(esc(yq) + ": " + frate(ya, dp))
    return frate(cur, dp) + ((" (" + "; ".join(bits) + ")") if bits else "")


def rate_table(p):
    rows = p.get("rate_kpis") or []
    if not rows:
        return ""
    F = lambda r, v: frate(v, r.get("rate_decimals", 1))
    head = ("<thead><tr><th>Rate KPI</th><th>Current Reading</th><th>Prior Quarter</th>"
            "<th>Prior Year</th><th>Sequential Change</th><th>YoY Change</th>"
            "<th>Read on Pace</th><th>Historical Unusualness</th></tr></thead>")
    body = []
    for r in rows:
        cur, prev, pq, ya, yq, dq, dy = _rate_parts(r)
        body.append(
            "<tr>"
            '<td class="name">' + esc(r.get("display") or r["name"]) + flag_tags(r) + "</td>"
            '<td class="num">' + F(r, cur) + "</td>"
            '<td class="num">' + F(r, prev) + "</td>"
            '<td class="num">' + F(r, ya) + "</td>"
            '<td class="num">' + faccel(dq, "pct") + "</td>"
            '<td class="num">' + faccel(dy, "pct") + "</td>"
            '<td class="' + imp_class(r.get("implication")) + '">' + esc(r.get("implication")) + "</td>"
            '<td class="num">' + esc(funusual(r["z_display"])) + "</td></tr>")
    return ('<table class="rates">' + head + "<tbody>" + "".join(body)
            + "</tbody></table>")


def direction_word(r):
    if r.get("kind") != "ratio":
        return str(r.get("streak_dir") or "").title()
    g, pg, a = r.get("latest_growth"), r.get("prev_growth"), r.get("latest_accel")
    if g is None or pg is None or a is None:
        return str(r.get("streak_dir") or "").title()
    if g >= 0 > pg:
        return "Turned up"
    if g < 0 <= pg:
        return "Turned down"
    if g >= 0:
        return "Rising faster" if a > 0 else "Rising less"
    return "Falling faster" if a < 0 else "Falling less"


def exclusion_twin_note(rows):
    bits = []
    for r in rows:
        t = r.get("exclusion_twin") or {}
        if not t.get("growth_display"):
            continue
        item = re.sub(r"in-?process r\s*&\s*d", "IPR&D", str(t.get("item") or ""), flags=re.I)
        bits.append(esc(r.get("display") or r.get("name")) + " excluding " + esc(item) + ": "
                    + ((esc(t["level_display"]) + ", ") if t.get("level_display") else "")
                    + esc(t["growth_display"]) + " y/y"
                    + ((" (" + esc(t.get("prev_period") or "last Q") + ": "
                        + esc(t["prev_growth_display"]) + ")") if t.get("prev_growth") is not None else ""))
    return ('<p class="note"><b>Excluding a named item.</b> ' + "; ".join(bits) + ".</p>") if bits else ""


def adjusted_pair_note(rows, more=()):
    pairs = [r for r in rows if (r.get("adjusted_twin") or {}).get("growth_display")
             and r.get("unit") == "bps"][:2]
    seen = set()
    pairs += [r for r in list(rows) + list(more) if r not in pairs and r.get("unit") in ("pct", "%")
              and not (r.get("ltcode") in seen or seen.add(r.get("ltcode")))
              and isinstance((r.get("adjusted_twin") or {}).get("latest_growth"), (int, float))
              and isinstance(r.get("latest_growth"), (int, float))
              and abs(r["latest_growth"] - r["adjusted_twin"]["latest_growth"]) >= 3.0][:2]
    if not pairs:
        return ""
    return ('<p class="note"><b>Reported and adjusted.</b> '
            + "; ".join(esc(r.get("display") or r["name"]) + " " + fgrowth(r["latest_growth"], r["unit"])
                        + " y/y, " + esc(r["adjusted_twin"]["display"]) + " "
                        + esc(r["adjusted_twin"]["growth_display"]) for r in pairs) + ".</p>")


def one_time_note(p):
    items = p.get("one_time_items") or []
    if not items:
        return ""
    def _one(o):
        bp = (", " + esc(o["revenue_bps"]) + " bps of revenue") if o.get("revenue_bps") is not None else ""
        ya = (" (" + esc(o.get("year_ago_period")) + ": " + esc(o["year_ago_display"])
              + ((", " + esc(o["year_ago_revenue_bps"]) + " bps") if o.get("year_ago_revenue_bps") is not None else "")
              + ")"
              if o.get("year_ago_display") else
              (" (none in " + esc(o.get("year_ago_period")) + ")" if o.get("year_ago_period") else ""))
        return esc(o.get("display") or o.get("name")) + " " + esc(o.get("latest_display")) + bp + ya
    return ('<p class="note"><b>One-time items the company reports this quarter:</b> '
            + "; ".join(_one(o) for o in items) + '. Each is sized against revenue: an item '
            'worth N bps of revenue moves a reported margin or cost ratio by about N bps. '
            'Adjusted lines exclude them; where both are ranked, the page shows the two '
            'side by side.</p>')


def cash_period_note(p, rows):
    cash = [r for r in rows if r.get("period_basis") == "quarterly" and r.get("period_evidence")]
    unv = [r for r in rows if r.get("period_basis") == "unverified"]
    out = ""
    if cash:
        out += ('<p class="note"><b>Quarterly, not year-to-date.</b> '
                + "; ".join(esc(r.get("display") or r["name"]) + ": " + esc(r["period_evidence"])
                            for r in cash[:3]) + ".</p>")
    if unv:
        out += ('<p class="note"><b>Period unverified.</b> '
                + "; ".join(esc(r.get("display") or r["name"]) for r in unv[:3])
                + (" rises" if len(unv) == 1 else " rise")
                + " through every fiscal year in the window, so the series cannot show whether "
                "it is quarterly or year-to-date; the year-over-year rate compares like with like "
                "either way, the level may not be a single quarter.</p>")
    return out


def large_move_note(rows):
    big = [r for r in rows if r.get("kind") == "level" and r.get("latest_growth") is not None
           and abs(r["latest_growth"]) >= LARGE_MOVE_PCT]
    if not big:
        return ""
    def _src(r):
        kind = ("calculated by InSync" if r.get("provenance") == "derived"
                else "company-reported" if r.get("provenance") in ("filed", "spoken") else "as returned")
        _d, _n = str(r.get("display") or r["name"]), str(r.get("name") or "")
        if re.sub(r"[^a-z0-9]", "", _d.lower()) == re.sub(r"[^a-z0-9]", "", _n.lower()):
            return "&ldquo;" + esc(_n) + "&rdquo; is " + kind
        return (esc(_d) + " is the source line &ldquo;" + esc(_n) + "&rdquo;, " + kind)
    return ('<p class="note"><b>Large moves, by source.</b> ' + "; ".join(_src(r) for r in big) + ".</p>")


LARGE_MOVE_PCT = 50.0


def _basis_parts(rows):
    quals, order = {}, []
    for r in rows:
        q = r.get("basis_qualifier")
        if not q:
            continue
        if q not in quals:
            quals[q] = []
            order.append(q)
        quals[q].append(r.get("display") or r["name"])
    renamed = []
    for r in rows:
        _d, _n = (r.get("display") or ""), (r.get("name") or "")
        if _d and _n and _d != _n and _d not in renamed:
            renamed.append((_d, _n))
    calc = [r.get("display") or r["name"] for r in rows
            if r.get("provenance") == "derived"]
    bridges = [(r.get("display") or r["name"], r["bridge"]["display"])
               for r in rows if r.get("bridge")]
    _parts = []
    if quals or renamed or calc:
        def _qual_sentence(names, q):
            m = re.match(r"adjusted:\s*excludes\s+(.*)", q)
            if m:
                return ("<b>" + esc(_and_list(names)) + "</b> "
                        + ("excludes " if len(names) == 1 else "exclude ")
                        + esc(m.group(1)) + ".")
            return "<b>" + esc(_and_list(names)) + "</b>: " + esc(q) + "."
        _parts = [_qual_sentence(quals[q], q) for q in order]
        if renamed:
            _parts.append("Shown under the source's own line item: "
                          + esc("; ".join("%s is %s" % (d, n) for d, n in renamed[:4]))
                          + ".")
        if calc:
            _parts.append("Calculated by InSync from reported lines rather than "
                          "reported by the company: " + esc(_and_list(calc)) + "."
                          + "".join(" " + esc(d) + ": " + esc(b) + "."
                                    for d, b in bridges))

    return quals, order, renamed, calc, _parts


def presentation_basis(p):
    rows = p.get("headline_moves") or []
    _q, _o, _r, _c, parts = _basis_parts(rows)
    if not parts:
        return ""
    return ('<p class="note"><b>Presentation basis of the headline figures.</b> '
            + " ".join(parts) + "</p>")


def _and_list(xs):
    xs = list(xs)
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def presentation_pointer(rows):
    quals, order, renamed, calc, parts = _basis_parts(rows)
    _inf = [str(x.get("display") or x.get("name")) for x in rows
            if x.get("polarity_basis") == "inferred"]
    if not parts and not _inf:
        return ""
    bits = []
    if _inf:
        bits.append("Read on pace for " + esc(_and_list(_inf)) + " assumes "
                    + ("the line is" if len(_inf) == 1 else "each line is")
                    + " revenue: the name carries no cost word and no rule declares its direction")
    adj = [n for q in order if re.search(r"adjust|one-time|ex-", q, re.I) for n in quals[q]]
    if adj:
        bits.append("Adjusted rows exclude items the company treats as one-time")
    _cat_m = any(x.get("template") == "machinery" and re.search(r"adjusted ebit margin",
                                                               str(x.get("display") or ""), re.I)
                 for x in rows)
    if _cat_m:
        calc = [c for c in calc if not re.search(r"adjusted ebit margin", str(c), re.I)]
    if calc:
        bits.append(esc(_and_list(calc)) + (" is" if len(calc) == 1 else " are")
                    + " calculated by InSync from reported lines")
    if any(x.get("template") == "machinery" and re.search(r"adjusted ebit margin",
                                                          str(x.get("display") or ""), re.I)
           for x in rows):
        bits.append("Adjusted EBIT Margin is InSync arithmetic on company-reported figures: "
                    "the adjusted EBIT line from the earnings release over revenue. The company "
                    "also publishes its own adjusted operating profit margin; the source does "
                    "not return it, so the two are not compared here")
    if not bits:
        return ""
    return ('<p class="note"><b>Basis.</b> ' + "; ".join(bits) + ".</p>")


def basis_label(r, diverging=()):
    q = r.get("qualified_by") or ""
    if q.startswith("material"):
        base = '<b>Inflection</b>'
    elif q.startswith("primary context"):
        base = '<span class="tag">Context</span>'
    else:
        base = "n/a"
    if r.get("ltcode") in diverging:
        base += ' <span class="tag" title="one leg of a cross-metric pair the engine flagged">Divergence</span>'
    return base


TIER_LABEL = {1: "Primary", 2: "Supporting", 3: "Contextual"}


def tier_badge(r):
    return ""


def _level_words(r, w):
    w = str(w or "")
    if r.get("kind") == "ratio":
        w = re.sub(r"\brising\b", "higher y/y", w)
        w = re.sub(r"\b(falling|improving)\b", "lower y/y", w)
    else:
        w = re.sub(r"\bfalling\b", "declining", w)
    return w[:1].upper() + w[1:]


def level_cell(r):
    w = r.get("level_word")
    if not w:
        return "Unchanged y/y" if r.get("level_direction") == "flat" \
            and r.get("latest_growth") is not None else "n/a"
    t = r.get("pace_only")
    out = esc(_level_words(r, t if t else w))
    at = r.get("adjusted_twin") or {}
    if at.get("level_word") and at["level_word"] != w:
        out += ('<br><span class="tag" title="the company\'s adjusted line, excluding '
                'items it treats as one-time: ' + esc(at.get("display")) + ' '
                + esc(at.get("growth_display")) + '">adjusted line: '
                + esc(_level_words(r, at["level_word"]).lower()) + '</span>')
    return out


def pace_tag(r):
    t = r.get("pace_only")
    if not t:
        return ""
    return (' <span class="tag" title="the implication grades the change in pace; '
            'the line itself still moved this way year over year">level ' + esc(t) + '</span>')


def imp_class(imp):
    return {"Favourable": "impf", "Unfavourable": "impu",
            "Mixed": "impm", "Ambiguous": "impa"}.get(imp or "", "impa")



def rank_table(rows, caption):
    if not rows:
        return ('<p class="note">' + esc(caption) +
                ': nothing cleared the materiality and confidence gates this quarter.</p>')
    head = ("<thead><tr><th>Metric</th><th>Latest</th><th>Year ago</th>"
            "<th>Current YoY Change</th><th>Prior Quarter's YoY Change</th>"
            "<th>Change in YoY Growth Pace</th><th>Streak</th>"
            "<th>Read on Pace</th><th>Current Level Direction</th></tr></thead>")
    body = "".join(
        "<tr>"
        '<td class="name">' + esc(r.get("display") or r["name"]) + flag_tags(r, with_grade=True) + "</td>"
        '<td class="num">' + flevel(r) + "</td>"
        '<td class="num">' + (as_level(r, r.get("prior_year_value"))
                              if r.get("unit_status") != "conflicted" else "withheld") + "</td>"
        '<td class="num">' + fgrowth(r["latest_growth"], r["unit"]) + "</td>"
        '<td class="num">' + fgrowth(r["prev_growth"], r["unit"]) + "</td>"
        '<td class="num">' + faccel(r["latest_accel"], r["unit"]) + "</td>"
        '<td class="num"><span class="stk">' + esc(r["streak"]) + "Q</span>"
        + sparkline(r["accel_series"][-8:]) + "</td>"
        '<td class="' + imp_class(r.get("implication")) + '">' + esc(r.get("implication")) + "</td>"
        "<td>" + level_cell(r) + "</td>"
        "</tr>" for r in rows)
    return ("<table>" + head + "<tbody>" + body + "</tbody></table>" + opposite_adjusted_note(rows)
            + small_base_note(rows) + promote_note(rows))


def promote_note(rows):
    bits = []
    for r in rows:
        pm = r.get("promote_match")
        if pm:
            bits.append(esc(r.get("display") or r.get("name")) + " rose " + esc(pm["increase_display"])
                        + " y/y, close to the " + esc(pm["promote_display"]) + " of promote revenue in "
                        + esc(pm["period"]) + " (strategic capital income including promotes less the "
                        "figure excluding them). The source does not split promotes by region, so the "
                        "page does not attribute the jump")
    return ('<p class="note"><b>Promote timing.</b> ' + "; ".join(bits[:1]) + ".</p>") if bits else ""


def small_base_note(rows):
    bits = []
    for r in rows:
        sb = r.get("small_base")
        if sb and sb.get("base_display") and sb.get("typical_display"):
            bits.append(esc(r.get("display") or r.get("name"))
                        + (": the year-ago " if sb.get("which") == "current"
                           else ": the base for last quarter\u2019s growth, the ")
                        + esc(sb["base_period"]) + " figure was " + esc(sb["base_display"])
                        + ", against about " + esc(sb["typical_display"])
                        + " in the same quarter of other years, so "
                        + ("the percentage mostly reflects the low base" if sb.get("kind") != "ratio"
                           else "the change is measured off an unusual quarter"))
    return ('<p class="note"><b>Small base.</b> ' + "; ".join(bits[:2]) + ".</p>") if bits else ""


def opposite_adjusted_note(rows):
    bits = []
    for r in rows:
        at = r.get("adjusted_twin") or {}
        if at.get("level_word") and r.get("level_word") and at["level_word"] != r["level_word"] \
                and at.get("growth_display"):
            bits.append(esc(r.get("display") or r["name"]) + " "
                        + fgrowth(r["latest_growth"], r["unit"]) + " y/y; the adjusted line, "
                        + esc(at.get("display")) + ", " + esc(at["growth_display"]))
    return ('<p class="note"><b>Reported and adjusted move apart.</b> ' + "; ".join(bits[:2])
            + ".</p>") if bits else ""


def heatmap_table(p):
    hm = p.get("heatmap") or []
    if TRIM["dropped"]:
        gone = set(TRIM["dropped"])
        hm = [r for r in hm if (r.get("display") or r.get("name")) not in gone] or hm
    if not hm:
        return ""
    periods = [c["period"] for c in hm[0]["cells"]]
    head = "<thead><tr><th>Metric</th>" + "".join(
        "<th>" + esc(x) + "</th>" for x in periods) + "</tr></thead>"
    rows = []
    for r in hm:
        unit = r.get("unit", "pct")
        cells = "".join(
            '<td class="cell" style="background:' + BIN_COLOR[c["bin"]]
            + (';color:#fff' if c["bin"] in ("vneg", "vpos") else '') + '" title="' +
            esc(BIN_LABEL[c["bin"]]) + ", " + esc(funusual(c["z"])) + '">' +
            ((frate(c.get("growth"), 0) if r.get("kind") == "pre_growth"
              else fgrowth(c.get("growth"), unit, 0)) if c.get("growth") is not None else "") +
            "</td>" for c in r["cells"])
        rows.append('<tr><td class="name">' + esc(r.get("display") or r["name"])
                    + ('<span class="mf"> (rate)</span>' if r.get("kind") == "pre_growth" else "") +
                    "</td>" + cells + "</tr>")
    legend = "".join('<i><span style="background:' + BIN_COLOR[b] + '"></span>' +
                     esc(BIN_LABEL[b]) + "</i>" for b in
                     ["vneg", "neg", "flat", "pos", "vpos"])
    return ('<table class="hm">' + head + "<tbody>" + "".join(rows) + "</tbody></table>"
            '<div class="legend">Each cell is that quarter&rsquo;s y/y growth (for a rate KPI, '
            'its reading), shaded by '
            'how far its change in pace was from the line&rsquo;s norm. Colour shows '
            'faster or slower growth, not good or bad.<br>' + legend + "</div>")



COMPACT = {"on": False}

GUIDE_ROWS_ON_PAGE = 14

TRIM = {"n": 0, "dropped": []}
REF = {"tight": 0}


def _trim_rows(rows, caption):
    if not TRIM["n"] or len(rows) <= 2:
        return rows
    order = sorted(range(len(rows)),
                   key=lambda i: (0 if (rows[i].get("tier") or 0) >= 3 else 1,
                                  rows[i].get("slot_priority",
                                              rows[i].get("priority", 0)),
                                  rows[i].get("ltcode") or ""))
    drop = set()
    for i in order:
        if len(drop) >= TRIM["n"] or len(rows) - len(drop) <= 2:
            break
        if (rows[i].get("tier") or 0) >= 3:
            drop.add(i)
    for i in sorted(drop):
        TRIM["dropped"].append(rows[i].get("display") or rows[i].get("name") or "")
    return [r for i, r in enumerate(rows) if i not in drop]
COMMENTARY_MAX = 420


def compact_css():
    return """
p.prose{font-size:9.9px;line-height:1.42;margin:0 0 5px;}
.dive{padding:1px 0 1px 10px;margin:8px 0;}
.dive .dm{font-size:8.4px;margin:2px 0 4px;}
.dive .dh{font-size:10.4px;}
.attrib{font-size:7.8px;margin:3px 0 0;}
.quote p{font-size:9.2px;} .quote{padding:3px 0 3px 9px;margin:5px 0 0;}
.quote .attr{font-size:7px;margin-top:2px;}
.fwd{font-size:8.2px;padding:4px 7px;margin:4px 0 0;}
.label{margin-top:15px;font-size:9.4px;} .rule{margin:2px 0 7px;}
tbody td{font-size:8.6px;padding:2.8px 5px;} thead th{font-size:7.2px;padding:2px 4px;letter-spacing:0;}
.mast{padding:7px 12px;} .mast .mf{font-size:8.4px;line-height:1.22;} .purpose{font-size:8.2px;margin:3px 0 4px;}
.takeaways .tsub{margin:2px 0 0;} .takeaways ul.pp li{font-size:9px;margin:0 0 1px;}
.takeaways{padding:9px 12px;margin:9px 0 3px;} .takeaways ul li{font-size:9.6px;margin:0 0 4px;}
.note{font-size:7.8px;} .scope{font-size:8.8px;padding-bottom:8px;margin:8px 0 0;}
.hm td.cell{font-size:7.6px;} .legend{font-size:7.4px;}
table.prov tbody td{font-size:6.8px;padding:2.2px 4px;}
.dvg{margin:6px 0;} .dvg .hl{font-size:9.4px;} .dvg .legs{font-size:8.5px;}
"""
REQUIRED_QUOTE_FIELDS = ("text", "speaker", "role", "quarter", "document",
                         "document_date", "document_id", "attributed_to_metric")



def decomposition_note(r):
    b = r.get("bridge") or {}
    if b.get("kind") != "decomposition":
        return ""
    return ('<p class="note"><b>Reconciles to total revenue:</b> ' + esc(b.get("display"))
            + ".</p>")


def year_ago_note(r):
    c = r.get("year_ago_comparison") or {}
    if not c.get("two_year_change_display") or not (
            c.get("which") == "weak"
            or (c.get("which") == "strong" and (r.get("latest_accel") or 0) < 0)):
        return ""
    lq = (r.get("period_series") or [""])[-1]
    return ('<p class="note"><b>' + ("Weak" if c["which"] == "weak" else "Strong")
            + ' year-ago comparison:</b> ' + esc(c["year_ago_period"]) + " was "
            + esc(c["year_ago_growth_display"]) + " y/y. Against " + esc(c["two_year_period"])
            + ((" (" + esc(c["two_year_level_display"]) + ")") if c.get("two_year_level_display") else "")
            + ", " + esc(lq) + " is " + esc(c["two_year_change_display"]) + ".</p>")


def reported_twin_note(r):
    c = r.get("calculated_twin")
    if c and c.get("latest_display"):
        _gm = c.get("gap_bps_max") or 0
        _gn = c.get("gap_bps_now") or 0
        return ('<p class="note"><b>' + esc(c.get("display")) + ':</b> '
                + esc(c["latest_display"]) + ", calculated by InSync from reported lines "
                "excluding items the company treats as one-time. It differs from the "
                "company-reported figure by " + str(_gn) + " bps this quarter"
                + ((" and by up to " + str(_gm) + " bps over the window") if _gm > _gn else "")
                + "; the reported figure is the one scored.</p>")
    t = r.get("reported_twin")
    if not t:
        return ""
    mx = t.get("gap_bps_max")
    return ('<p class="note"><b>Company-reported:</b> ' + esc(t.get("display"))
            + " " + esc(t.get("latest_display")) + ". The figure above is calculated "
            "by InSync" + (" excluding one-time items"
                           if "adjusted" in str(r.get("basis_qualifier") or "") else "")
            + "; the two differ by "
            + esc(t.get("gap_bps_now")) + " bps this quarter"
            + ((" and by up to " + esc(mx) + " bps over the window")
               if mx and mx > (t.get("gap_bps_now") or 0) else "") + ".</p>")

def commentary_block(r, commentary, p):
    if not commentary:
        return ""
    hit = commentary.get(r["ltcode"]) or commentary.get(r["name"])
    if not isinstance(hit, dict):
        return ""
    missing = [f for f in REQUIRED_QUOTE_FIELDS if not str(hit.get(f) or "").strip()]
    if missing:
        return ('<p class="note"><b>Management commentary withheld.</b> Attribution '
                'incomplete (' + esc(", ".join(missing)) + '), so the quotation is not shown.</p>')
    if str(hit.get("ticker") or p["ticker"]).upper() != p["ticker"].upper():
        return ('<p class="note"><b>Management commentary withheld.</b> The quotation is '
                'attributed to a different company.</p>')
    want = str(hit.get("attributed_to_metric") or "").strip().lower()
    have = str(r.get("display") or r["name"]).strip().lower()
    if want and want != have:
        return ('<p class="note"><b>Management commentary withheld.</b> The quotation was '
                'given about ' + esc(hit["attributed_to_metric"]) + ', not this line.</p>')
    if str(hit["quarter"]).upper() != p["latest_quarter"].upper():
        return ('<p class="note"><b>Management commentary withheld.</b> The quotation is from '
                + esc(hit["quarter"]) + ', not the scan quarter.</p>')
    text = str(hit["text"]).strip()
    cap = 260 if COMPACT["on"] else COMMENTARY_MAX
    if len(text) > cap:
        head = text[:cap]
        cut = max(head.rfind(". "), head.rfind("? "), head.rfind("! "))
        text = (head[:cut + 1] if cut > 100 else head.rsplit(" ", 1)[0] + ".") + " [quotation truncated]"
    page = (", p" + esc(hit["page"])) if str(hit.get("page") or "").strip() else ""
    _key = (" &middot; " + esc(hit["document_id"])
            if PROVENANCE.get("mode") == "full" else "")
    attr = (esc(hit["speaker"]) + ", " + esc(hit["role"]) + " &middot; " + esc(p["ticker"]) +
            " " + esc(hit["quarter"]) + " " + esc(hit["document"]) + " &middot; " +
            esc(hit["document_date"]) + page + _key)
    return ('<div class="quote"><p>' + esc(text) + "</p>"
            '<div class="attr">' + attr +
            " &middot; verified management commentary</div></div>")


def as_level(r, value):
    if value is None:
        return "n/a"
    return flevel({
        "display_value": value,
        "unit": r.get("unit"),
        "kind": r.get("kind"),
        "unit_label": r.get("unit_label"),
        "unit_decimals": r.get("unit_decimals", 2),
        "unit_status": r.get("unit_status"),
        "currency": r.get("currency"),
    })


def forward_bar(r):
    fb = r.get("forward_bar")
    if not fb or fb.get("level_to_hold_growth") is None:
        return ""
    unit = r["unit"]
    nb = esc(fb.get("next_base_period"))
    flat = fgrowth(fb.get("growth_if_level_flat"), unit)
    if r.get("unit_status") == "conflicted":
        return ('<p class="fwd"><span class="k">next quarter</span>level withheld, unit '
                'unresolved; ' + flat + ' y/y if unchanged from this quarter.</p>')
    hold = (("%.2f%%" % fb["level_to_hold_growth"]) if unit == "bps"
            else as_level(r, fb.get("level_to_hold_display",
                                    fb["level_to_hold_growth"])))
    return ('<p class="fwd"><span class="k">next quarter</span><b>' + hold
            + '</b> keeps the y/y rate at ' + fgrowth(r.get("latest_growth"), unit)
            + ' against ' + nb + '; flat q/q gives ' + flat + ' y/y.</p>')


VERDICT_CLASS = {"Favourable": "pos", "Unfavourable": "neg"}


def verdict_chip(r):
    imp = r.get("implication") or "n/a"
    cls = VERDICT_CLASS.get(imp, "neu")
    return ('<span class="vd ' + cls + '">' + esc(imp) + '</span>'
            '<span class="cf">data confidence: ' + esc(str(r.get("confidence") or "").lower()) + '</span>')


def measure_strip(r):
    K = lambda t: '<span class="k">' + t + "</span>"
    S = '<span class="sep">&#124;</span>'
    if r.get("kind") == "pre_growth":
        cur, prev, pq, ya, yq, dq, dy = _rate_parts(r)
        _rd = r.get("rate_decimals", 1)
        return S.join([K("latest") + "<b>" + rate_pair(r) + "</b>",
                       K("vs last q") + faccel(dq, "pct"),
                       K("vs year ago") + faccel(dy, "pct"),
                       K("historical unusualness") + esc(funusual(r["z_display"]))])
    parts = [
        K("y/y") + "<b>" + fgrowth(r["latest_growth"], r["unit"]) + "</b> ("
        + esc(prevq(r)) + ": " + fgrowth(r["prev_growth"], r["unit"]) + ")",
        K("change in pace") + faccel(r["latest_accel"], r["unit"]),
        K("historical unusualness") + esc(funusual(r["z_display"])),
        K("streak") + esc(r["streak"]) + "Q " + esc(r["streak_dir"] if r.get("kind") != "ratio"
                                                    else direction_word(r).lower()),
    ]
    return S.join(parts)


def sect(label, *body):
    inner = "".join(body)
    if not inner.strip():
        return ""
    return (SECT_MARK + '<div class="sect"><div class="label">' + label
            + '</div><div class="rule"></div>' + inner + "</div>")


def headline_label(p):
    n = len(p.get("headline_moves") or [])
    words = {1: "The", 2: "Two", 3: "Three", 4: "Four", 5: "Five"}
    if n == 1:
        return "Key Change In Growth Pace"
    return "Key Changes In Growth Pace"


def deep_dives(p, prose, commentary=None):
    out = []
    dives = p["deep_dive"][:4] if COMPACT["on"] else p["deep_dive"]
    attribs = ["; ".join(str(a) for a in (r.get("attribution") or []))
               for r in dives]
    WORD = {1: "One", 2: "Two", 3: "three", 4: "four", 5: "five"}
    cap = ((p.get("constants") or {}).get("deep_dive_rows")) or 3
    if 0 < len(dives) < cap:
        out.append('<p class="note">%s line%s below: the other material moves repeat '
                   'one of these and are in the tables above.</p>'
                   % (WORD.get(len(dives), str(len(dives))),
                      "" if len(dives) == 1 else "s"))
    _NOBRK = "No disclosed breakdown"
    attribs = ["" if a.startswith(_NOBRK) else a for a in attribs]
    uniform = len(set(attribs)) == 1 and len(dives) > 1 and attribs[0]
    if uniform:
        out.append('<p class="note" id="dvr"><b>Drivers, every line below.</b> '
                   + esc(attribs[0]) + ".</p>")
    for i, r in enumerate(dives, 1):
        text = prose.get("dd" + str(i), "")
        body = ('<p class="prose">' + esc(text) + "</p>") if text else (
            '<p class="note">Narrative not supplied for this metric.</p>')
        _at = [a for a in (r.get("attribution") or []) if not str(a).startswith(_NOBRK)]
        per_dive = "" if (uniform or not _at) else (
            '<p class="attrib"><b>Drivers.</b> ' + " ".join(esc(str(a).rstrip(".")) + "." for a in _at)
            + "</p>")
        if r.get("non_operating_window"):
            per_dive += ('<p class="attrib nowin"><b>Below operating profit.</b> '
                         + non_operating_window_text(r["non_operating_window"]) + '</p>')
        out.append('<div class="dive"><div class="dh">'
                   '<span class="nm">' + esc(r.get("display") or r["name"]) +
                   tier_badge(r) + flag_tags(r) + '</span>'
                   '<span class="rt">' + verdict_chip(r) + pace_tag(r) + '</span>' +
                   '</div><div class="dm">' + measure_strip(r) + "</div>" + body +
                   per_dive + reported_twin_note(r) + year_ago_note(r) + decomposition_note(r)
                   + _aq_dive(r)
                   + commentary_block(r, commentary, p) +
                   "</div>")
    return "".join(out) + more_from_management(p, commentary)


def more_from_management(p, commentary):
    if not commentary:
        return ""
    _dd = {r.get("ltcode") for r in (p.get("deep_dive") or [])}
    shown = {}
    for _bk in ("headline_moves", "primary_context", "quiet_headline", "rate_kpis",
                "accelerating", "decelerating"):
        _rs = p.get(_bk) or []
        for r in (_rs if isinstance(_rs, list) else []):
            if isinstance(r, dict):
                shown.setdefault(r.get("ltcode"), r.get("display") or r.get("name"))
    for c in ((p.get("quarter_strip") or {}).get("cells") or []):
        shown.setdefault(c.get("ltcode"), c.get("display") or c.get("name"))
    for g in ((p.get("guidance_check") or {}).get("revisions") or []):
        shown.setdefault(g.get("ltcode"), (g.get("display") or g.get("name") or "") + " guide")
    out = []
    for code, disp in shown.items():
        if code in _dd or not code:
            continue
        hit = commentary.get(code)
        if not isinstance(hit, dict):
            continue
        if any(not str(hit.get(f) or "").strip() for f in REQUIRED_QUOTE_FIELDS):
            continue
        if str(hit.get("attributed_to_metric") or "").strip().lower() != str(disp or "").strip().lower():
            continue
        if str(hit.get("quarter")).upper() != str(p["latest_quarter"]).upper():
            continue
        if str(hit.get("ticker") or p["ticker"]).upper() != p["ticker"].upper():
            continue
        out.append('<div class="mq"><b>' + esc(disp) + '.</b> '
                   + commentary_block({"ltcode": code, "name": disp, "display": disp}, commentary, p)
                   + '</div>')
        if len(out) >= 3:
            break
    if not out:
        return ""
    return ('<div class="dive"><div class="dh"><b>Also from management</b></div>' + "".join(out) + '</div>')



def divergence_block(p, prose):
    ds = (p.get("divergences") or [])[:3]
    if not ds:
        return ('<p class="note">No qualifying divergence between revenue, margin, earnings, '
                'cash, credit or segment series this quarter.</p>'
                + ('<p class="prose">' + esc(prose["divergence"]) + "</p>"
                   if prose.get("divergence") else ""))
    items = []
    for d in ds:
        legs = '<span class="sep">&#124;</span>'.join(
            '<span class="m">' + esc(display_or(m)) + "</span> "
            + (yy_pair(m, "growth", "prev_growth") if m.get("growth") is not None
               else faccel(m["accel"], m["unit"]) + " pace change")
            for m in d["metrics"])
        extra = ""
        if (d.get("bridge") or {}).get("text"):
            extra += '<div class="brg">' + esc(d["bridge"]["text"]) + "</div>"
        for _lm in (d.get("metrics") or []):
            if isinstance(_lm.get("bridge"), str):
                extra += '<div class="brg">Bridge: ' + esc(_lm["bridge"]) + ".</div>"
        if (d.get("cash_context") or {}).get("text"):
            extra += '<div class="brg">' + esc(d["cash_context"]["text"]) + "</div>"
        items.append('<div class="dvg"><div class="hl">' + esc(d["headline"]) +
                     '</div><div class="legs">' + legs + "</div>" + extra + "</div>")
    prose_html = ('<p class="prose">' + esc(prose.get("divergence", "")) + "</p>"
                  if prose.get("divergence") else "")
    return "".join(items) + prose_html


def display_or(m):
    return m.get("display") or m.get("name") or m.get("ltcode")


def cash_block(p, commentary=None):
    cc = p.get("cash_conversion") or {}
    if not cc.get("triggered"):
        return ('<p class="note">Cash-conversion cross-check not triggered: ' +
                esc(cc.get("reason", "revenue did not accelerate")) + ".</p>")
    _lq = esc(p.get("latest_quarter") or "")
    _cash_rows = [r for r in (cc.get("rows") or [])
                  if r.get("label") in ("Operating Cash Flow", "Free Cash Flow",
                                        "Capital Expenditure")]
    _proven = bool(_cash_rows) and all(r.get("period_basis") == "quarterly" for r in _cash_rows)
    head = ("<thead><tr><th>Panel line</th><th>Latest (" + _lq + ")</th>"
            "<th>Year ago</th>"
            "<th>Y/Y</th><th>Vs revenue growth</th><th>Read</th></tr></thead>")

    def _cash_lvl(v, unit):
        return v if v else "n/a"

    body = "".join(
        "<tr>"
        '<td class="name">' + esc(r.get("display") or r["label"])
        + (' <span class="mf">(three months)</span>' if (_proven and r.get("label") in
           ("Operating Cash Flow", "Free Cash Flow", "Capital Expenditure")) else
           (' <span class="mf">(period end)</span>' if re.search(
               r"receivable|inventor|payable|deferred revenue|balance", str(r.get("display") or r.get("label") or ""), re.I)
            and not re.search(r"days", str(r.get("display") or r.get("label") or ""), re.I) else ''))
        + "</td>"
        '<td class="num">' + esc(_cash_lvl(r.get("level_display"), None)) + "</td>"
        '<td class="num">' + esc(_cash_lvl(r.get("prior_level_display"), None)) + "</td>"
        '<td class="num">' + (fgrowth(r.get("latest_growth"), r.get("unit", "pct"))
                              if r.get("latest_growth") is not None else "n/a") + "</td>"
        '<td class="num">' + (faccel(r.get("gap_versus_revenue"), "pct")
                              if r.get("gap_versus_revenue") is not None else "n/a") + "</td>"
        "<td>" + esc(r.get("status")) + "</td></tr>" for r in cc.get("rows", []))
    nd = cc.get("not_disclosed") or []
    nd_txt = (" Not in this scan: " + esc(", ".join(nd)) + ".") if nd else ""
    q = cc.get("quality_note")
    qh = ('<p class="prose">' + esc(q) + "</p>") if q else ""
    tbl = ("<table>" + head + "<tbody>" + body + "</tbody></table>") if body else ""
    cm_html = "".join(
        commentary_block({"ltcode": r.get("ltcode"), "name": r.get("label"),
                          "display": r.get("display") or r.get("label")},
                         commentary, p)
        for r in cc.get("rows", []) if commentary and r.get("ltcode") in commentary)
    _ev = next((r for r in _cash_rows if r.get("label") in ("Operating Cash Flow", "Free Cash Flow")
                and r.get("period_basis") == "quarterly" and r.get("period_evidence")), None) \
        or next((r for r in _cash_rows if r.get("period_basis") == "quarterly"
                 and r.get("period_evidence")), None)
    _unv = [r for r in _cash_rows if r.get("period_basis") == "unverified"]
    ev_txt = ((' Quarterly, not year-to-date: ' + esc(_ev.get("display") or _ev.get("label"))
               + ' ' + esc(_ev["period_evidence"]) + '.') if _ev else "")
    if _unv:
        ev_txt += (' Period unverified: ' + esc(", ".join(str(r.get("display") or r.get("label")) for r in _unv))
                   + ' rise' + ('s' if len(_unv) == 1 else '') + ' through every fiscal year, so the '
                   'series cannot show whether ' + ('it is' if len(_unv) == 1 else 'they are')
                   + ' quarterly or year-to-date.')
    return (tbl + '<p class="note">' + esc(cc.get("conclusion", "")) + nd_txt + ev_txt
            + "</p>" + cm_html + qh)


def guide_display(row):
    raw = row.get("guide_text")
    mid = row.get("guide_midpoint")
    if mid is None:
        return '<span title="' + esc(raw) + '">' + guide_text(raw) + "</span>"
    unit = row.get("unit", "pct")
    shown = (("%.2f%%" % mid) if unit == "bps"
             else flevel({"latest_value": mid, "unit": unit}))
    band = re.search(r"(\u00b1|\+/-)\s*([\d.]+\s*%?)", str(raw or ""))
    if band:
        shown += " \u00b1 " + band.group(2)
    elif re.search(r"\bto\b|(?<=[\d%])\s*-\s*(?=[\d$])", str(raw or "")):
        shown += " (midpoint)"
    return '<span title="' + esc(raw) + '">' + esc(shown) + "</span>"


UNCOMPARED_REASON = {
    "line_not_in_scan": "line not in this scan",
    "line_not_scoreable": "too little history to compare",
    "qualitative_guide": "worded guide, quoted as given",
    "growth_form_not_comparable": "guide is a growth rate; the line moves in bps",
    "level_withheld": "level withheld",
    "scale_mismatch": "guide and reported figure on different scales",
    "actual_is_calculated": "the scan's figure is an InSync calculation, not the company's",
}


def declines_sentence(g):
    rows = [n for n in (g.get("declines_to_guide") or []) if n.get("text")]
    if not rows:
        return ('<p class="note"><b>The company states it does not guide.</b> '
                'Management is on record declining to provide guidance, so there '
                'is nothing to compare the reported history against. The '
                'inflections stand on reported history alone.</p>')
    latest = rows[0]
    return ('<p class="note"><b>The company states it does not guide.</b> '
            'This is management\'s own position, not a gap in the scan: nothing '
            'is withheld from this note and nothing is outstanding. The '
            'inflections stand on reported history alone, which for a '
            'non-guiding company is the only evidence there is.</p>'
            '<div class="quote src-text"><p>' + esc(str(latest["text"])) + "</p>"
            '<div class="attr">' + esc(latest.get("label") or "Guide note")
            + " &middot; given with " + esc(latest.get("source_period"))
            + " &middot; InSync guidance note, source text verbatim</div></div>")


def _ranked_codes(p):
    return {r.get("ltcode")
            for k in ("headline_moves", "accelerating", "decelerating", "deep_dive",
                      "monitor", "primary_context", "quiet_headline", "rate_kpis")
            for r in (p.get(k) or [])}


def delivered_block(g, heading=None, ranked=None):
    rows = g.get("delivered") or []
    notes = [n for n in (g.get("outlook_notes") or [])
             if n.get("targets_latest") and n.get("note_value") is not None]
    if not rows and not notes:
        return ""
    head = ("<thead><tr><th>Metric</th><th>Guided for " + esc(g.get("latest_quarter") or "")
            + "</th><th>Reported</th><th>Versus guide</th></tr></thead>")
    body = "".join(
        "<tr>"
        '<td class="name">' + esc(r.get("display") or r["name"]) + "</td>"
        '<td class="num"><span title="' + esc(str(r.get("guide_text") or "")) + '">'
        + esc(r.get("guide_display_text") or ("qualitative" if r.get("guide_kind") == "qualitative" else "")) + "</span></td>"
        '<td class="num">' + esc(r.get("actual_display") or "n/a") + "</td>"
        "<td>" + esc(r.get("versus_display") or "") + "</td></tr>" for r in rows)
    body += "".join(
        "<tr>"
        '<td class="name">' + esc(n.get("matched_display") or "") + "</td>"
        '<td class="num"><span title="' + src_text(n.get("text") or "") + '">'
        + esc(n.get("note_value_display") or "") + "</span></td>"
        '<td class="num">' + esc(n.get("matched_level_display") or "") + "</td>"
        "<td>" + esc(n.get("matched_gap_display") or "") + "</td></tr>"
        for n in notes if n.get("matched_gap_display"))
    src = [n for n in notes if n.get("matched_gap_display") and n.get("text")]
    foot = ""
    if src:
        foot = ('<p class="note">Read from a guidance note: '
                + "; ".join('<span class="src-text">&ldquo;' + esc(str(n["text"]))
                            + '&rdquo;</span>' for n in src[:2])
                + ".</p>")
    cap = ('<div class="sublab">' + esc(heading) + "</div>") if heading else ""
    if ranked is not None:
        _off = [r for r in rows if r.get("ltcode") not in ranked]
        if _off and len(_off) == len(rows):
            foot += ('<p class="note">Neither of these lines is among the '
                     'inflections this note ranks.</p>' if len(_off) > 1 else
                     '<p class="note">This line is not among the inflections this '
                     'note ranks; it is shown because the company guided it and '
                     'the quarter has reported, which makes it checkable.</p>')
        elif _off:
            foot += ('<p class="note">Not among the inflections this note ranks: '
                     + esc(", ".join(str(r.get("display") or r["name"])
                                     for r in _off[:3])) + ".</p>")
    return cap + "<table>" + head + "<tbody>" + body + "</tbody></table>" + foot


def forward_block(g, prose, heading=None):
    _RECF = re.compile(r"\brec\b|- eps - rec|reconcil", re.I)
    _sc = [r for r in (g.get("scored") or [])
           if not _RECF.search(str(r.get("name") or "") + " " + str(r.get("display") or ""))]
    _lv = {re.sub(r"\s+growth$", "", str(r.get("display") or r.get("name") or ""), flags=re.I).lower(): r
           for r in _sc if not re.search(r"growth$", str(r.get("display") or r.get("name") or ""), re.I)}
    scored = []
    for r in _sc:
        d = str(r.get("display") or r.get("name") or "")
        base = re.sub(r"\s+growth$", "", d, flags=re.I).lower()
        tw = _lv.get(base) if re.search(r"growth$", d, re.I) else None
        if tw is not None and tw.get("guide_implied_growth") is not None \
                and r.get("guide_implied_growth") is not None \
                and abs(tw["guide_implied_growth"] - r["guide_implied_growth"]) <= 1.0:
            continue
        scored.append(r)
    scored = sorted(scored, key=lambda o: (_guide_importance(o) if _guide_importance(o) is not None else 50))
    _fcap = max(3, 7 - 2 * REF["tight"])
    _fcut = max(0, len(scored) - _fcap)
    scored = scored[:_fcap]
    fwd_unc = sorted([o for o in (g.get("forward_uncompared") or [])
                      if not _RECF.search(str(o.get("name") or "") + " " + str(o.get("display") or ""))
                      and not re.search(r"^change in bps|^change in .*margin$", str(o.get("name") or "") + "|" + str(o.get("display") or ""), re.I | re.M)],
                     key=lambda o: (_guide_importance(o) if _guide_importance(o) is not None else 50))
    notes = [n for n in (g.get("outlook_notes") or [])
             if n.get("targets_next") and n.get("note_value_display")]
    nq = esc(g.get("next_quarter") or "the next quarter")
    if not (scored or fwd_unc or notes):
        return ""
    parts = [('<div class="sublab">' + esc(heading) + "</div>") if heading else ""]
    if scored or notes or fwd_unc:
        head = ("<thead><tr><th>Metric</th><th>Latest y/y</th>"
                "<th>Guide for " + nq + "</th><th>Implied y/y</th>"
                "<th>Guide implication</th></tr></thead>")
        body = "".join(
            "<tr>"
            '<td class="name">' + esc(r.get("display") or r["name"]) + "</td>"
            '<td class="num">' + fgrowth(r.get("latest_growth"), r.get("unit", "pct")) + "</td>"
            '<td class="num"><span title="' + esc(str(r.get("guide_text") or "")) + '">'
            + esc(r.get("guide_display_text") or ("qualitative" if r.get("guide_kind") == "qualitative" else "")) + "</span></td>"
            '<td class="num">' + fgrowth(r.get("guide_implied_growth"),
                                         r.get("unit", "pct")) + "</td>"
            "<td>" + ("n/m" if r.get("verdict") == "unparsed" else
                      esc(r.get("verdict_display") or r["verdict"]).capitalize())
            + "</td></tr>" for r in scored)
        def _worded(o):
            t = str(o.get("guide_text") or "")
            if re.search(r"(lower|higher|above|below|less|more)\s+than\s+[\d.]+\s*%|"
                         r"\b(above|below)\s+[\d.]+\s*%", t, re.I):
                return "worded guide, quoted as given"
            if re.search(r"\bhigher\b|increase|\bup\b|growth|stronger|expand", t, re.I):
                return "higher y/y, in the company's words"
            if re.search(r"\blower\b|decrease|\bdown\b|decline|weaker|contract", t, re.I):
                return "lower y/y, in the company's words"
            return esc(UNCOMPARED_REASON.get(o.get("reason"), "not comparable"))
        body += "".join(
            "<tr>"
            '<td class="name">' + esc(o.get("display") or o["name"]) + "</td>"
            '<td class="num">' + (fgrowth(o.get("latest_growth"), o.get("unit", "pct"))
                                  if o.get("latest_growth") is not None else "&ndash;") + "</td>"
            '<td class="num">' + (esc(o["guide_display_text"]) if o.get("guide_display_text")
                                  else '<span class="src-text">' + src_text(o.get("guide_text") or "")
                                  + "</span>") + "</td>"
            '<td class="num">&ndash;</td>'
            "<td>" + _worded(o)
            + "</td></tr>" for o in fwd_unc[:max(1, 4 - 2 * REF["tight"])])
        body += "".join(
            "<tr>"
            '<td class="name">' + esc(n.get("matched_display") or "") + "</td>"
            '<td class="num">' + esc(n.get("matched_level_display") or "") + "</td>"
            '<td class="num"><span title="' + src_text(n.get("text") or "") + '">'
            + esc(n.get("note_value_display") or "") + "</span></td>"
            '<td class="num">n/a</td>'
            "<td>company outlook, stated as a single figure</td></tr>"
            for n in notes[:2])
        parts.append("<table>" + head + "<tbody>" + body + "</tbody></table>")
        if _fcut or len(fwd_unc) > max(1, 4 - 2 * REF["tight"]):
            parts.append('<p class="note">Further guides for ' + nq + ' are retained in the audit record for this run.</p>')
        parts.append('<p class="note">Implied y/y works the guide out, at the midpoint '
                     'of a range, against the same year-ago base the reported rate '
                     'uses. A guide with no comparable figure is reproduced in the '
                     'company\'s own wording.</p>')
    return "".join(parts)


def guidance_block(p, prose, tables=True):
    g = dict(p.get("guidance_check") or {})
    g.setdefault("latest_quarter", p.get("latest_quarter"))
    scored = g.get("scored") or []
    notes = g.get("outlook_notes") or []
    other = g.get("other_horizon") or []
    fwd_unc = g.get("forward_uncompared") or []
    dlv_unc = g.get("delivered_uncompared") or []
    nq = esc(g.get("next_quarter") or "the next quarter")
    declined = (g.get("retrieval") or {}).get("state") == "declines_to_guide"
    if declined:
        parts = [declines_sentence(g)]
        notes = [n for n in notes if not n.get("declines")]
    else:
        parts = [delivered_block(g, ranked=_ranked_codes(p)) if tables else ""]
    if scored and tables:
        head = ("<thead><tr><th>Metric</th><th>Latest y/y</th>"
                "<th>Guide for " + nq + "</th><th>Implied y/y</th>"
                "<th>Guide implication</th></tr></thead>")
        body = "".join(
            "<tr>"
            '<td class="name">' + esc(r.get("display") or r["name"]) + "</td>"
            '<td class="num">' + fgrowth(r.get("latest_growth"), r.get("unit", "pct")) + "</td>"
            '<td class="num"><span title="' + esc(str(r.get("guide_text") or "")) + '">'
            + esc(r.get("guide_display_text") or ("qualitative" if r.get("guide_kind") == "qualitative" else "")) + "</span></td>"
            '<td class="num">' + fgrowth(r.get("guide_implied_growth"),
                                         r.get("unit", "pct")) + "</td>"
            "<td>" + ("n/m" if r.get("verdict") == "unparsed" else
                      esc(r.get("verdict_display") or r["verdict"]).capitalize())
            + "</td></tr>" for r in scored)
        parts.append("<table>" + head + "<tbody>" + body + "</tbody></table>")
        parts.append('<p class="note">Guides are the company\'s own as the provider '
                     'returned them; reported figures are the actuals every other table '
                     'here uses. Implied y/y is the guide, at the midpoint of a guided '
                     'range, worked out against the same '
                     'year-ago base the reported rate uses: a slower rate is ordinary '
                     'decay, not a disagreement, and a sign change is a reversal. A '
                     'guide the engine cannot compare is named, with the reason, rather '
                     'than approximated.</p>')
    elif not (declined or fwd_unc or notes or other or (g.get("delivered") or [])):
        st = (g.get("retrieval") or {}).get("state")
        if st == "not_retrieved":
            parts.append('<p class="note"><b>Retrieval gap, not a silent company.</b> '
                         'This scan returned no guidance rows at all, so whether '
                         'management guided ' + nq + ' is not established either way '
                         'here. The inflections stand on reported history alone.</p>')
        elif st == "declines_to_guide":
            parts.append(declines_sentence(g))
        else:
            parts.append('<p class="note">Guidance was returned for this company, but '
                         'none of it targets ' + nq + '; the inflections stand on '
                         'reported history alone.</p>')
    if fwd_unc and tables:
        parts.append('<p class="note"><b>Guided for ' + nq + ', not compared:</b> '
                     + "; ".join(
                         esc(o.get("display") or o["name"]) + ', "<span class="src-text">'
                         + src_text(o.get("guide_text") or "") + '</span>", because '
                         + esc(UNCOMPARED_REASON.get(o.get("reason"), "it is not comparable"))
                         for o in fwd_unc[:4])
                     + ".</p>")
    if not tables and other:
        _rank = {}
        for _k in ("headline_moves", "deep_dive", "monitor", "accelerating",
                   "decelerating", "primary_context"):
            for _r in (p.get(_k) or []):
                _rank.setdefault(_r.get("ltcode"), len(_rank))
        _imp = _guide_importance
        _revmap = {(r.get("ltcode"), str(r.get("target_period"))): r
                   for r in (g.get("revisions") or [])}
        _REC = re.compile(r"\brec\b|- eps - rec|reconcil", re.I)
        _flipped = {k for k, r in _revmap.items() if r.get("sign_changed_in_year")}
        _rel = [o for o in other
                if not _REC.search(str(o.get("name") or "") + " " + str(o.get("display") or ""))
                and (o.get("ltcode"), str(o.get("target_period"))) not in _flipped
                and (o.get("ltcode") in _rank or o.get("resolved_ltcode") in _rank
                     or _imp(o) is not None
                     or (o.get("ltcode"), str(o.get("target_period"))) in _revmap)]

        def _pos(o):
            c = o.get("ltcode") if o.get("ltcode") in _rank else o.get("resolved_ltcode")
            return _rank.get(c, 999)
        _lq = str(p.get("latest_quarter") or "").upper()

        def _stale(o):
            return bool(o.get("source_period")) and str(o["source_period"]).upper() != _lq
        _rel.sort(key=lambda o: (_stale(o), (_imp(o) if _imp(o) is not None else 50),
                                 (o.get("ltcode"), str(o.get("target_period"))) not in _revmap,
                                 _pos(o), str(o.get("display"))))
        _grp, _merged = {}, []
        for o in _rel:
            k = (str(o.get("display") or o.get("name")), str(o.get("guide_display_text") or o.get("guide_text")),
                 str(o.get("source_period")))
            if re.fullmatch(r"[1-4]Q\d\d", str(o.get("target_period") or "")) \
                    and (o.get("ltcode"), str(o.get("target_period"))) not in _revmap:
                if k in _grp:
                    _grp[k].append(o)
                    continue
                _grp[k] = [o]
            _merged.append(o)
        for o in _merged:
            k = (str(o.get("display") or o.get("name")), str(o.get("guide_display_text") or o.get("guide_text")),
                 str(o.get("source_period")))
            if len(_grp.get(k, [])) > 1:
                _ps = [str(x.get("target_period")) for x in _grp[k]]
                o["_period_label"] = _ps[0] + " to " + _ps[-1] + " (each)"
        _rel = _merged
        _CAP = max(4, 7 - REF["tight"])
        _left = [o for o in _rel[_CAP:] if (o.get("ltcode"), str(o.get("target_period"))) in _revmap]

        _hist = g.get("guide_history") or {}

        def _change(o):
            r = _revmap.get((o.get("ltcode"), str(o.get("target_period"))))
            if not r:
                h = _hist.get("%s|%s" % (o.get("ltcode"), o.get("target_period")))
                if h and len(h.get("periods") or []) >= 2 and h.get("unchanged"):
                    return esc("Unchanged since " + str(h["periods"][0]))
                if h and len(h.get("periods") or []) >= 2:
                    return esc("Restated in words since " + str(h["periods"][0]))
                if not re.fullmatch(r"(19|20)\d{2}", str(o.get("target_period") or "")):
                    return "Not a full-year guide"
                return "No earlier guide retrieved"
            d = str(r.get("direction") or "")
            out = d[:1].upper() + d[1:]
            if r.get("changed_in"):
                out += " in " + str(r["changed_in"])
            _io = r.get("items_only")
            if _io:
                return esc(out + " from " + _as_range(str(r.get("first_display") or ""))
                           + " to include one-time items" + ((" (" + _io["gap_display"] + " at the midpoint)")
                                                            if _io.get("gap_display") else "")
                           + "; " + str(_io.get("adjusted_display")) + " guide unchanged")
            out += " from " + _as_range(str(r.get("first_display") or r.get("first_text") or ""))
            if r.get("back_to"):
                out += ", back to the %s guide" % r["back_to"]
            if r.get("worded"):
                out += " (in the company's words)"
            _rng = " to " in str(r.get("first_display") or "") + str(r.get("latest_display") or "")
            _mw = "midpoint " if _rng else ""
            if r.get("midpoint_change_bps") is not None:
                _b = r["midpoint_change_bps"]
                _b = math.floor(abs(_b) + 0.5) * (1 if _b >= 0 else -1)
                out += " (%s%+d bps)" % (_mw, _b)
            elif r.get("midpoint_change_pct") is not None:
                out += " (%s%+.1f%%)" % (_mw, r["midpoint_change_pct"])

            return esc(out)
        if _rel:
            _body = "".join(
                "<tr>"
                '<td class="name">' + esc(o.get("display") or o["name"]) + "</td>"
                "<td>" + esc(o.get("_period_label") or o.get("target_period")) + "</td>"
                '<td class="num">' + (esc(o["guide_display_text"]) if o.get("guide_display_text")
                                      else ('<span class="src-text">' + src_text(o.get("guide_text") or "")
                                            + "</span>")) + "</td>"
                "<td>" + esc(o.get("source_period"))
                + ((' <span class="tag">not repeated in ' + esc(p.get("latest_quarter")) + "</span>")
                   if o.get("source_period") and p.get("latest_quarter")
                   and str(o.get("source_period")).upper() != str(p.get("latest_quarter")).upper()
                   else "") + "</td>"
                "<td>" + _change(o) + "</td></tr>"
                for o in _rel[:_CAP])
            _lev = {e.get("ltcode"): e for e in (p.get("label_evidence") or [])}
            _gl = []
            for o in _rel[:_CAP]:
                e = _lev.get(o.get("ltcode"))
                if e and e.get("display") and e["display"] != (o.get("display") or ""):
                    _gl.append("the %s guide is filed on the same line as %s"
                               % (o.get("display") or o.get("name"), e["display"])
                               + ("; the line excludes an item the company's guide may include, "
                                  "so the two are not compared" if re.search(r"\bexcl", e["display"], re.I)
                                  else ""))
            _glnote = ('<p class="note"><b>Label.</b> ' + esc("; ".join(_gl))[:1].upper()
                       + esc("; ".join(_gl))[1:] + ".</p>") if _gl else ""
            parts.append('<div class="sublab">A. Formal company guidance</div>')
            if (g.get("scored") or g.get("forward_uncompared")):
                parts.append('<p class="note">Guides for ' + nq + ' are tested against their '
                             'year-ago base in the Company outlook table on the reference page.</p>')
            parts.append('<p class="note"><b>Change this year</b> compares the latest guide with the one it replaced; guides in words are quoted as given.</p>'
                         "<table><thead><tr><th>Guided line</th><th>For</th>"
                         "<th>Latest guide</th><th>Given with</th><th>Change this year</th></tr></thead>"
                         "<tbody>" + _body + "</tbody></table>" + _glnote
                         + (('<p class="note"><b>Also changed this year:</b> '
                             + esc(", ".join(str(o.get("display") or o.get("name")) for o in _left[:8]))
                             + (" and others" if len(_left) > 8 else "")
                             + "; the full list is retained in the audit record for this run.</p>") if _left else ""))
    parts.append(component_check_note(g))
    parts.append(source_correction_note(p))
    if prose.get("guidance"):
        parts.append('<div class="sublab">Reading the guidance</div>')
        parts.append(bullets(prose["guidance"]))
    live = [n for n in notes
            if (n.get("targets_next") or n.get("targets_latest"))
            and not n.get("matched_gap_display")] if tables else []
    if True:
        _tol = " ".join(str(x.get("tolerance_source") or "")
                        for b in ("scored", "delivered", "other_horizon", "forward_uncompared",
                                  "delivered_uncompared", "revisions")
                        for x in (g.get(b) or []))
        _cq = {str(c.get(k) or "").strip() for c in (g.get("component_checks") or [])
               for k in ("cc_text", "fx_text", "ma_text", "total_text")}
        _hl = [n for n in notes if n.get("headline_annual") and n not in live
               and str(n.get("text") or "").strip() not in _tol
               and str(n.get("text") or "").strip() not in _cq]
        _hl.sort(key=lambda n: (0 if re.search(r"earnings per share|\beps\b|\bffo\b",
                                                str(n.get("label")) + " " + str(n.get("text")), re.I)
                                else 1, str(n.get("label") or "")))
        live = (live + _hl)[:4]
    if live:
        parts.append('<div class="sublab">B. Management commentary on the outlook</div>')
        parts.append(outlook_notes_block(live))
    elif not notes or not tables:
        pass
    elif not any(n.get("matched_gap_display") for n in notes):
        parts.append('<div class="sublab">B. Management commentary on the outlook</div>')
        parts.append(outlook_notes_block(notes))
    if (other or dlv_unc) and tables:
        seen, items = set(), []
        for o in list(other) + list(dlv_unc):
            lbl = ((o.get("display") or o["name"]) + " (" + str(o["target_period"]) + ")")
            if lbl in seen:
                continue
            seen.add(lbl)
            items.append(lbl)
        stale = g.get("superseded_quarterly_guides") or 0
        parts.append('<p class="note"><b>Other guides returned, on horizons this '
                     'quarterly base cannot test:</b> ' + esc("; ".join(items[:5]))
                     + ("; and " + esc(len(items) - 5) + " more" if len(items) > 5 else "")
                     + ("." if not stale else
                        ", plus " + esc(stale) + " superseded quarterly guide"
                        + ("" if stale == 1 else "s") + ".")
                     + " All are listed with their full strings in the guidance rows "
                     "table.</p>")
    if not "".join(parts).strip():
        st = (g.get("retrieval") or {}).get("state")
        if st == "not_retrieved":
            parts.append('<p class="note"><b>Retrieval gap, not a silent company.</b> '
                         'This scan returned no guidance rows at all, so whether '
                         'management guided ' + nq + ' is not established either way '
                         'here. The inflections stand on reported history alone.</p>')
        elif st == "declines_to_guide":
            parts.append(declines_sentence(g))
        elif st == "none_for_next_quarter":
            parts.append('<p class="note"><b>Guided, but not on this horizon.</b> '
                         'Management has given guidance, on annual and longer '
                         'horizons only; nothing returned targets ' + nq + ', so the '
                         'inflections stand on reported history alone. Every guide '
                         'returned is retained with its full string in the audit '
                         'record for this run.</p>')
        else:
            parts.append('<p class="note"><b>Guidance was returned and compared.</b> '
                         'The guide-by-guide tables are carried by the audit build of '
                         'this note and by the audit record for this run; this edition '
                         'reports the inflections rather than the comparison.</p>')
    return "".join(parts)


def _as_range(t):
    m = re.fullmatch(r"\s*(\S+)\s+to\s+(\S+)(\s+[A-Za-z]{1,4})?\s*", t or "")
    return ("a range of " + m.group(1) + " to " + m.group(2) + (m.group(3) or "")) if m else (t or "")


def source_correction_note(p):
    out = []
    for c in (p.get("source_corrections") or [])[:2]:
        m = str(c.get("metric") or "")
        if re.search(r"\(ex-?\s?1x items?\)", m, re.I):
            m = "adjusted " + re.sub(r"\s*\(ex-?\s?1x items?\)", "", m, flags=re.I).strip()
        parts = [str(c.get("fx_text") or "")] + ([str(c["ma_text"])] if c.get("ma_text") else [])
        out.append('<p class="note"><b>Source correction.</b> For ' + esc(c["target_period"])
                   + ", given with " + esc(c["source_period"]) + ", the source filed "
                   + esc(m) + " growth as " + esc(c["total_as_filed"]) + " and the currency-neutral "
                   "figure as " + esc(c["cc_as_filed"]) + ". With the currency and acquisition effects ("
                   + esc("; ".join(x for x in parts if x)) + ") the parts add up only with the two "
                   "exchanged, so the note reads the total as " + esc(c["cc_as_filed"])
                   + " and the currency-neutral figure as " + esc(c["total_as_filed"]) + ".</p>")
    return "".join(out)


def component_check_note(g):
    out = []
    for c in (g.get("component_checks") or [])[:2]:
        _m = str(c.get("metric") or "this line")
        if re.search(r"\(ex-?\s?1x items?\)", _m, re.I):
            _m = "adjusted " + re.sub(r"\s*\(ex-?\s?1x items?\)", "", _m, flags=re.I).strip()
        c = dict(c, metric=_m)
        def _lab(word, txt):
            return txt if re.search(word, txt, re.I) else (word.split("|")[0] + " " + txt)
        parts = ["currency-neutral " + c["cc_text"], _lab("currency", c["fx_text"])]
        if c.get("ma_text"):
            parts.append(_lab("acquisitions and divestitures|acquisition|divest|m&a", c["ma_text"]))
        out.append('<p class="note"><b>Guide components do not add up.</b> For '
                   + esc(c["target_period"]) + ", given with " + esc(c["source_period"])
                   + ", the company's growth components for " + esc(c.get("metric") or "this line")
                   + " (" + esc("; ".join(parts))
                   + ") sum to about " + esc(c["implied_display"]) + ", against its total guide of "
                   + esc(c["total_text"]) + ". The source does not say which figure, or which "
                   "sign, is wrong; the note prints both as returned.</p>")
    return "".join(out)


def outlook_notes_block(notes):
    parts = []
    if notes:
        parts.append('<p class="note"><b>Company guidance notes,</b> reproduced exactly '
                     'as returned and not copy-edited, so the page and the source say '
                     'the same thing.</p>')
        parts.append("".join(
            '<div class="quote src-text"><p>' + src_text(n["text"]) + "</p>"
            '<div class="attr">' + esc(n.get("label")) + " &middot; targeting " +
            esc(n.get("target_period")) + " &middot; given with " +
            esc(n.get("source_period")) +
            (" &middot; read as " + esc(n["note_value_display"]) + " for "
             + esc(n.get("matched_display") or "")
             if n.get("note_value_display") else "") +
            " &middot; InSync guidance note, source text verbatim</div></div>"
            for n in notes[:4]))
    return "".join(parts)


def monitor_table(p):
    rows = p.get("monitor") or []
    if not rows:
        return '<p class="note">No monitorable variable followed from this quarter.</p>'
    head = ("<thead><tr><th>What changed</th><th>Question for next quarter</th>"
            "<th>InSync continuation level</th></tr></thead>")
    body = "".join(
        "<tr>"
        '<td class="name">' + esc(r["variable"]) + "<br><span class=\"mf\">"
        + esc(r["current_fact"]) + "</span></td>"
        "<td>" + esc(r.get("question") or "") + "</td>"
        "<td>" + esc(r["next_proof"]) + "</td></tr>" for r in rows[:5])
    return "<table>" + head + "<tbody>" + body + "</tbody></table>"


def _context_rows(p):
    top = {r.get("ltcode") for r in (p.get("headline_moves") or [])}
    twins = {str((r.get("reported_twin") or {}).get("display") or "")
             for r in (p.get("headline_moves") or [])} - {""}
    return [r for r in (p.get("primary_context") or []) if r.get("ltcode") not in top
            and str(r.get("display") or "") not in twins]


def context_section(p):
    if not _context_rows(p):
        return ""
    return ('<div class="label">Large Moves Within Normal Range</div>'
            '<div class="rule"></div>' + context_table(p))


def context_table(p):
    rows = _context_rows(p)
    if not rows:
        return ""
    head = ("<thead><tr><th>Metric</th><th>Latest</th><th>Current YoY Change</th>"
            "<th>Prior Quarter's YoY Change</th><th>Change in YoY Growth Pace</th>"
            "<th>Historical Unusualness</th><th>Read on Pace</th></tr></thead>")
    body = "".join(
        "<tr>"
        '<td class="name">' + esc(r.get("display") or r["name"]) + flag_tags(r) + "</td>"
        '<td class="num">' + flevel(r) + "</td>"
        '<td class="num">' + fgrowth(r["latest_growth"], r["unit"]) + "</td>"
        '<td class="num">' + fgrowth(r["prev_growth"], r["unit"]) + "</td>"
        '<td class="num">' + faccel(r["latest_accel"], r["unit"]) + "</td>"
        "<td>" + esc(funusual(r["z_display"])) + "</td>"
        '<td title="' + esc(r.get("implication_reason") or "") + '">'
        + esc(r.get("implication")) + pace_tag(r) + "</td></tr>" for r in rows)
    return ('<table>' + head + "<tbody>" + body + "</tbody></table>"
            '<p class="note">Big moves in their own units that are ordinary for these '
            'lines, so they are shown here and not counted as inflections.'
            + (' <sup class="mk cm">*</sup> InSync calculation from company-reported figures; the company does not publish this exact number.'
               if any(x.get("provenance") == "derived" and not x.get("insync_calculated")
                      for x in rows) else "")
            + (' <sup class="mk">\u2020</sup> last quarter\u2019s growth rate was in the top '
               'or bottom quartile of this line\u2019s history.'
               if any(x.get("comp_flag") in ("easy_base", "hard_base") for x in rows) else "")
            + '</p>' + small_base_note(rows))


def why_unsigned(p):
    rows = ((p.get("accelerating") or []) + (p.get("decelerating") or [])
            + (p.get("primary_context") or []))
    seen, order, names = set(), [], {}
    generic = "direction is context dependent for this line"
    for r in rows:
        why = r.get("implication_reason")
        if (r.get("implication") not in ("Ambiguous", "Mixed") or not why
                or why == generic or r["ltcode"] in seen):
            continue
        seen.add(r["ltcode"])
        if why not in names:
            order.append(why)
            names[why] = []
        names[why].append(r.get("display") or r["name"])
    items = []
    for why in order:
        ns = names[why]
        label = ns[0] if len(ns) == 1 else ", ".join(ns[:-1]) + " and " + ns[-1]
        items.append("<b>" + esc(label) + ".</b> " + esc(why[:1].upper() + why[1:]) + ".")
    if not items:
        return ""
    return ('<p class="note"><b>Why these read as ambiguous.</b> ' +
            " ".join(items) + "</p>")


def marker_legend(p):
    rows = ((p.get("accelerating") or []) + (p.get("decelerating") or [])
            + (p.get("primary_context") or []) + (p.get("deep_dive") or []))
    used, defs = set(), []
    for r in rows:
        if r.get("small_line"):
            used.add("small")
        if r.get("comp_flag"):
            used.add("\u2020")
        if r.get("unit_status") == "validated" and r.get("unit_source") \
                and "reproduces this line" in str(r.get("unit_source")):
            used.add("unit-derived")
        if r.get("provenance") == "spoken":
            used.add("call-sourced")
        if re.search(r"financing activit|\bcff\b|debt issu|repayment|share repurchase|"
                     r"buyback|dividends? paid", str(r.get("name") or ""), re.I):
            used.add("capital structure")
        if r.get("direction_note") == "improving_from_decline":
            used.add("still declining")
        if r.get("provenance") == "derived":
            used.add("*")
    order = [
        ("*", "InSync calculation from company-reported figures; the company does not publish this exact number."),
        ("small", "the line is under one percent of quarterly revenue, so a large "
                  "percentage move is a small absolute one. Comparable currency "
                  "lines only; it is never applied to per-share amounts, counts "
                  "or ratios."),
        ("\u2020", "last quarter's growth rate was in the top or bottom quartile of "
                    "this line's history, so part of the change in pace is that comparison."),
        ("call-sourced", "at least one quarter came from an earnings call rather "
                         "than a filing."),
        ("capital structure", "the line moves with issuance, repayment, buybacks and "
                              "dividends; it is context for the balance sheet, not "
                              "operating momentum."),
        ("unit-derived", "the source label for this line disagreed with the value "
                         "returned, so the scale was taken from the issuer's own "
                         "arithmetic instead. The rate of change is unaffected; the "
                         "level rests on that inference and the confidence grade is "
                         "capped at moderate for it. The arithmetic used is named "
                         "in the methodology note."),
    ]
    for key, text in order:
        if key in used:
            defs.append("<b>" + esc(key) + ":</b> " + esc(text))
    trimmed = ""
    if TRIM["dropped"]:
        d = sorted(set(TRIM["dropped"]))
        trimmed = ('<p class="note"><b>Held out of the displayed tables</b> to meet the '
                   'page budget, contextual lines first: ' + esc("; ".join(d))
                   + ". The counts above are of every line that cleared the bars, not "
                   "of the rows shown; all are retained in the audit record.</p>")
    if not defs:
        return trimmed
    return ('<p class="note"><b>Marks.</b> ' + " ".join(defs) + "</p>") + trimmed


def quiet_line(p):
    rows = p.get("quiet_headline") or []
    if not rows:
        return ""
    def _quiet_phrase(r):
        name = r.get("display") or r["name"]
        val = fgrowth(r["latest_growth"], r["unit"])
        if r.get("flow_rate_mode"):
            stock = r.get("flow_denominator_name") or "balance"
            stock = re.sub(r"\s*[-–(]?\s*(eop|end[- ]of[- ]period)\s*\)?\s*$",
                           "", stock, flags=re.I).strip(" -–") or "balance"
            return (name + " ran at " + val + " of the prior quarter's closing " +
                    esc(stock))
        _p = r.get("prev_growth")
        if r.get("kind") == "pre_growth":
            _dp = r.get("rate_decimals", 1)
            return name + " " + frate(r.get("latest_growth"), _dp) + ((" (" + esc(r.get("prev_period") or "last Q")
                                                                    + ": " + frate(_p, _dp) + ")") if _p is not None else "")
        return name + " " + val + " y/y" + ((" (" + esc(r.get("prev_period") or "last Q") + ": "
                                           + fgrowth(_p, r["unit"]) + ")") if _p is not None else "")
    txt = "; ".join(_quiet_phrase(r) for r in rows[:5])
    return ('<p class="note"><b>Other key lines, change in pace within normal range:</b> '
            + txt + ".</p>")


SECTION_SHORT = {
    "headline_moves": "Top five", "accelerating": "Accel", "decelerating": "Decel",
    "deep_dive": "Dive", "quiet_headline": "Quiet", "heatmap": "Map",
    "monitor": "Monitor", "cash_conversion": "Cash", "divergences": "Cross",
    "guidance_check": "Guide",
}


def fmidpoint(g):
    v = g.get("parsed_midpoint")
    if v is None:
        return ""
    if g.get("parsed_unit") == "bps":
        return "%.2f%%" % v
    return flevel({"latest_value": v, "unit": "pct"})


def quote_footer(commentary):
    return (" Quotes are verbatim and fully attributed."
            if (commentary or {}) else "")


def source_records_block(p, commentary=None):
    sr = p.get("source_records") or {}
    guides = sr.get("guidance_rows") or []
    docs = list(sr.get("documents") or [])

    seen = {(d.get("document"), d.get("given_with")) for d in docs}
    for code, hit in sorted((commentary or {}).items()):
        if not isinstance(hit, dict):
            continue
        key = (hit.get("document_id") or hit.get("document"), hit.get("quarter"))
        if key in seen or not key[0]:
            continue
        seen.add(key)
        docs.append({"document": hit.get("document"),
                     "document_id": hit.get("document_id"),
                     "class": "transcript or release",
                     "given_with": hit.get("quarter"),
                     "date": hit.get("document_date"),
                     "page": hit.get("page"),
                     "used_for": "management commentary on "
                                 + str(hit.get("attributed_to_metric") or code)})
    if not docs and not guides:
        return ""

    out = []
    if docs:
        rows = "".join(
            "<tr>"
            '<td class="name">' + esc(d.get("document") or d.get("document_id")) + "</td>"
            '<td class="src">' + esc(d.get("document_id") or "identifier not returned") +
            (", p" + esc(d["page"]) if str(d.get("page") or "").strip() else "") + "</td>"
            "<td>" + esc(d.get("class")) + "</td>"
            "<td>" + esc(d.get("date") or d.get("given_with") or "date not returned") + "</td>"
            "<td>" + esc(d.get("used_for")) + "</td></tr>" for d in docs)
        out.append('<div class="label">Provenance: Documents Behind Quoted Material'
                   '</div><div class="rule"></div>')
        out.append('<table class="prov"><thead><tr><th>Document</th><th>Identifier</th>'
                   "<th>Class</th><th>Date or period</th><th>Used for</th></tr></thead>"
                   "<tbody>" + rows + "</tbody></table>")
    if guides:
        _code_col = PROVENANCE["mode"] == "full"
        _total = len(guides)
        guides = guides[:GUIDE_ROWS_ON_PAGE]
        rows = "".join(
            "<tr>"
            '<td class="name">' + esc(g.get("metric")) + "</td>"
            + ('<td class="src">' + (esc(g.get("ltcode")) if g.get("ltcode") else "none (an outlook note)")
               + "</td>" if _code_col else "")
            + "<td>" + esc(g.get("target_period")) + "</td>"
            "<td>" + esc(g.get("source_period")) + "</td>"
            '<td class="src"><span class="src-text">'
            + esc(g.get("raw_guide") or "text not returned") + "</span></td>"
            '<td class="num">' + (fmidpoint(g) if g.get("parsed_midpoint") is not None
                                  else esc(g.get("parse_status"))) + "</td>"
            "<td>" + esc(g.get("document") or "source not returned") + "</td>"
            "</tr>" for g in guides)
        out.append('<div class="label">Provenance: Guidance Rows As Returned</div>'
                   '<div class="rule"></div>')
        out.append('<table class="prov"><thead><tr><th>Guided metric</th>'
                   + ("<th>LTCODE</th>" if _code_col else "")
                   + "<th>Targets</th><th>Given with</th><th>Guide string as returned</th>"
                   "<th>Parsed midpoint</th><th>Source</th></tr></thead>"
                   "<tbody>" + rows + "</tbody></table>")
        if _total > len(guides):
            out.append('<p class="note">' + esc(_total - len(guides)) + ' further guidance '
                       + ('row was' if _total - len(guides) == 1 else 'rows were')
                       + ' returned, on horizons this note does not test. All '
                       + esc(_total) + ' are retained in the audit record with their full strings.</p>')
    out.append('<p class="note">The guide string is reproduced as the source returned it, '
               'beside the midpoint the engine parsed from it, so a parse can be checked '
               'against the text rather than taken on trust. Every quotation elsewhere in '
               'this note carries its document identifier and page inline.</p>')
    return "".join(out)


KEY_SOURCE_CAP = 12


def key_source_rows(p):
    prov = {r.get("ltcode"): r for r in (p.get("provenance") or [])}
    order, seen = [], set()

    def take(code):
        if code and code in prov and code not in seen:
            seen.add(code)
            order.append(prov[code])

    for r in (p.get("deep_dive") or []):
        take(r.get("ltcode"))
    for r in (p.get("headline_moves") or []):
        take(r.get("ltcode"))
    for r in ((p.get("cash_conversion") or {}).get("rows") or []):
        take(r.get("ltcode"))
    gc = p.get("guidance_check") or {}
    for bucket in ("scored", "delivered"):
        for r in (gc.get(bucket) or []):
            take(r.get("ltcode"))
    for d in (p.get("divergences") or []):
        for m in (d.get("metrics") or []):
            take(m.get("ltcode"))
    return order[:KEY_SOURCE_CAP]


def key_sources_table(p):
    rows = key_source_rows(p)
    if not rows:
        return ""
    spans = {(r.get("quarters"), r.get("first_period"), r.get("last_period"))
             for r in rows}
    one_span = len(spans) == 1

    def cell(r):
        if r is None:
            return '<td class="filler"></td><td class="filler"></td>'
        span = ("" if one_span else
                " <span class=\"mf\">(" + esc(r.get("quarters")) + " qtrs)</span>")
        return ('<td class="name">' + esc(r.get("display") or r.get("name")) + span
                + '</td><td>' + esc(_sourced_from(r)) + "</td>")

    half = (len(rows) + 1) // 2
    left, right = rows[:half], rows[half:] + [None] * (half - len(rows[half:]))
    head = ("<thead><tr><th>Metric</th><th>Figures are</th>"
            "<th>Metric</th><th>Figures are</th></tr></thead>")
    body = "".join("<tr>" + cell(a) + cell(b) + "</tr>"
                   for a, b in zip(left, right))
    if one_span:
        q, a, b = next(iter(spans))
        tail = ('<p class="note">Every line above was read for ' + esc(q)
                + ' quarters, ' + esc(a) + ' to ' + esc(b)
                + ', with no change of definition inside the window. The full scanned '
                'universe is retained in the audit record for this run.</p>')
    else:
        tail = ('<p class="note">These are the lines the note draws its conclusions '
                'from; the full scanned universe is retained in the audit record for this '
                'run.</p>')
    return ('<table class="keysrc">' + head + "<tbody>" + body
            + "</tbody></table>" + tail)


def _excl_clause(p, c):
    n = c.get("series_excluded") or 0
    if not n:
        return ""
    if n == 1:
        ex = (p.get("excluded") or [])
        why = str((ex[0].get("reason") if ex else "") or "").strip().lower()
        for pat, plain in (("derived twin", "a duplicate of another line"),
                           ("step series", "a series that is not continuous"),
                           ("unstable base", "an unstable year-ago comparison"),
                           ("no observation", "no reading in the latest quarter"),
                           ("modelled", "a modelled rather than reported figure")):
            if pat in why:
                return ", and one excluded as " + plain
        return ", and one excluded"
    cats = p.get("exclusion_categories") or []
    if not cats:
        return ", and " + esc(n) + " excluded"
    return (", and " + esc(n) + " excluded: "
            + "; ".join(esc(x["count"]) + " " + esc(x["category"]) for x in cats))


def currency_sentence(p):
    rc = p.get("reporting_currency") or {}
    sym = rc.get("symbol")
    if not sym:
        return ""
    other = [str(x) for x in (rc.get("mixed") or []) if x]
    if other:
        return ("<b>Currency.</b> Figures are shown in the currency the company reports in, "
                + esc(sym) + ", exactly as the source returned them; lines the source quotes in "
                + esc(_and_list(other)) + " are shown in that currency and carry its symbol. "
                "Nothing is converted.")
    return ("<b>Currency.</b> Every figure is shown in the currency the company "
            "reports in, " + esc(sym) + ", exactly as the source returned it; "
            "nothing is converted.")


def _overall_read(p):
    t = p.get("implication_counts") or {}
    cats = [(esc(n) + " " + k.lower()) for k, n in
            (("Favourable", t.get("Favourable", 0)), ("Unfavourable", t.get("Unfavourable", 0)),
             ("Mixed", t.get("Mixed", 0)), ("Ambiguous", t.get("Ambiguous", 0))) if n]
    if not cats or p.get("direction") == "No material inflection":
        return ""
    c = p.get("coverage") or {}
    pc = p.get("published_counts") or {}
    na, nd = pc.get("accelerating"), pc.get("decelerating")
    _tot = pc.get("total", c.get("material_total", 0))
    if na is not None and nd is not None:
        lead = (esc(na) + " accelerating, " + esc(nd) + " slowing. ")
    else:
        lead = esc(p.get("direction", "Mixed")) + " on pace. "
    return ("<b>Overall read.</b> Of the "
            + esc(_tot) + " lines with a large move that was also "
            "unusual for the line: " + lead + "By what the move means: "
            + ", ".join(cats) + ".")


def methodology_box(p):
    c, k = p["coverage"], p["constants"]
    derived = []
    seen = set()
    for key in ("headline_moves", "accelerating", "decelerating", "deep_dive"):
        for r in (p.get(key) or []):
            nm = r.get("display") or r.get("name")
            if (nm and nm not in seen
                    and "reproduces this line" in str(r.get("unit_source") or "")):
                seen.add(nm)
                derived.append(nm)
    parts = [
        "<b>Window.</b> " + esc(k["lookback_quarters"]) + " quarters, "
        + esc((p.get("scan_period") or {}).get("first")) + " to "
        + esc((p.get("scan_period") or {}).get("last")) + "; "
        + esc(k["min_quarters_to_rank"]) + " needed to score, and a line must "
        "report in the latest quarter.",
        "<b>Growth.</b> Year over year for the same quarter, in per cent for "
        "levels and basis points for ratios.",
        "<b>Change in YoY Growth Pace.</b> This quarter's y/y rate less last quarter's. "
        "A line can be growing and decelerating at once.",
        "<b>Historical Unusualness.</b> This quarter's change in pace measured in standard "
        "deviations (SD) of the line's own past quarter-to-quarter changes in pace, so lines "
        "in different units compare: 1 SD is a typical move; notable from 0.75 SD, unusual "
        "from 1.5 SD, very unusual from 2.5 SD. Size, not good or bad.",
        "<b>Columns.</b> Momentum Direction says whether growth sped up or slowed "
        "(for a ratio, which way it moved against a year ago and whether by more or "
        "less than last quarter); Read on Pace whether that change is favourable, "
        "unfavourable or ambiguous for the business; Current Level Direction which "
        "way the line itself moved year over year.",
        "<b>Labels.</b> A line with no mark is company-reported (earnings release or "
        "filing). An asterisk (*) marks a figure InSync derived by arithmetic from "
        "company-reported lines (a margin, a share of revenue). \u201cInSync-Calculated\u201d "
        "in a name marks a measure InSync constructs itself (its own adjustment, EBITDA, "
        "an average of period-end counts); where the company reports the same measure, "
        "the company's figure is scored and the calculation is shown beside it.",
        "<b>Rate KPIs.</b> A line that is itself a rate or spread (rent change, "
        "same-store NOI growth, organic growth) is compared with its own reading last "
        "quarter and a year ago, in points; it is not grown.",
        "<b>Level to hold.</b> The level next quarter that keeps the y/y rate "
        "unchanged against a year-ago figure already reported. Arithmetic, not a "
        "forecast or company guidance.",
        _overall_read(p),
        "<b>Rounding.</b> Rates computed on unrounded reported values and rounded "
        "once for display, so recomputing from the printed figures can differ in "
        "the last digit.",
        currency_sentence(p),
        "<b>Coverage.</b> " + esc(c["series_scanned"]) + " metrics retrieved, "
        + esc(c.get("series_ranked")) + " with enough history to score"
        + (_excl_clause(p, c) or "") + ".",
    ]
    if derived:
        parts.append(
            "<b>Unit normalisation.</b> " + esc(" and ".join(derived[:2]))
            + " carry a scale taken from the issuer's own arithmetic, the source "
            "label having disagreed with the value returned. Rates are unaffected; "
            "confidence on "
            + ("them is" if len(derived[:2]) > 1 else "it is") + " capped at "
            "moderate.")
    _ns = not_scored_note(p)
    if _ns:
        parts.append(re.sub(r'^<p class="note">|</p>$', "", _ns))
    return ('<div class="mbox"><div class="mh">Methodology</div>'
            + "".join('<p>' + x + "</p>" for x in parts if x) + "</div>")


def limitations_para(p):
    return ('<p class="note"><b>Limitations.</b> This note measures reported history '
            'against itself: no consensus, estimates or prices, so a line can inflect '
            'favourably and still miss expectations. A line not shown was not in the '
            'scan, which does not mean the company does not disclose it. Guidance is '
            'the company\u2019s own, as the provider returned it.</p>')


def dq_exceptions_note(p):
    groups, order = {}, []
    for e in p.get("excluded") or []:
        rs = str(e.get("reason") or "")
        if rs.startswith("Data-Quality Exception, Not Scored:"):
            nm = e.get("display") or e.get("name")
            nm = re.sub(r"^%\s*", "", str(nm or ""))
            why = rs.split(":", 1)[1].strip()
            if why not in groups:
                groups[why] = []
                order.append(why)
            groups[why].append(nm)
    out = []
    for why in order:
        ns = groups[why]
        lab = ns[0] if len(ns) == 1 else ", ".join(ns[:-1]) + " and " + ns[-1]
        out.append(esc(lab) + ": " + esc(why))
    if not out:
        return ""
    return ('<p class="note"><b>Data-quality exceptions, not scored.</b> '
            + "; ".join(out[:3]) + ".</p>")


def client_reference_section(p, prose):
    g = dict(p.get("guidance_check") or {})
    g.setdefault("latest_quarter", p.get("latest_quarter"))
    _rk = _ranked_codes(p)
    g["delivered"] = [r for r in (g.get("delivered") or []) if r.get("ltcode") in _rk
                      or re.fullmatch(r"(total )?revenues?|net sales|(adjusted |diluted |reported )?eps",
                                      str(r.get("display") or r.get("name") or "").strip(), re.I)]
    return "".join([
        key_sources_table(p),
        dq_exceptions_note(p),
        delivered_block(g, heading="Reported against guidance, "
                                  + str(p.get("latest_quarter") or ""),
                        ranked=_rk),
        forward_block(g, prose, heading="Company outlook, "
                                        + str(g.get("next_quarter") or "")),
        presentation_basis(p),
        methodology_box(p),
        limitations_para(p),
    ])


_GUIDE_IMP = (r"\beps\b|earnings per share|^(core )?a?ffo\b", r"revenue|net sales|^sales",
              r"margin|medical (loss|care) ratio|\bmlr\b|combined ratio|operating ratio|"
              r"^(adjusted )?(ebit|operating (profit|income)|earnings from operations)$",
              r"organic", r"occupancy|same store|\bnoi\b",
              r"cash flow|\bfcf\b|\bocf\b", r"capex|capital expend",
              r"repurchase|buyback", r"dividend|\bdps\b", r"opex|operating expense|cost")


def _guide_importance(o):
    nm = re.sub(r"\([^)]*\)", " ", str(o.get("display") or o.get("name") or "")).strip()
    if re.search(r"\beffect on\b|\bimpact\b|contribution", nm, re.I):
        return 60
    if re.search(r"tax rate", nm, re.I):
        return len(_GUIDE_IMP)
    for i, pat in enumerate(_GUIDE_IMP):
        if re.search(pat, nm, re.I):
            return i
    return None


def _revisions_shown(g, rank=None):
    rev = [r for r in (g.get("revisions") or [])
           if rank is None or r.get("ltcode") in rank or r.get("in_scan")
           or _guide_importance(r) is not None]

    def _rk(r):
        i = _guide_importance(r)
        return (i if i is not None else 50, (rank or {}).get(r.get("ltcode"), 999))
    return sorted(rev, key=_rk)[:6]


def revisions_block(g, rank=None):
    rev = _revisions_shown(g, rank)
    if not rev:
        return ""

    def _chg(r):
        if r.get("midpoint_change_bps") is not None:
            return " (midpoint %+.0f bps)" % r["midpoint_change_bps"]
        if r.get("midpoint_change_pct") is not None:
            return " (midpoint %+.1f%%)" % r["midpoint_change_pct"]
        return ""
    body = "".join(
        "<tr>"
        '<td class="name">' + esc(r.get("display")) + "</td>"
        "<td>" + esc(r.get("target_period")) + "</td>"
        '<td class="num">' + esc(r.get("first_display") or r.get("first_text"))
        + " <span class=\"tag\">" + esc(r.get("first_source_period")) + "</span></td>"
        '<td class="num">' + esc(r.get("latest_display") or r.get("latest_text"))
        + " <span class=\"tag\">" + esc(r.get("latest_source_period")) + "</span></td>"
        "<td>" + esc(str(r.get("direction") or "")[:1].upper() + str(r.get("direction") or "")[1:])
        + _chg(r) + "</td></tr>" for r in rev)
    return ('<p class="note"><b>Full-year guides changed this year:</b> the earlier guide '
            'against the latest. A guide given in words is quoted as given.</p>'
            "<table><thead><tr><th>Guided line</th><th>For</th><th>Earlier guide</th>"
            "<th>Latest guide</th><th>Change</th></tr></thead><tbody>" + body
            + "</tbody></table>")


def routing_block(p):
    r = p.get("sector_routing") or {}
    if r.get("classification_relation") == "proposed_unsupported":
        _labels = (p.get("sector_tables") or {}).get("labels") or {}
        _prop = (r.get("proposed_from_classification") or [None])[0]
        return ('<p class="note"><b>Sector routing.</b> Source classification '
                + esc(r.get("insync_subindustry")) + ' proposes the <b>'
                + esc(_labels.get(_prop, _prop)) + '</b> template; this company '
                'discloses none of the lines that template ranks on, so the note '
                'uses the general ordering and the header says General.</p>')
    if not r.get("template"):
        return ""
    scores = ", ".join(f"{k} {v}" for k, v in (r.get("evidence_scores") or {}).items())
    rel = r.get("classification_relation")
    if r.get("overrode_classification"):
        return ('<p class="note"><b>Sector routing.</b> Source classification '
                + esc(r.get("insync_subindustry")) + '; the note used <b>'
                + esc(r.get("template_label")) + '</b> instead, resolved from the '
                'metrics this company discloses (evidence: ' + esc(scores) +
                '; threshold ' + esc(r.get("minimum_evidence")) + '). The template '
                'sets the order of the tables and nothing else.</p>')
    if rel == "chose_among_proposed":
        _labels = (p.get("sector_tables") or {}).get("labels") or {}
        others = [_labels.get(x, x) for x in (r.get("proposed_from_classification") or [])
                  if x != r.get("template")]
        return ('<p class="note"><b>Sector routing.</b> Source classification '
                + esc(r.get("insync_subindustry")) + ' admits more than one '
                'template' + ((' (also ' + esc(", ".join(others)) + ')') if others
                              else '') + '; the disclosed metrics chose <b>'
                + esc(r.get("template_label")) + '</b> (evidence: ' + esc(scores)
                + '; threshold ' + esc(r.get("minimum_evidence")) + '). The '
                'template sets the order of the tables and nothing else.</p>')
    if rel == "filled_gap":
        return ('<p class="note"><b>Sector routing.</b> Source classification '
                + esc(r.get("insync_subindustry")) + ' maps to no template in '
                'this skill; the note used <b>' + esc(r.get("template_label"))
                + '</b>, resolved from the metrics this company discloses '
                '(evidence: ' + esc(scores) + '; threshold '
                + esc(r.get("minimum_evidence")) + '). The template sets the '
                'order of the tables and nothing else.</p>')
    best = max((r.get("evidence_scores") or {}).values(), default=0)
    floor = r.get("minimum_evidence") or 0
    if best >= floor and scores:
        return ('<p class="note"><b>Sector routing.</b> Source classification '
                + esc(r.get("insync_subindustry")) + '; template <b>'
                + esc(r.get("template_label")) + '</b>, confirmed by the disclosed '
                'metrics (evidence: ' + esc(scores) + ').</p>')
    return ('<p class="note"><b>Sector routing, unconfirmed.</b> Source classification '
            + esc(r.get("insync_subindustry")) + '; template <b>'
            + esc(r.get("template_label")) + '</b>, taken from that classification '
            'alone. The disclosed metrics did not reach the evidence threshold of '
            + esc(floor) + ' needed to confirm or override it'
            + (' (evidence: ' + esc(scores) + ')' if scores
               else ' (no template pattern matched any retrieved metric)')
            + '. The template affects the ORDER of the tables and nothing else, so an '
            'unconfirmed route means the ranking is closer to purely statistical; no '
            'figure and no implication on this page depends on it.</p>')


def not_retrieved_block(p):
    nr = p.get("not_retrieved") or {}
    rows = nr.get("missing") or []
    if not rows:
        return ""
    _code = PROVENANCE["mode"] == "full"
    listed = "; ".join(esc(r["name"]) + ((" (" + esc(r["ltcode"]) + ")") if _code else "")
                       + ((", " + esc(r["status"])) if r.get("status")
                          and r["status"] != "not fetched in this run" else "")
                       for r in rows[:10])
    more = ("; and " + esc(len(rows) - 10) + " more, listed in full in the audit record"
            if len(rows) > 10 else "")
    return ('<p class="note"><b>Discovered and not retrieved (' +
            esc(nr.get("missing_count", 0)) + ' of ' + esc(nr.get("planned", 0)) +
            ').</b> ' + listed + more + '. Their history was never read, so nothing in '
            'this note covers them and an inflection among them would not be visible '
            'here.</p>')


PROVENANCE = {"mode": "standard"}


SOURCE_LABEL = {
    "sec fillings": "SEC filing",
    "sec filings": "SEC filing",
    "other filings": "Other filing",
    "financial release": "Financial release",
    "presentations": "Investor presentation",
    "calculated": "Calculated",
    "sell-side/other model sources": "Modelled",
}


def _sourced_from(r):
    mix = r.get("source_mix") or {}
    if not mix:
        return r.get("class") or "document class not returned"
    show_n = len(mix) > 1
    return ", ".join(
        SOURCE_LABEL.get(str(k).strip().lower(), str(k))
        + ((" (" + str(v) + ")") if show_n else "")
        for k, v in mix.items())


def _shown_in(r):
    long = {"Top five": "Top five", "Accel": "Accelerating", "Decel": "Decelerating",
            "Dive": "Deep dive", "Quiet": "Quiet", "Map": "Inflection map",
            "Monitor": "Monitor", "Cash": "Cash panel", "Cross": "Cross-metric",
            "Guide": "Guidance"}
    names = [long.get(SECTION_SHORT.get(x, x), SECTION_SHORT.get(x, x))
             for x in (r.get("shown_in") or [])]
    return ", ".join(names) or "scanned only"


def provenance_table(p):
    rows = p.get("provenance") or []
    if not rows or PROVENANCE["mode"] == "minimal":
        return ""
    full = PROVENANCE["mode"] == "full"

    spans = {(r.get("quarters"), r.get("first_period"), r.get("last_period"))
             for r in rows}
    defs = {bool(r.get("definition_stable")) for r in rows}
    show_span = full or len(spans) > 1
    show_def = full or len(defs) > 1

    def _initialism_of(short, long_):
        letters = re.sub(r"[^a-z]", "", short.lower())
        words = [w for w in re.findall(r"[a-z]+", long_.lower()) if len(w) > 2]
        if not letters or not words or len(letters) > len(words) + 1:
            return False
        return letters.startswith("".join(w[0] for w in words[:len(letters)]))

    QUALIFIER = re.compile(
        r"\(([^)]*)\)|\b(ex-?\w*|adj\w*|total|consolidated|gross|reported|"
        r"pro\s*-?forma|comparable|organic|constant currency|underlying|"
        r"like-for-like|w/[\w\s]+|including|excluding)\b", re.I)

    def basis_note(r):
        disp = (r.get("display") or r["name"] or "").strip()
        src = (r["name"] or "").strip()
        flat = lambda x: re.sub(r"[^a-z0-9]", "", x.lower())
        parts = []
        if flat(disp) != flat(src) and not _initialism_of(src, disp):
            src_q = {flat(m.group(0)) for m in QUALIFIER.finditer(src)}
            disp_q = {flat(m.group(0)) for m in QUALIFIER.finditer(disp)}
            if src_q - disp_q:
                parts.append(esc(src))
        if full:
            parts.append(esc(r["ltcode"]))
            parts.append(esc(r.get("uic") or "UIC not returned"))
        if not parts:
            return ""
        return '<div class="pbasis">' + " &middot; ".join(parts) + "</div>"

    cols = ["<th>Metric" + (" / LTCODE / UIC" if full else "") + "</th>"]
    if show_span:
        cols.append("<th>Qtrs, span</th>")
    cols.append("<th>Sourced from</th>")
    if show_def:
        cols.append("<th>Def.</th>")
    cols.append("<th>Shown in</th>")
    head = "<thead><tr>" + "".join(cols) + "</tr></thead>"

    body = []
    for r in rows:
        cells = ['<td class="name">' + esc(r.get("display") or r["name"])
                 + basis_note(r) + "</td>"]
        if show_span:
            cells.append('<td class="num">' + esc(r["quarters"]) + "Q, "
                         + esc(r.get("first_period")) + " to "
                         + esc(r.get("last_period")) + "</td>")
        cells.append('<td class="src">' + esc(_sourced_from(r)) + "</td>")
        if show_def:
            cells.append("<td>" + ("stable" if r.get("definition_stable")
                                   else "moved") + "</td>")
        cells.append('<td class="src">' + esc(_shown_in(r)) + "</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")

    consts = []
    if not show_span and spans:
        q, a, b = next(iter(spans))
        consts.append("Every line below covers " + esc(q) + " quarters, "
                      + esc(a) + " to " + esc(b))
    if not show_def and defs:
        consts.append("no line changed definition inside the window"
                      if defs == {True} else "definitions moved on every line")
    tail = ""
    if consts:
        tail += ('<p class="note"><b>Common to every row.</b> '
                 + esc("; ".join(consts)).replace("&amp;", "&") + '.</p>')
    tail += ('<p class="note"><b>Source chain.</b> Figures are fetched from the '
             'InSync MCP; the source class beside each line is the classification '
             'the provider returns for it, not a document this note opened. Every '
             'level shown is a figure the provider reported; every growth rate, '
             'acceleration and threshold was computed from those levels by the '
             'bundled engine.</p>')
    tail += ('<p class="note"><b>Rounding.</b> Every rate is computed on the unrounded '
             'values the provider returned and rounded once, for display only. '
             'Recomputing a rate from the rounded levels printed here can differ in the '
             'last digit shown.</p>')
    if not full:
        tail += ('<p class="note"><b>Full audit trail.</b> Every line\u2019s source code, '
                 'internal identifier and per-quarter source mix, with the provider\u2019s '
                 'own source labels verbatim, is retained in the audit record for this '
                 'run, so any figure here can be traced to the fetch that returned it.</p>')
    unresolved = sum(1 for x in rows if not (x.get("source_mix")))
    if unresolved:
        tail += ('<p class="note">"Document class not returned" means the MCP response '
                 'for that line carried no provenance field. The figure is a reported '
                 'actual from the same fetch as every other row; the gap is in the '
                 'metadata, not the data.</p>')
    return ('<table class="prov">' + head + "<tbody>" + "".join(body)
            + "</tbody></table>" + tail)


def provenance_summary(p):
    rows = p.get("provenance") or []
    if not rows or PROVENANCE["mode"] != "minimal":
        return ""
    tally = {}
    for r in rows:
        for k in (r.get("source_mix") or {"document class not returned": 1}):
            lab = SOURCE_LABEL.get(str(k).strip().lower(), str(k))
            tally[lab] = tally.get(lab, 0) + 1
    spans = {(r.get("quarters"), r.get("first_period"), r.get("last_period"))
             for r in rows}
    span_txt = ""
    if len(spans) == 1:
        q, a, b = next(iter(spans))
        span_txt = (", each over " + esc(q) + " quarters, " + esc(a) + " to " + esc(b))
    return ('<p class="note"><b>Sources.</b> '
            + esc("; ".join("%s: %d line(s)" % (k, v) for k, v in sorted(tally.items())))
            + span_txt + '. Source classes are as the InSync MCP returns them, not '
            'documents this note opened; every level shown is a figure the provider '
            'reported and every rate of change was computed from those levels by the '
            'bundled engine. The per-line audit trail is retained in the audit '
            'record for this run.</p>')


def methodology(p):
    k = p["constants"]
    ex = p.get("excluded") or []
    reasons = {}
    for e in ex:
        reasons[e["reason"]] = reasons.get(e["reason"], 0) + 1
    by_code = {}
    for src in (ex, p.get("provenance") or []):
        for row in src:
            if row.get("ltcode"):
                by_code.setdefault(row["ltcode"], row.get("display") or row.get("name") or "")

    def _dename(txt):
        if PROVENANCE["mode"] == "full":
            return txt
        def sub(m):
            nm = by_code.get(m.group(0))
            return nm if nm else "another line"
        return re.sub(r"\b(?:IS|BS|CF|KM|SD)_[PS]_\d+(?:-M)?\b", sub, txt)

    merged = {}
    for kk, vv in reasons.items():
        key = _dename(str(kk))
        merged[key] = merged.get(key, 0) + vv
    held = "; ".join(k + " (" + str(v) + ")" for k, v in sorted(merged.items()))
    return ('<p class="note"><b>Method.</b> Growth is year-over-year per quarter, in percent '
            'for levels and basis points for ratios. Acceleration is the change in that growth '
            'rate from the prior quarter. <b>How unusual</b> scores the latest acceleration '
            'against that same line&rsquo;s own history, which is what makes different units '
            'comparable on one list. Order is a composite of unusualness, tier, materiality '
            'and confidence, each component recorded per row in the audit record. Lookback ' +
            esc(k["lookback_quarters"]) + 'Q; minimum ' + esc(k["min_quarters_to_rank"]) +
            'Q to score; a line must report in the scan quarter. <b>Held out:</b> ' +
            esc(held or "none") + '. ' + currency_sentence(p) + '</p>'
            + not_scored_note(p))


def closing_scope(p):
    c = p.get("coverage") or {}
    planned, scanned = c.get("series_planned") or 0, c.get("series_scanned") or 0
    if planned <= scanned and scanned >= SCAN_BREADTH_FLOOR:
        return ""
    return ('<p class="note">Scoped to the ' + esc(scanned) + ' metrics this run '
            'read of the ' + esc(planned or scanned) + ' the discovery plan found. '
            'What to watch next is what these lines make measurable; the metrics '
            'not read are named under Provenance And Method and nothing here '
            'covers them.</p>')


def build_html(p, prose, commentary=None):
    shape = check_prose_shape(prose or {})
    if shape:
        raise SystemExit("narrative rejected:\n  " + "\n  ".join(shape)
                         + "\nThis is a monitor, not a report. Sections are capped at "
                           "%d words; guidance and watch are lists of at most %d bullets "
                           "of %d words." % (PROSE_CAP["setup"],
                                             BULLET_CAP["max_bullets"],
                                             BULLET_CAP["max_words"]))
    guard = ('<div class="guard">' + esc(p["guard"]) + "</div>") if p.get("guard") else ""
    watch = ""
    return ("""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<!-- Declared ON THE PAGE, because the page is what the identifier gate reads.
     Stamping it into the in-memory payload was the first attempt and the gate
     never saw it: the linter reads the computed.json the ENGINE wrote, not the
     renderer's copy of it. -->
<meta name='insync-provenance' content='""" + esc(PROVENANCE["mode"]) + """'>
<title>""" + esc(p["ticker"]) + " Inflection Monitor " + esc(p["latest_quarter"]) + " &middot; " + esc(PRODUCT_DESCRIPTOR) + """</title>
<style>""" + CSS + (compact_css() if COMPACT["on"] else "") + """</style></head><body><div class="page">
""" + masthead(p) + scan_scope_banner(p) + guard + standfirst(prose)
   + quarter_strip(p) + summary_bullets(p, prose) + """
""" + sect(headline_label(p), headline_table(p)) + (sect("Rate KPIs", rate_table(p)) if rate_table(p) else "") + quiet_line(p) + context_section(p) + """

<div class="brk"></div>
""" + sect("Accelerating", rank_table(_trim_rows(p["accelerating"], "Accelerating"),
                                     "Accelerating"))
   + sect("Decelerating", rank_table(_trim_rows(p["decelerating"], "Decelerating"),
                                     "Decelerating"))
   + why_unsigned(p) + marker_legend(p)
   + sect("Eight-Quarter Inflection Map", heatmap_table(p)) + """

<div class="brk"></div>
<div class="label">What Is Driving The Change</div><div class="rule"></div>
""" + deep_dives(p, prose, commentary) + """

""" + sect("Cross-Metric Signals", divergence_block(p, prose), cash_block(p, commentary)) + """

<div class="brk"></div>
""" + sect("Management Guidance",
           guidance_block(p, prose, tables=(PROVENANCE["mode"] == "full"))) + """
""" + sect("What To Monitor Next", monitor_table(p), watch)
   + (sect("Provenance And Method", provenance_table(p), provenance_summary(p))
      + presentation_basis(p) + routing_block(p) + not_retrieved_block(p)
      + source_records_block(p, commentary)
      + methodology(p)
      if PROVENANCE["mode"] == "full" else
      sect("Sources, Guidance And Methodology",
           client_reference_section(p, prose))) + """
<div class="footer">
Source: company reports via InSync Analytics (InSync MCP); InSync Analytics calculations (marked *).
Levels "to hold" a growth rate are arithmetic on the year-ago base, not forecasts. For
research support only; not investment advice or a recommendation.""" + quote_footer(commentary) + """
Inflection Monitor engine v""" + esc(p.get("engine_version") or "unversioned") + """.
Prepared """ + ("by " + esc(_prepared_by(p)) + ", " if _prepared_by(p) else "") + """""" + esc(p["generated_utc"]) + """.
</div>
</div></body></html>""")



def page_count(pdf_path):
    try:
        with open(pdf_path, "rb") as fh:
            blob = fh.read()
    except OSError:
        return None
    counts = [int(m) for m in re.findall(rb"/Count\s+(\d+)", blob)]
    if counts:
        return max(counts)
    n = len(re.findall(rb"/Type\s*/Page[^s]", blob))
    return n or None


SECT_MARK = "<!--SECT-->"


def _pdf_page_lines(pdf_path):
    try:
        out = subprocess.run(["pdftotext", "-layout", pdf_path, "-"],
                             capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    return [[re.sub(r"\s+", " ", l).strip() for l in pg.splitlines() if l.strip()]
            for pg in out.stdout.decode("utf-8", "replace").split("\f")]


def bad_breaks(pdf_path, labels, heads):
    pages = _pdf_page_lines(pdf_path)
    if len(pages) < 2:
        return None
    _key = lambda x: re.sub(r"\s+", "", x).lower()
    _head = lambda x: not x.rstrip().endswith((".", ",", ";", ":"))
    lows_labels = {_key(l) for l in labels}
    for pi in range(2, len(pages) + 1):
        ls = pages[pi - 1]
        if not ls:
            continue
        lows = [x.lower().rstrip(".") for x in ls]
        label_at = next((k for k, x in enumerate(ls)
                         if _head(x) and _key(x) in lows_labels), None)
        for cells in heads:
            want = [c.lower() for c in cells]
            hdr_at = next((k for k in range(min(len(lows), 6))
                           if sum(1 for c in want if c in lows[k])
                           >= max(3, len(want) - 1)), None)
            if hdr_at is None:
                continue
            if label_at is not None and label_at < hdr_at:
                continue
            return pi
        prev = pages[pi - 2]
        if prev and _head(prev[-1]) and _key(prev[-1]) in lows_labels:
            return pi
    return None


REFERENCE_LABELS = {
    "provenanceandmethod",
    "provenance:guidancerowsasreturned",
    "provenance:documentsbehindquotedmaterial",
    "sources,guidanceandmethodology",
}


def analysis_overruns(pdf_path, labels, analytic_cutoff=3):
    pages = _pdf_page_lines(pdf_path)
    _key = lambda x: re.sub(r"\s+", "", x).lower().rstrip(".")
    analytic = {_key(l) for l in labels if _key(l) not in REFERENCE_LABELS}
    for pi, ls in enumerate(pages, 1):
        if pi <= analytic_cutoff:
            continue
        for x in ls:
            if _key(x) in analytic:
                return pi
    return None


def to_pdf(html_path, pdf_path):
    try:
        from weasyprint import HTML
        HTML(filename=html_path).write_pdf(pdf_path)
        return "weasyprint"
    except Exception:
        pass
    for exe in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        path = shutil.which(exe) or (
            f"/opt/pw-browsers/{exe}" if os.path.exists(f"/opt/pw-browsers/{exe}") else None)
        if not path:
            continue
        try:
            subprocess.run(
                [path, "--headless", "--disable-gpu", "--no-sandbox",
                 "--no-pdf-header-footer", f"--print-to-pdf={pdf_path}",
                 f"file://{os.path.abspath(html_path)}"],
                check=True, capture_output=True, timeout=180)
            if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 1000:
                return os.path.basename(path)
        except Exception:
            continue
    if shutil.which("wkhtmltopdf"):
        try:
            subprocess.run(["wkhtmltopdf", "-q", html_path, pdf_path],
                           check=True, capture_output=True, timeout=180)
            return "wkhtmltopdf"
        except Exception:
            pass
    return None



def main():
    ap = argparse.ArgumentParser(description="inflection-monitor renderer")
    ap.add_argument("computed")
    ap.add_argument("--narrative")
    ap.add_argument("--commentary",
                    help="JSON map of ltcode -> {text, speaker, role, quarter, document, "
                         "document_date, document_id, attributed_to_metric} (optional: ticker, "
                         "page, effect): management's own explanation for a line, extracted "
                         "with parse_document_content. A quotation missing a required field "
                         "is withheld")
    ap.add_argument("--out-dir", default="./out")
    ap.add_argument("--narrative-template", action="store_true",
                    help="print the narrative.json contract for this computed.json and exit")
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--prepared-by", default=os.environ.get("INFLECTION_PREPARED_BY"),
                    help="name printed in the footer colophon and "
                         "recorded in the audit JSON. Defaults to "
                         "$INFLECTION_PREPARED_BY.")
    ap.add_argument("--provenance", choices=("full", "standard", "minimal"),
                    default="standard",
                    help="how much of the audit trail to PRINT. The trail itself is "
                         "unchanged in every mode and stays in the working JSON; full is for "
                         "internal QC, standard is the client default, minimal drops "
                         "the per-line table for a source-class summary.")
    ap.add_argument("--max-pages", type=int, default=4,
                    help="hard page budget; the renderer falls back to compact to meet it")
    args = ap.parse_args()
    args.max_pages_explicit = any(a.startswith("--max-pages") for a in sys.argv[1:])

    with open(args.computed, encoding="utf-8") as fh:
        p = json.load(fh)

    if args.prepared_by:
        p["prepared_by"] = args.prepared_by.strip()

    if args.narrative_template:
        print(json.dumps(narrative_template(p), indent=2))
        return

    prose, problems = {}, []
    if args.narrative:
        with open(args.narrative, encoding="utf-8") as fh:
            prose, problems = resolve_narrative(json.load(fh), p["placeholders"])
    if problems:
        raise SystemExit("narrative rejected:\n  " + "\n  ".join(problems))

    commentary = {}
    if args.commentary and os.path.exists(args.commentary):
        with open(args.commentary, encoding="utf-8") as fh:
            commentary = json.load(fh) or {}

    os.makedirs(args.out_dir, exist_ok=True)
    stem = f"{ticker_slug(p['ticker'])}_Inflection_Monitor_{p['latest_quarter']}"
    hp = os.path.join(args.out_dir, stem + ".html")
    pp = os.path.join(args.out_dir, stem + ".pdf")

    BREAKS = {"before": set()}

    def _emit():
        doc = build_html(p, prose, commentary)
        parts = doc.split(SECT_MARK)
        out = [parts[0]]
        for k, seg in enumerate(parts[1:], 1):
            if k in BREAKS["before"]:
                out.append('<div class="brk"></div>')
            out.append(seg)
        return "".join(out)

    def _sect_meta():
        doc = build_html(p, prose, commentary)
        labels = [re.sub(r"<[^>]+>", "", re.sub(r"\s+", " ", m)).strip()
                  for m in re.findall(r'<div class="label">(.*?)</div>', doc, re.S)]
        heads = []
        for tm in re.finditer(r"<thead>(.*?)</thead>", doc, re.S):
            cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).strip()
                     for c in re.findall(r"<th[^>]*>(.*?)</th>", tm.group(1), re.S)]
            cells = [c for c in cells if c]
            if len(cells) >= 3:
                heads.append(cells)
        return labels, heads, len(doc.split(SECT_MARK)) - 1

    p["provenance_print_mode"] = args.provenance

    def write_and_measure():
        with open(hp, "w", encoding="utf-8") as fh:
            fh.write(_emit())
        if args.no_pdf:
            return None, None
        eng = to_pdf(hp, pp)
        return eng, (page_count(pp) if eng else None)

    def solve_breaks(eng, pages, budget):
        if not eng or not pages:
            return eng, pages, 0
        labels, heads, n_sect = _sect_meta()
        added = 0
        for _ in range(6):
            bad = bad_breaks(pp, labels, heads)
            if bad is None:
                break
            pg = _pdf_page_lines(pp)
            if bad > len(pg):
                break
            _k = lambda x: re.sub(r"\s+", "", x).lower().rstrip(".")
            here = {_k(x) for x in pg[bad - 1]}
            prior = {_k(x) for x in pg[bad - 2]}
            idx = None
            for k, l in enumerate(labels, 1):
                if _k(l) in here and _k(l) not in prior:
                    idx = k
                    break
            if idx is None:
                idx = max((k for k, l in enumerate(labels, 1)
                           if _k(l) in prior), default=None)
            if idx is None or idx in BREAKS["before"] or idx > n_sect:
                break
            BREAKS["before"].add(idx)
            eng2, pages2 = write_and_measure()
            if pages2 and pages2 > budget:
                BREAKS["before"].discard(idx)
                eng, pages = write_and_measure()
                break
            eng, pages, added = eng2, pages2, added + 1
        return eng, pages, added

    budget = args.max_pages
    if not args.max_pages_explicit:
        n = (p.get("coverage") or {}).get("material_total")
        if n is None:
            n = ((p.get("coverage") or {}).get("material_accelerating", 0)
                 + (p.get("coverage") or {}).get("material_decelerating", 0))
        derived = 2 if n < 5 else (3 if n <= 8 else 4)
        if (p.get("not_retrieved") or {}).get("missing_count"):
            derived += 1
        budget = min(args.max_pages, derived)
    breach_budget = budget + (1 if (args.provenance == "full"
                                    and not args.max_pages_explicit) else 0)

    PROVENANCE["mode"] = args.provenance
    COMPACT["on"] = False
    eng, pages = write_and_measure()
    eng, pages, breaks_added = solve_breaks(eng, pages, budget)
    mode = "standard"
    overrun = (analysis_overruns(pp, _sect_meta()[0]) if eng else None)
    if pages is not None and (pages > budget or overrun):
        COMPACT["on"] = True
        mode = "compact"
        eng, pages = write_and_measure()
        eng, pages, breaks_added = solve_breaks(eng, pages, budget)
        overrun = (analysis_overruns(pp, _sect_meta()[0]) if eng else None)
        while (pages is not None and (pages > budget or overrun)
               and TRIM["n"] < 4):
            TRIM["n"] += 1
            TRIM["dropped"] = []
            mode = "trimmed"
            eng, pages = write_and_measure()
            eng, pages, breaks_added = solve_breaks(eng, pages, budget)
            overrun = (analysis_overruns(pp, _sect_meta()[0]) if eng else None)
            if not TRIM["dropped"]:
                break

        while (pages is not None and (pages > budget or overrun) and REF["tight"] < 2):
            REF["tight"] += 1
            mode = "trimmed"
            eng, pages = write_and_measure()
            eng, pages, breaks_added = solve_breaks(eng, pages, budget)
            overrun = (analysis_overruns(pp, _sect_meta()[0]) if eng else None)

    result = {"html": hp, "pdf": pp if eng else None, "pdf_engine": eng,
              "pages": pages, "layout_mode": mode, "page_budget": budget,
              "page_budget_tolerated": breach_budget,
              "provenance_mode": args.provenance,
              "layout_breaks_added": breaks_added,
              "analysis_pages_ok": overrun is None,
              "rows_held_out": sorted(set(TRIM["dropped"])),
              "budget_basis": ("caller supplied" if args.max_pages_explicit
                               else "derived from the material-inflection count"),
              "narrative_sections": sum(1 for v in prose.values() if v),
              "commentary_metrics": len(commentary)}
    if not eng and args.no_pdf:
        result["pdf_note"] = "PDF not requested (--no-pdf): a test render, not a deliverable."
    elif not eng:
        result["deliverable"] = None
        result["pdf_note"] = ("STOP: no PDF engine is available, so there is no deliverable "
                              "(the PDF is the only one); tell the user a PDF engine is needed "
                              "and do not ship the HTML. The page budget could not be "
                              "measured.")
    elif pages is not None and pages > breach_budget:
        result["page_budget_breach"] = (
            f"Still {pages} pages after compaction and trimming, against a "
            f"{breach_budget}-page budget. Reduce the displayed rows or deep dives "
            f"rather than shipping over budget.")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _contract import run as _run_contract
    _run_contract(main, 'render')
