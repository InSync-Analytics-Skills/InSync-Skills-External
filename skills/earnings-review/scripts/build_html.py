"""Earnings Review - build_html.py (generic renderer)
Reads <workdir>/data.json, review_config.json, computed.json, prose.json and writes <workdir>/<TICKER>_<PERIOD>_Earnings_Review.html
in the InSync house PDF format (US Letter, Calibri body, Georgia three-cell masthead, orange rules, banded tables, colour = sign only).
Prose is authored in prose.json with {tokens} that resolve to pre-formatted strings from computed.json; {token|c} colours by sign.
Nothing numeric is typed here.
Usage: python build_html.py <workdir>
"""
import json, sys, os, re, html
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from erlib import envelope, crash_envelope
from version import VERSION
crash_envelope("build_html")
W = sys.argv[1] if len(sys.argv) > 1 else "."
d = json.load(open(os.path.join(W, "data.json"), encoding="utf-8")); cfg = json.load(open(os.path.join(W, "review_config.json"), encoding="utf-8"))
if not os.path.exists(os.path.join(W, "computed.json")): envelope("build_html", "computed.json missing: run compute.py first")
c = json.load(open(os.path.join(W, "computed.json"), encoding="utf-8")); pr = json.load(open(os.path.join(W, "prose.json"), encoding="utf-8"))
_need = {"the_print": list, "summary": list, "what_changed": list, "guidance_read": str, "tone": dict, "monitor": list, "source": str}
_bad = [k for k, t in _need.items() if not isinstance(pr.get(k), t)]
if _bad: envelope("build_html", f"prose.json is missing or mistyped: {', '.join(_bad)} (see templates/prose.json)")
BRAND_DEFAULT = {"colors": {"orange": "E8850C", "orangeDk": "B25E08", "ink": "1A1A1A", "navy": "232526", "white": "FFFFFF", "headerBg": "EDEDED", "bandFill": "F0E9D8",
    "calloutBg": "F6F3EE", "mastBorder": "C9C9C9", "beatGreen": "1E7A34", "missRed": "B02A1F", "inlineGray": "6E6E6E", "sectionHeader": "1A1A1A", "tableBorder": "DDDDDD",
    "headerRule": "BBBBBB", "rowRule": "EEEEEE", "rowAlt": "F5F0E8", "divider": "F0E9D8", "grayDark": "222222", "grayLight": "555555", "footerBorder": "DDDDDD"},
    "fonts": {"title": "Georgia", "body": "Calibri"},
    "masthead": {"brand_size_half_points": 33, "brand_size_two_line_half_points": 24, "mark_px": {"w": 59, "h": 46}, "company_size_ladder_half_points": [28, 27, 26, 25, 24, 23, 22, 21, 20],
                 "logo_file": "insync_logo.png", "brand_split": {"orange_word": "InSync", "ink_word": " Analytics"}},
    "disclosure": "For informational purposes only. Not investment advice. This document is not an offer to buy or sell, or a solicitation of an offer to buy or sell, any financial instrument, and it does not constitute participation in any trading strategy in any jurisdiction. InSync Analytics does not guarantee the accuracy, completeness, or timeliness of the information presented. This document does not take into account the investment objectives, financial situation, or specific needs of any individual investor. InSync Analytics is not a licensed financial advisor, broker, or dealer, and this document is not intended to provide investment, legal, accounting, or tax advice. You are solely responsible for your own investment decisions; InSync Analytics is not liable for any losses or damages arising from the use of this material."}
_bp = os.path.join(ROOT, "common", "brand.json")
if os.path.exists(_bp): brand = json.load(open(_bp, encoding="utf-8"))
else: brand = BRAND_DEFAULT; print("WARN brand.json missing: using the built-in copy; restore common/brand.json")
MH = brand.get("masthead") or BRAND_DEFAULT["masthead"]; FONTS = brand.get("fonts") or BRAND_DEFAULT["fonts"]
T = dict(c["tokens"])
for _k, _q in (d.get("quotes") or {}).items(): T[f"q.{_k}"] = "\u201c" + str(_q.get("text", "")).strip() + "\u201d"
M = d["metrics"]; CM = c["metrics"]; P = d["periods"]; n = len(P); i = n - 1
esc = html.escape; DASH = "—"; UNRESOLVED = []

BC = brand["colors"]
C = dict(ORANGE="#" + BC["orange"], INK="#" + BC["ink"], DARK="#" + BC["sectionHeader"], GREY="#" + BC["inlineGray"], LABEL="#" + BC["grayLight"],
         HEADBG="#" + BC["headerBg"], BAND="#" + BC["bandFill"], ZEBRA="#" + BC["rowAlt"], EXBG="#" + BC["calloutBg"], BOX="#" + BC["mastBorder"],
         HRULE="#" + BC["headerRule"], RRULE="#" + BC["rowRule"], GRID="#" + BC["tableBorder"], GREEN="#" + BC["beatGreen"], RED="#" + BC["missRed"])

def _is_zero(t): return re.fullmatch(r"[+\-(]*0(\.0+)?\)?\s*(%|bps|bp)?", t) is not None
def sgn(t):
    t = str(t).strip()
    if _is_zero(t): return esc(t)
    if t.startswith("(") or t.startswith("-") or t.startswith("−"): return f'<span class="dn">{esc(t)}</span>'
    if t.startswith("+"): return f'<span class="up">{esc(t)}</span>'
    return esc(t)
def neg(t):
    t = str(t).strip(); return f'<span class="dn">{esc(t)}</span>' if (t.startswith("(") or t.startswith("-")) else esc(t)
TOK = re.compile(r"\{([A-Za-z0-9_.\-]+)(\|c)?\}")
def rel_disp():
    dsp, tm = REL["display"], REL.get("timing", "")
    return dsp if (not tm or tm.lower() in dsp.lower()) else f"{dsp} ({tm})"
def sub(text):
    out = []; pos = 0
    for m in TOK.finditer(text):
        out.append(esc(text[pos:m.start()]))
        key, col = m.group(1), m.group(2)
        if key not in T: UNRESOLVED.append(key); out.append(f'<span class="dn">[{esc(key)}]</span>')
        else: out.append(sgn(T[key]) if col else esc(T[key]))
        pos = m.end()
    out.append(esc(text[pos:])); return "".join(out)
def bold_lead(lead, text): return f"<b>{sub(lead)}</b> {sub(text)}"
def subtxt(text):
    def rep(m):
        key = m.group(1)
        if key not in T: UNRESOLVED.append(key); return f"[{key}]"
        return T[key]
    return TOK.sub(rep, text)

def table(head, rows, widths, aligns=None, sub_heads=None, keep=False):
    aligns = aligns or (["l"] + ["r"] * (len(head) - 1))
    cg = "".join(f'<col style="width:{w}%">' for w in widths)
    def th(j, h):
        s2 = f'<div class="th2">{esc(sub_heads[j])}</div>' if sub_heads and sub_heads[j] else ""
        return f'<th class="{aligns[j]}">{esc(h)}{s2}</th>'
    ths = "".join(th(j, h) for j, h in enumerate(head)); body = ""; z = 0
    for r in rows:
        if isinstance(r, tuple) and r[0] == "band": body += f'<tr class="band"><td colspan="{len(head)}">{esc(r[1])}</td></tr>'; z = 0; continue
        if isinstance(r, tuple) and r[0] == "sub": body += '<tr class="subrow">' + "".join(f'<td class="{aligns[j]}">{cell}</td>' for j, cell in enumerate(r[1])) + "</tr>"; continue
        cls = ' class="z"' if z % 2 == 1 else ""; z += 1
        body += f"<tr{cls}>" + "".join(f'<td class="{aligns[j]}">{cell}</td>' for j, cell in enumerate(r)) + "</tr>"
    return f'<table class="data{" keep" if keep else ""}"><colgroup>{cg}</colgroup><thead><tr>{ths}</tr></thead><tbody>{body}</tbody></table>'
def sec(t): return f'<h2 class="sec">{esc(t)}</h2>'
def note(t, kind="o"): return f'<div class="note {kind}">{t}</div>'
def ul(items): return '<ul class="r">' + "".join(f"<li>{x}</li>" for x in items) + "</ul>"
def mn(t): return f'<span class="mname">{esc(t)}</span>'
def btag(mid_):
    b = M[mid_].get("basis")
    lab = M[mid_]["label"].lower()
    if b == "adjusted" and len(M[mid_].get("ltcode") or []) > 1: return "" if re.search(r"\badj(?:usted|\.)", lab) else " <span class='tag'>(adj.)</span>"
    return (" <span class='tag'>(reported)</span>" if b == "reported" and not re.search(r"\b(reported|gaap)\b", lab) else "")
def cons_cell(mid_):
    m = M[mid_]; cs = m.get("consensus")
    if not cs: return DASH
    s = T[f"{mid_}.consensus"]
    if cs.get("count") is not None: s += f" ({cs['count']})"
    if cs.get("thin"): s += "†"
    return esc(s)

parts = []
CO = d["company"]; PD = d["period"]; REL = d["release"]; CALL = d["call"]
def _company_pt():
    text = f"{CO['name']} ({CO['ticker']}) - {PD['label']}"
    avail = (8.5 - 1.0) * 72 * 0.5524 - 14
    try:
        try: import pymupdf as fitz
        except ImportError: import fitz
        width = lambda pt: fitz.get_text_length(text, fontname="tibo", fontsize=pt) * 1.12
    except Exception:
        width = lambda pt: len(text) * pt * 0.58
    for hp in MH.get("company_size_ladder_half_points", [28]):
        if width(hp / 2) <= avail: return hp / 2, False
    return MH.get("company_size_ladder_half_points", [20])[-1] / 2, True
CO_PT, CO_WRAP = _company_pt()
BRAND_PT = (MH.get("brand_size_two_line_half_points", 24) if CO_WRAP else MH.get("brand_size_half_points", 33)) / 2
MARK = MH.get("mark_px", {"w": 59, "h": 46}); LOGO = MH.get("logo_file", "insync_logo.png"); BS = MH.get("brand_split", {"orange_word": "InSync", "ink_word": " Analytics"})
parts.append(f'''<table class="mast"><tr>
<td class="logo"><img src="{esc(LOGO)}" alt=""></td>
<td class="mid"><div class="brand"><span class="i">{esc(BS.get("orange_word", "InSync"))}</span><span class="a">{esc(BS.get("ink_word", " Analytics"))}</span></div>
<div class="co">{esc(CO["name"])} <span class="tk">({esc(CO["ticker"])})</span> - {esc(PD["label"])}</div></td>
<td class="rt"><div class="ew">{esc(cfg.get("product", "Earnings Review"))}</div>
<div class="pr"><b>Prepared: </b>{esc(d["prepared"])}</div>
<div class="pr"><b>Reported: </b>{esc(rel_disp())}</div></td>
</tr></table><div class="orule"></div>''')

parts.append('<div class="sub dark">Share Price Reaction and Valuation</div>')
R = d["reaction"]
parts.append(table(["Earnings release date", "Earnings call date", "Pre-earnings price", "Post-earnings price", "Price change", "Market cap", "EV", "P/E (NTM)", "EV/EBITDA (NTM)"],
    [[esc(f"{T['f.release_display']}") if "f.release_display" in T else esc(rel_disp()), esc(T.get("f.call_display", CALL["display"])),
      T["reaction.pre_price"], T["reaction.post_price"], sgn(T["reaction.chg"]) + f" (index {esc(T['reaction.index_chg'])})",
      T["val.mcap"], T["val.ev"], T["val.pe_ntm"], T["val.ev_ebitda_ntm"]]],
    [14, 11, 10, 10, 13, 10, 9, 11, 12], aligns=["l"] + ["r"] * 8, keep=True))
VH = c.get("valuation_history") or {}
VLAB = {"pe": ("P/E (adj.)", "val.pe_ntm"), "ev_ebitda": ("EV/EBITDA (adj.)", "val.ev_ebitda_ntm"), "pb": ("P/B", "val.pb"), "pe_gaap": ("P/E (reported)*", None),
        "ev_ebitda_gaap": ("EV/EBITDA (reported)*", None), "ev_sales": ("EV/Sales", None)}
vrows = []
for k in ("pe", "ev_ebitda", "pb", "pe_gaap", "ev_ebitda_gaap", "ev_sales"):
    if k not in VH: continue
    lab, ntm = VLAB[k]; p_ = f"val.hist.{k}."
    rng3 = DASH if T[p_ + "min3y"] == DASH else f"{T[p_ + 'min3y']}\u2060–\u200b{T[p_ + 'max3y']}"
    vrows.append([mn(lab), esc(T.get(ntm, DASH)) if ntm else DASH, esc(T[p_ + "ltm"]), esc(T[p_ + "avg1y"]), esc(T[p_ + "avg3y"]), esc(rng3), sgn(T[p_ + "vs1y"]), sgn(T[p_ + "vs3y"])])
if vrows:
    parts.append('<div class="sub dark">Valuation versus Own History</div>')
    parts.append(table(["Multiple", "Current NTM", "Current LTM", "1Y avg (LTM)", "3Y avg (LTM)", "3Y range (LTM)", "LTM vs 1Y avg", "LTM vs 3Y avg"], vrows,
                       [18, 11, 11, 12, 12, 14, 11, 11], aligns=["l"] + ["r"] * 7, keep=True))

parts.append('<div class="ex"><h3>THE PRINT</h3>' + ul([sub(x) for x in pr["the_print"]]) + '</div>')

parts.append(sec("Analytical Summary"))
parts.append('<div class="ex"><h3>INITIAL THOUGHTS ON RESULTS</h3>' + ul([bold_lead(x["lead"], x["text"]) for x in pr["summary"]]) + '</div>')

SC = cfg["scorecard"]
parts.append(sec("KPI Scorecard: Print vs Consensus"))
rows = []
for band in SC["bands"]:
    rows.append(("band", band["label"]))
    for mid_ in band["rows"]:
        m = M[mid_]; cm = CM[mid_]
        actual = neg(T[f"{mid_}.actual"]) if cm["actual"] is not None or m.get("actual_text") else DASH
        surprise = sgn(T[f"{mid_}.surprise"]) if cm["surprise"] is not None else DASH
        guide = esc(T[f"{mid_}.guide_prior"])
        vsg = m.get("vs_guide_text") or (T[f"{mid_}.vs_guide"] if cm["vs_guide_mid"] is not None else DASH)
        comment = sub(pr.get("scorecard_comments", {}).get(mid_, ""))
        lab_ = mn(m["label"]) + btag(mid_) + (" <span class='tag'>(Primary KPI)</span>" if mid_ == cfg.get("primary_kpi") else "")
        rows.append([lab_, actual, cons_cell(mid_), surprise, guide, sgn(vsg) if vsg != DASH else DASH, comment])
parts.append(table(["Metric", PD["label"], PD["label"], "Beat / (Miss)", "Guide", "vs Guide", "Comment"], rows,
                   [20, 9, 11, 9, 10, 8, 33], aligns=["l", "r", "r", "r", "r", "r", "l"], sub_heads=["", "(Actual)", "(Consensus)", "", SC.get("guide_date_label", ""), "(midpoint)", ""]))
_ls = c.get("large_surprise") or []
_ln = " ".join(f"{esc(x['label'])}: surprise beyond {'15%' if M[x['id']].get('kind', 'level') == 'level' else '200 bps'}; actual and consensus are both on {esc(str(x['code']))}." for x in _ls if x.get("code") == x.get("cons_code"))
if pr.get("scorecard_note") or _ln: parts.append(note((sub(pr["scorecard_note"]) + " " if pr.get("scorecard_note") else "") + _ln, "k"))

parts.append(sec("What Changed"))
rows3 = [[mn(subtxt(x["change"])), sub(x["facts"]), sub(x["mgmt"]), sub(x["view"])] for x in pr["what_changed"]]
parts.append(table(["Change", "Reported facts", "Management commentary (verbatim)", "Interpretation"], rows3, [16, 32, 26, 26], aligns=["l"] * 4, keep=True))

parts.append(sec("Segment and KPI Review: Quarterly KPI Evolution"))
rows4 = []
for band in cfg["evolution"]["bands"]:
    rows4.append(("band", band["label"]))
    for r in band["rows"]:
        mid_ = r["id"]; m = M[mid_]; cm = CM[mid_]
        rows4.append([mn(m["label"])] + [neg(T[f"{mid_}.v.{p}"]) for p in P])
        if r.get("yoy"):
            _nat = r["yoy"] == "native" and any(v is not None for v in (m.get("native_yoy") or []))
            lab = r.get("yoy_label") or ("Y/Y Growth (InSync native)" if _nat else ("Y/Y Change" if m.get("kind") == "rate" else "Y/Y Growth"))
            rows4.append(("sub", [f"<span class='ind'>{esc(lab)}</span>"] + [sgn(T[f"{mid_}.yoy.{p}"]) if T[f"{mid_}.yoy.{p}"] != DASH else DASH for p in P]))
parts.append(table(["Metric"] + P, rows4, [34] + [66 / n] * n, aligns=["l"] + ["r"] * n, sub_heads=[""] + ["(Actual)"] * n))

GC = cfg["guidance"]; F = d.get("forward", {})
parts.append(sec("Guidance Evolution: Guide vs Actual vs Consensus, and the New Guide"))
def tag(t): return f" <span class='tag'>{esc(t)}</span>"
def grow(mid_):
    m = M[mid_]; cm = CM[mid_]
    cn = m.get("consensus_next")
    return [mn(m["label"]), esc(T[f"{mid_}.guide_prior"]), cons_cell(mid_), f"<b>{neg(T[f'{mid_}.actual'])}</b>",
            sgn(T[f"{mid_}.vs_guide"]) if cm["vs_guide_mid"] is not None else DASH, sgn(T[f"{mid_}.surprise"]) if cm["surprise"] is not None else DASH,
            esc(T[f"{mid_}.guide_new"]), (esc(T[f"{mid_}.consensus_next"]) + tag(cn.get("source", GC.get("post_tag", "post-print")).replace("InSync ", ""))) if cn else DASH]
rows6 = [("band", GC["band_label"])] + [grow(x) for x in GC["rows"]]
for x in d.get("guidance_extra_rows", []):
    rows6.append([mn(x["label"]), esc(x["prior"]), esc(x["consensus"]), f"<b>{neg(x['actual'])}</b>", sgn(x["vs_guide"]), sgn(x["vs_cons"]), esc(x["new"]), esc(x["consensus_next"])])
e = GC.get("eps_row"); GV = cfg.get("guide_vs_frozen"); GVL = GV if isinstance(GV, list) else ([GV] if GV else [])
fe_key = next((g["forward"] for g in GVL if g.get("metric") == e and str(g.get("forward", "")).endswith("_next_q") and g["forward"] in F), None) or \
         next((k for k, f_ in F.items() if f_.get("metric") == e and k.endswith("_next_q")), None) or ("eps_next_q" if "eps_next_q" in F else None)
if e:
    frozen_cell = (esc(f"{T[f'fwd.{fe_key}.pre']} ({T[f'fwd.{fe_key}.pre_n']})") + tag(GC.get("frozen_tag", "pre-print"))) if fe_key and f"fwd.{fe_key}.pre" in T else DASH
    _xc = (F.get(fe_key) or {}).get("pre_code") if fe_key else None
    if frozen_cell != DASH and _xc and _xc != d["metrics"][e].get("resolved_code"): frozen_cell += tag(f"on {_xc}")
    rows6.append([mn(M[e]["label"].replace("(GAAP, diluted)", "(diluted, $)")), esc(T[f"{e}.guide_prior"]), cons_cell(e), f"<b>{esc(GC.get('eps_actual_text') or T[f'{e}.actual'])}</b>",
                  sgn(T[f"{e}.vs_guide"]), sgn(T[f"{e}.surprise"]), esc(T[f"{e}.guide_new"]), frozen_cell])
rv = GC.get("revenue_row")
if rv:
    rid = rv["id"]; fr = F.get("rev_next_q")
    rows6.append([mn(M[rid]["label"]), esc(T[f"{rid}.guide_prior"]), cons_cell(rid), f"<b>{esc(T[f'{rid}.actual'])}</b>", DASH, sgn(T[f"{rid}.surprise"]),
                  esc(f"~{T['i.' + rv['implied']]} {rv.get('implied_label', 'implied*')}") if rv.get("implied") else (esc(T[f"{rid}.guide_new"]) if M[rid].get("guide_new") else DASH),
                  (esc(f"{T['fwd.rev_next_q.pre']} ({T['fwd.rev_next_q.pre_n']})") + tag(GC.get("frozen_tag", "pre-print"))) if fr else DASH])
FYL = c.get("fy_rows") or d.get("fy_guide"); FYL = [FYL] if isinstance(FYL, dict) else (FYL or [])
def fy_cons_cell(FY, px):
    if FY.get("consensus_text"): return sub(FY["consensus_text"])
    if px + "consensus" not in T: return DASH
    s = esc(T[px + "consensus"]) + tag(T.get(px + "consensus_src", ""))
    if px + "consensus_growth" in T: s += "<br>" + sgn(T[px + "consensus_growth"]) + tag("Y/Y implied")
    return s
if FYL:
    rows6.append(("band", GC.get("fy_band_label", "Full year")))
for fi, FY in enumerate(FYL):
    px = "fy." if fi == 0 else f"fy{fi+1}."
    def _tg(val, t_): return tag(t_) if (t_ and val != DASH) else ""
    rows6.append([mn(FY["label"]), esc(T[px + "prior"]) + _tg(T[px + "prior"], FY.get("prior_tag", "")), fy_cons_cell(FY, px), f"<b>{sub(T[px + 'ytd'])}</b>", sgn(subtxt(FY.get("vs_guide_text", DASH))), DASH,
                  esc(T[px + "new"]) + _tg(T[px + "new"], FY.get("new_tag", "")), esc(T[px + "consensus_next"]) + _tg(T[px + "consensus_next"], FY.get("consensus_next_tag", ""))])
parts.append(table(["Guided item", GC["col_prior"], f"Consensus {PD['label']}", f"Actual {PD['label']}", "Actual vs Guide", "Actual vs Consensus", GC["col_new"], f"Consensus {PD['next_label']}"], rows6,
                   [17, 11, 11, 13, 9, 9, 15, 15], aligns=["l"] + ["r"] * 7, sub_heads=["", GC["col_prior_sub"], "(pre-print / †)", "(reported)", "(vs midpoint)", "", GC["col_new_sub"], "(pre-print or post-print)"]))
parts.append(note(sub(pr["guidance_read"]), "g"))
rev_bits = []
for key in ["eps_next_q", "rev_next_q", "eps_fy0", "eps_fy1"] + [k for k in F if k not in ("eps_next_q", "rev_next_q", "eps_fy0", "eps_fy1")]:
    if key in F:
        s = f"{esc(T[f'fwd.{key}.label'])} {esc(T[f'fwd.{key}.pre'])} → {esc(T[f'fwd.{key}.post'])}, {sgn(T[f'fwd.{key}.chg'])}"
        if F[key].get("post_down") is not None: s += f" ({T[f'fwd.{key}.post_down']} of {T[f'fwd.{key}.post_n']} cut)"
        _rc = (d["metrics"].get(F[key].get("metric")) or {}).get("resolved_code")
        if F[key].get("pre_code") and _rc and F[key]["pre_code"] != _rc: s += f" (on {esc(F[key]['pre_code'])}, not the graded {esc(_rc)})"
        rev_bits.append(s)
parts.append(note(f"<b>Post-print revisions (InSync, pre-print snapshot on or before {esc(d['consensus']['frozen_date'])} → {esc(d['consensus']['post_print_date'])}):</b> " + "; ".join(rev_bits) + ". " + sub(pr.get("revisions_tail", "")), "o"))

TN = pr["tone"]
parts.append(sec("Management Tone Shift"))
parts.append(table(["Dimension", subtxt(TN["prior_label"]), subtxt(TN["this_label"])], [[mn(x["dim"]), sub(x["prior"]), sub(x["this"])] for x in TN["rows"]], [16, 40, 44], aligns=["l"] * 3, keep=True))
if TN.get("note"): parts.append(note(sub(TN["note"]), "k"))

parts.append(sec("What to Monitor"))
parts.append('<ol class="r">' + "".join(f"<li>{bold_lead(x['lead'], x['text'])}</li>" for x in pr["monitor"]) + "</ol>")

OVL = d.get("overrides_log", []); ovs = ""
KU = d.get("kpi_universe") or {}
if KU.get("provenance") == "mcp_kpi_tier": ovs += f" KPI series resolved from InSync's full KPI tiers; {esc(CO['ticker'])} carries no top_kpi tagging."
if KU.get("provenance") == "mcp_discovery": ovs += f" KPI series resolved by InSync line-item discovery; {esc(CO['ticker'])} carries no KPI-tier tagging in InSync."
def _lab(mid_): return re.sub(r"\s*\((\$M|\$B|diluted|x|%)\)", "", M[mid_]["label"])
def _grp(kind):
    g = {}
    for x in OVL:
        if x["kind"] == kind: g.setdefault(_lab(x["metric"]), []).append(x["key"])
    return g
gv, gg = _grp("value"), _grp("guide")
NGD = d.get("not_graded") or {}
_bc = [k for k, m in M.items() if m.get("basis_conflict")]
if _bc: ovs += " Not graded because InSync's pre-print consensus is on a different line item from the reported actual: " + "; ".join(esc(_lab(k)) for k in _bc) + "."
_rc = CO.get("reporting_currency", "USD"); _pc = (d.get("reaction") or {}).get("price_currency") or _rc
if _rc != "USD" or _pc != _rc: ovs += f" Figures in {esc(_rc)}" + (f"; share prices in {esc(_pc)}." if _pc != _rc else ".")
if (c.get("valuation_check") or {}).get("ok") is False: ovs += " InSync's valuation for this issuer failed a price and EPS consistency check; market cap, EV and multiples are not shown."
if (d.get("reaction") or {}).get("price_source"): ovs += " Share prices are InSync adjusted closes (dividend and split adjusted); no volume."
if (d.get("provenance") or {}).get("confirm_stale"): ovs += f" Reviewed although a later period has reported: {esc(d['provenance']['confirm_stale'])}."
if NGD: ovs += " Not graded although InSync carries a consensus: " + "; ".join(f"{esc(_lab(k))} ({esc(v)})" for k, v in NGD.items() if k in M) + "."
if gv: ovs += " Document figures in place of InSync: " + "; ".join(f"{esc(k)} {esc(', '.join(sorted(v, key=lambda p_: (p_[-2:], p_))))}" for k, v in gv.items()) + "."
if gg: ovs += " Guidance from documents: " + ", ".join(esc(k) for k in gg) + "."
gs = {}
for x in OVL:
    if x["kind"] == "stated_change": gs.setdefault((M[x["metric"]].get("stated_yoy") or {}).get("basis", "stated"), []).append(_lab(x["metric"]))
for bas, labs in gs.items(): ovs += f" Changes quoted on the company's {esc(bas)} basis: {esc(', '.join(labs))}."
ex = [x for x in OVL if x["kind"] == "exclude" and x["key"] in P]
if ex:
    exm = sorted({x["metric"] for x in ex}, key=list(M).index); exp = [p_ for p_ in P if any(x["key"] == p_ for x in ex)]
    rs = list(dict.fromkeys(x.get("reason") or "different basis" for x in ex))
    ovs += f" Shown as dashes: {len(exm)} lines in {esc(', '.join(exp))} ({esc('; '.join(rs))})."
parts.append('<div class="src"><b>Source:</b> ' + sub(pr["source"]) + ovs + f" Earnings Review skill {VERSION}." + " DISCLOSURE: " + esc(brand["disclosure"]) + "</div>")

CSS = """
@page{size:letter;margin:0.5in 0.5in 0.62in 0.5in}
*{box-sizing:border-box;-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{margin:0;font-family:Calibri,Carlito,Arial,sans-serif;color:__INK__;font-size:9.7pt;line-height:1.28}
p{margin:0 0 4px}
.mast{width:100%;border:1px solid __BOX__;border-collapse:collapse;table-layout:fixed}
.mast td{vertical-align:bottom;padding:4px 7px}
.mast .logo{width:10%;text-align:left} .mast .logo img{width:59px;height:46px;display:block}
.mast .mid{width:55.24%} .mast .rt{width:34.76%;text-align:right;vertical-align:top}
.brand{font-family:Georgia,"Liberation Serif",serif;font-size:16.5pt;line-height:1.05;margin:0}
.brand .i{color:__ORANGE__;font-weight:bold} .brand .a{color:__DARK__}
.co{font-family:Georgia,"Liberation Serif",serif;font-weight:bold;font-size:14pt;color:__DARK__;margin:0;white-space:nowrap} .co .tk{color:__ORANGE__}
.ew{font-family:Georgia,"Liberation Serif",serif;font-weight:bold;font-size:18pt;color:__DARK__;line-height:1.05}
.pr{font-family:Georgia,"Liberation Serif",serif;font-size:9pt;color:__GREY__} .pr b{color:__ORANGE__}
.orule{border-bottom:1.75pt solid __ORANGE__;margin:3px 0 7px;height:0}
.ex{background:__EXBG__;border-left:2.5pt solid __ORANGE__;padding:7px 11px 8px 12px;margin:10px 0 8px;break-inside:avoid}
.ex h3{font-size:9pt;color:__DARK__;margin:0 0 4px;font-weight:bold;letter-spacing:.3px}
.ex ul.r li{font-size:9.3pt}
h2.sec{font-family:Calibri,Carlito,Arial,sans-serif;font-size:12.5pt;font-weight:bold;color:__DARK__;margin:20px 0 7px;padding-bottom:3px;border-bottom:1.5pt solid __ORANGE__;break-after:avoid;page-break-after:avoid}
h2.sec+*{break-before:avoid;page-break-before:avoid}
table.data.keep{break-inside:avoid;page-break-inside:avoid}
table.data thead tr{break-after:avoid;page-break-after:avoid}
table.data tr.band{break-after:avoid;page-break-after:avoid}
.sub{font-size:8.5pt;font-weight:bold;color:__GREY__;letter-spacing:.4px;margin:12px 0 3px;break-after:avoid}
.sub.dark{color:__DARK__;font-size:9.5pt;letter-spacing:.2px}
ul.r,ol.r{margin:3px 0 8px;padding-left:15px} ul.r li,ol.r li{font-size:9.3pt;line-height:1.3;margin:2px 0} ul.r li::marker,ol.r li::marker{color:__ORANGE__}
.note{padding:6px 10px;margin:6px 0 10px;font-size:9pt;color:__INK__;background:__EXBG__;break-inside:avoid;break-before:avoid}
.note.o{border-left:2.75pt solid __ORANGE__} .note.g{border-left:2.75pt solid __GREEN__} .note.k{border-left:2.75pt solid __DARK__}
table.data{border-collapse:collapse;width:100%;margin:4px 0 10px;table-layout:fixed}
table.data thead{display:table-header-group}
table.data tr{break-inside:avoid;page-break-inside:avoid}
table.data th{background:__HEADBG__;color:__DARK__;font-weight:bold;font-size:7.4pt;padding:4px 6px;border:0;border-bottom:.7pt solid __HRULE__;vertical-align:bottom}
table.data th .th2{font-weight:normal;font-size:6.6pt;color:__GREY__}
table.data td{font-size:8.3pt;padding:3.2px 6px;border:0;border-bottom:.25pt solid __RRULE__;font-variant-numeric:tabular-nums;word-wrap:break-word;color:__INK__;vertical-align:top}
table.data th.l,table.data td.l{text-align:left} table.data th.r,table.data td.r{text-align:right}
table.data td .mname{color:__DARK__;font-weight:600}
table.data tr.subrow td{font-size:7.8pt;color:__LABEL__;padding-top:1.6px;padding-bottom:1.6px} .ind{padding-left:9px}
tr.band td{background:__BAND__;font-weight:bold;font-size:7.4pt;color:__DARK__}
table.data tr.z td{background:__ZEBRA__}
.up{color:__GREEN__;font-weight:bold} .dn{color:__RED__;font-weight:bold} .tag{font-size:6.3pt;color:__GREY__;font-weight:normal}
.src{margin-top:6px;border-top:.5pt solid __GRID__;padding-top:4px;font-size:6.2pt;color:__GREY__;line-height:1.3}
"""
for k, v in C.items(): CSS = CSS.replace(f"__{k}__", v)
_TF, _BF = FONTS.get("title", "Georgia"), FONTS.get("body", "Calibri")
CSS = (CSS.replace("16.5pt", f"{BRAND_PT:g}pt").replace("font-size:14pt;color", f"font-size:{CO_PT:g}pt;color")
          .replace("width:59px;height:46px", f"width:{MARK.get('w', 59)}px;height:{MARK.get('h', 46)}px").replace("Georgia,", f"{_TF},").replace("Calibri,", f"{_BF},"))
if CO_WRAP: CSS = CSS.replace("margin:0;white-space:nowrap", "margin:0;white-space:normal")
doc = f'<!DOCTYPE html><html><head><meta charset="utf-8"><title>{esc(CO["name"])} ({esc(CO["ticker"])}) {esc(PD["label"])} Earnings Review</title><style>{CSS}</style></head><body>{"".join(parts)}</body></html>'
out = os.path.join(W, f"{CO['ticker']}_{PD['label']}_Earnings_Review.html")
open(out, "w", encoding="utf-8").write(doc)
logo_dst = os.path.join(W, LOGO)
if not os.path.exists(logo_dst):
    import shutil; shutil.copy(os.path.join(ROOT, "assets", LOGO), logo_dst)
json.dump({"unresolved_tokens": sorted(set(UNRESOLVED))}, open(os.path.join(W, "build_report.json"), "w"), indent=1)
print(f"saved {out}; unresolved tokens: {sorted(set(UNRESOLVED)) or 'none'}")
