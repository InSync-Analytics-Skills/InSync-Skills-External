"""
sector_modules.py - Base + Sector-Module architecture for the capital-allocation report.

The report SPINE is shared and unchanged across sectors (Executive Summary, Allocation Mix,
Shareholder Returns, Reinvestment, Balance Sheet, Peer Comparison, Outcome Metrics, Monitoring,
Scorecard). What differs by sector is the OUTCOME lens - the measures that actually tell you
whether capital allocation created value - because an FCF/ROIC frame is wrong for a REIT (AFFO),
a bank (ROTCE / TBV / CET1) or an insurer (book-value growth / combined ratio).

Design:
  - Each sector is a Module exposing outcome(ctx) -> dict | None.
  - DefaultModule.outcome() returns None  => compute/render keep the existing tech/FCF behaviour
    UNCHANGED (the tech path is therefore byte-identical; modules are strictly additive).
  - A non-default module returns {"rows": [...], "verdict", "verdict_basis", "score", "notes"};
    when present, the renderer swaps the Outcome-metrics table for the sector rows.

ctx is a plain dict of already-parsed values + helper fns handed in by compute() so a module does
NO raw parsing of its own (keeps all fetch/parse logic in one place). See compute_capalloc.compute.

Every figure a module emits is still deterministic and sourced from the same MCP rows; a module
never invents a number and never editorialises (neutral, evidence-first register).
"""


class SectorModule:
    key = "_default"
    label = "Default"

    _WITHDRAWN_TEXT = {
        "capex": ("Capital expenditure", ["Not a deployment channel for this sector"],
                  ["Unscored - weight withdrawn, not assumed"]),
    }

    def outcome(self, ctx):
        return None

    @staticmethod
    def disclose(methodology, ov):
        keys = set(ov or {})
        out = [r for r in (methodology or []) if r[1] in keys]
        shown = {r[1] for r in out}
        for k, v in (ov or {}).items():
            if v is None and k not in shown and k in SectorModule._WITHDRAWN_TEXT:
                _lbl, _inp, _bands = SectorModule._WITHDRAWN_TEXT[k]
                out.append((_lbl, k, list(_inp), list(_bands)))
        return out


class DefaultModule(SectorModule):
    key = "_default"
    label = "Default (FCF / ROIC)"


class REITModule(SectorModule):
    key = "REIT"
    label = "Real Estate (REIT)"

    def outcome(self, ctx):
        gv, series = ctx["gv"], ctx["series"]
        by, years = ctx["by_period"], ctx["years"]
        cur, span, coc = ctx["cur"], ctx["span"], ctx["coc"]
        safe_div, cagr, ssum = ctx["safe_div"], ctx["cagr"], ctx["ssum"]
        fmt_usd, fmt_pct, fmt_x, fmt_ps = ctx["fmt_usd"], ctx["fmt_pct"], ctx["fmt_x"], ctx["fmt_ps"]
        period = ctx["period"]

        affo_s = series(by, years, "affo")
        if not any(v is not None for v in affo_s):
            affo_s = series(by, years, "ffo")
        div_s, sh_s = ctx["div_s"], ctx["sh_s"]
        sh_o_first = ctx.get("sh_first_obs")
        sh_o_last = ctx.get("sh_last_obs")
        sh_o_idx = ctx.get("sh_obs_idx") or []
        span_obs = ctx.get("sh_span_obs") or span

        affo_latest = next((v for v in reversed(affo_s) if v is not None), None)
        affo_first = next((v for v in affo_s if v is not None), None)
        div_latest = div_s[-1] if div_s else None
        cum_div = ssum(div_s)
        cum_affo = ssum(affo_s)

        _affo_at_first = affo_s[sh_o_idx[0]] if sh_o_idx else None
        _affo_at_last = affo_s[sh_o_idx[-1]] if sh_o_idx else None
        if _affo_at_first is None:
            _affo_at_first = affo_first
        if _affo_at_last is None:
            _affo_at_last = affo_latest
        affo_ps_first = safe_div(_affo_at_first, sh_o_first) if (_affo_at_first is not None and sh_o_first) else None
        affo_ps_last = safe_div(_affo_at_last, sh_o_last) if (_affo_at_last is not None and sh_o_last) else None
        affo_ps_cagr = cagr(affo_ps_first, affo_ps_last, span_obs)

        payout = safe_div(div_latest, affo_latest)
        coverage = safe_div(affo_latest, div_latest)
        cum_payout = safe_div(cum_div, cum_affo)
        leverage = safe_div(ctx.get("net_debt"), ctx.get("ebitda"))
        sh_first = sh_o_first
        sh_last = sh_o_last
        _div_at_first = (div_s[sh_o_idx[0]] if (sh_o_idx and div_s and len(div_s) > sh_o_idx[0]) else None)
        _div_at_last = (div_s[sh_o_idx[-1]] if (sh_o_idx and div_s and len(div_s) > sh_o_idx[-1]) else None)
        if _div_at_first is None:
            _div_at_first = (div_s[0] if div_s else None)
        if _div_at_last is None:
            _div_at_last = (div_s[-1] if div_s else None)
        dps_first = safe_div(_div_at_first, sh_first) if (_div_at_first is not None and sh_first) else None
        dps_last = safe_div(_div_at_last, sh_last) if (_div_at_last is not None and sh_last) else None
        dps_cagr = cagr(dps_first, dps_last, span_obs)
        sh_growth_ann = cagr(sh_first, sh_last, span_obs) if (sh_first and sh_last) else None
        sh_growth_cum = ((sh_last / sh_first - 1.0) * 100.0) if (sh_first and sh_last) else None
        acq_s = ctx.get("acq_s") or []
        capex_s = ctx.get("capex_s") or []
        cum_acq = ssum([abs(v) for v in acq_s if v is not None]) if acq_s else None
        cum_capex = ssum([abs(v) for v in capex_s if v is not None]) if capex_s else None
        ext_share = safe_div(cum_acq, ssum([cum_acq, cum_capex])) if cum_acq is not None else None
        accretive = (affo_ps_cagr is not None and affo_ps_cagr > 0)

        rows = [
            ["AFFO per share (CAGR)", fmt_pct(affo_ps_cagr, signed=True) if affo_ps_cagr is not None else "n/a",
             period, "Per-share AFFO compounding"],
            ["AFFO payout ratio", fmt_pct(payout * 100) if payout is not None else "n/a",
             "Latest FY", "Dividends declared / AFFO"],
            ["Dividend coverage", fmt_x(coverage) if coverage is not None else "n/a",
             "Latest FY", "AFFO / dividends declared"],
            ["Dividend per share (CAGR)", fmt_pct(dps_cagr, signed=True) if dps_cagr is not None else "n/a",
             period, "Income growth per share"],
            ["Dividends declared", fmt_usd(cum_div, cur) if cum_div is not None else "n/a",
             period, (f"{fmt_pct(cum_payout*100)} of cumulative AFFO" if cum_payout is not None else "—")],
            ["Leverage (Net Debt / EBITDA)", fmt_x(leverage) if leverage is not None else "n/a",
             "Latest reported quarter", "Reported leverage"],
            ["Share count change, cumulative", fmt_pct(sh_growth_cum, signed=True) if sh_growth_cum is not None else "n/a",
             period, (f"{fmt_pct(sh_growth_ann, signed=True)}/yr annualized - equity funds external growth"
                      if sh_growth_ann is not None else "Equity funds external growth")],
        ]

        score = 5.0
        flags = []
        if affo_ps_cagr is not None:
            if affo_ps_cagr > 4: score += 1.5; flags.append(f"AFFO/share compounding {fmt_pct(affo_ps_cagr, signed=True)}")
            elif affo_ps_cagr > 0: score += 0.5
            else: score -= 1.5; flags.append("AFFO/share not growing")
        if dps_cagr is not None:
            if dps_cagr > 4: score += 1; flags.append(f"dividend/share {fmt_pct(dps_cagr, signed=True)}")
            elif dps_cagr > 0: score += 0.5
            else: score -= 1; flags.append("dividend/share not growing")
        if coverage is not None:
            if coverage >= 1.15: score += 1; flags.append(f"AFFO covers the dividend {fmt_x(coverage)}")
            elif coverage >= 1.0: score += 0.5
            else: score -= 1.5; flags.append(f"AFFO does not cover the dividend ({fmt_x(coverage)})")
        if leverage is not None:
            if leverage <= 6: score += 0.5
            elif leverage > 8: score -= 1; flags.append(f"leverage {fmt_x(leverage)}")
        score = int(round(max(0, min(10, score))))
        verdict = ("Value Creating" if score >= 7 else "Value Neutral" if score >= 5 else "Value Destructive")
        drivers = ", ".join(flags[:3]) if flags else "mixed signals"
        basis = (f"{verdict}: REIT outcome score {score}/10 (>=7 Value Creating, 5-6 Neutral, <5 Destructive). "
                 f"Judged on AFFO per-share growth, dividend-per-share growth, AFFO coverage of the dividend and "
                 f"leverage. Driven by {drivers}.")

        def _p(x, signed=False):
            return "n/a" if x is None else fmt_pct(x, signed=signed)
        ov = {"value_creation": score}
        if payout is not None:
            ov["dividends"] = 8 if payout <= 0.85 else 6 if payout <= 1.0 else 4
        if leverage is not None:
            ov["balance_sheet"] = 8 if leverage <= 5.5 else 6 if leverage <= 6.5 else 5 if leverage <= 8 else 3
        ma = 5
        if ext_share is not None and ext_share >= 0.6:
            ma += 2 if accretive else -1
        elif ext_share is not None and ext_share >= 0.3:
            ma += 1 if accretive else 0
        if leverage is not None and leverage <= 6.5:
            ma += 0.5
        ov["m_and_a"] = int(round(max(0, min(10, ma))))
        ov["capex"] = None
        cd = 7
        if leverage is not None and leverage > 7: cd -= 2
        if payout is not None and payout > 1.0: cd -= 1
        if accretive is False: cd -= 1
        ov["capital_discipline"] = max(0, min(10, cd))

        acq_txt = fmt_usd(cum_acq, cur) if cum_acq is not None else "n/a"
        ev = {
            "dividends": (f"AFFO payout {_p(payout*100 if payout is not None else None)}; "
                          f"{fmt_x(coverage) if coverage is not None else 'n/a'} AFFO coverage; "
                          f"dividend/share {_p(dps_cagr, signed=True) if dps_cagr is not None else 'n/a'}."),
            "balance_sheet": (f"{fmt_x(leverage) if leverage is not None else 'n/a'} net debt/EBITDA "
                              f"(net-lease norms); interest and maturity ladder to watch."),
            "m_and_a": (f"External growth via acquisitions {acq_txt}"
                        + (f" ({_p(ext_share*100)} of reinvestment)" if ext_share is not None else "")
                        + f"; AFFO/share {_p(affo_ps_cagr, signed=True) if affo_ps_cagr is not None else 'n/a'} "
                          f"({'accretive' if accretive else 'not yet accretive'}); deal yields not disclosed."),
            "capex": None,
            "capital_discipline": (f"External growth part-funded by equity (share count "
                                   f"{_p(sh_growth_cum, signed=True) if sh_growth_cum is not None else 'n/a'} cumulative, "
                                   f"{_p(sh_growth_ann, signed=True) if sh_growth_ann is not None else 'n/a'}/yr); "
                                   f"leverage {fmt_x(leverage) if leverage is not None else 'n/a'}."),
            "value_creation": (f"AFFO/share {_p(affo_ps_cagr, signed=True) if affo_ps_cagr is not None else 'n/a'}; "
                               f"dividend/share {_p(dps_cagr, signed=True) if dps_cagr is not None else 'n/a'}; "
                               f"AFFO coverage {fmt_x(coverage) if coverage is not None else 'n/a'}; "
                               f"leverage {fmt_x(leverage) if leverage is not None else 'n/a'}."),
        }

        dashboard = [
            (_p(affo_ps_cagr, signed=True) if affo_ps_cagr is not None else "n/a", "AFFO/share (CAGR)"),
            (_p(payout * 100 if payout is not None else None), "AFFO payout"),
            (fmt_x(coverage) if coverage is not None else "n/a", "Dividend coverage"),
            (fmt_x(leverage) if leverage is not None else "n/a", "Leverage (ND/EBITDA)"),
        ]

        reinvestment = {
            "heading": "Reinvestment: property acquisitions and development",
            "note": ("For a REIT, growth capital is deployed through property acquisitions and development, not "
                     "maintenance capex. Acquisition spend below is the primary external-growth channel; capex is "
                     "maintenance only."),
        }

        methodology = [
            ("Buybacks", "buybacks",
             ["Buyback yield", "Share-count change"],
             ["8.0-10.0  buying back below NAV", "6.5-7.9  modest buyback",
              "5.0-6.4  none", "<5.0  issuing below NAV"]),
            ("Dividends", "dividends",
             ["AFFO payout ratio"],
             ["8.0  payout <=85% AFFO", "6.0  85-100% AFFO",
              "6.0  85-100% AFFO", "4.0  >100% of AFFO"]),
            ("External growth and development", "m_and_a",
             ["Property acquisitions and development as share of reinvestment",
              "AFFO per-share accretion", "Maintenance capex (immaterial for a REIT)"],
             ["8.0-10.0  acquisition-led & AFFO/share rising", "6.5-7.9  accretive",
              "5.0-6.4  flat AFFO/share", "<5.0  dilutive (AFFO/share falling)"]),
            ("Balance sheet", "balance_sheet",
             ["Net debt / EBITDA", "Maturity ladder"],
             ["8.0-10.0  <=5.5x", "6.5-7.9  5.5-6.5x", "5.0-6.4  6.5-8.0x", "<5.0  >8.0x"]),
            ("Capital discipline", "capital_discipline",
             ["Equity issuance vs NAV", "Leverage control"],
             ["8.0-10.0  issues above NAV, leverage controlled", "6.5-7.9  neutral issuance",
              "5.0-6.4  some dilutive issuance", "<5.0  persistent below-NAV issuance"]),
            ("Value creation (outcome)", "value_creation",
             ["AFFO per share growth", "AFFO payout coverage", "NAV/share growth where available"],
             ["8.0-10.0  AFFO/share >4% & covered", "6.5-7.9  AFFO/share >0%",
              "5.0-6.4  flat", "<5.0  AFFO/share declining"]),
        ]

        affo_yield = safe_div(affo_latest, ctx.get("mcap"))
        snapshot = {"yield_label": "AFFO yield",
                    "yield_value": _p(affo_yield * 100 if affo_yield is not None else None)}
        div_coverage = {"payout_value": _p(payout * 100 if payout is not None else None),
                        "payout_basis": "Dividends / AFFO",
                        "label": "AFFO coverage", "value": fmt_x(coverage) if coverage is not None else "n/a",
                        "basis": "AFFO / dividends declared"}

        return {"rows": rows, "verdict": verdict, "verdict_basis": basis, "score": score,
                "scorecard_overrides": ov, "evidence_overrides": ev,
                "weight_merges": [{"from": "capex", "into": "m_and_a"}],
                "dimension_labels": {"m_and_a": "External growth and development"},
                "dashboard": dashboard, "reinvestment": reinvestment, "methodology": self.disclose(methodology, ov),
                "snapshot": snapshot, "div_coverage": div_coverage,
                "notes": ("REIT outcome lens: the dividend is quasi-mandatory (>=90% of taxable income), so value "
                          "creation is read through AFFO per-share growth, AFFO payout coverage, dividend growth "
                          "and disciplined (accretive) equity funding.")}


class BankModule(SectorModule):
    key = "Banks"
    label = "Banks & Financials"

    def outcome(self, ctx):
        gv, series = ctx["gv"], ctx["series"]
        by, years = ctx["by_period"], ctx["years"]
        span, coe = ctx["span"], ctx["coe"]
        safe_div, cagr = ctx["safe_div"], ctx["cagr"]
        fmt_pct, fmt_x = ctx["fmt_pct"], ctx["fmt_x"]
        fmt_usd, cur = ctx["fmt_usd"], ctx.get("cur", "$")
        period = ctx["period"]
        L = ctx["L"]

        ni_s = ctx["ni_s"]; eq_s = ctx["eq_s"]; sh_s = ctx["sh_s"]
        intang_s = ctx["intang_s"]; div_s = ctx["div_s"]; bb_s = ctx["bb_s"]
        sh_o_first = ctx.get("sh_first_obs")
        sh_o_last = ctx.get("sh_last_obs")
        sh_o_idx = ctx.get("sh_obs_idx") or []
        span_obs = ctx.get("sh_span_obs") or span

        rotce = gv(L, "rotce")
        ni_last = next((v for v in reversed(ni_s) if v is not None), None)
        def _tce(i):
            e = eq_s[i] if i < len(eq_s) else None
            g = intang_s[i] if i < len(intang_s) else None
            return (e - (g or 0)) if e is not None else None
        tce_last = _tce(len(years) - 1)
        if rotce is None and ni_last is not None and tce_last:
            rotce = ni_last / tce_last * 100.0
        tbvps_series = series(by, years, "tbv_per_share")
        tbvps_first = next((v for v in tbvps_series if v is not None), None)
        tbvps_last = next((v for v in reversed(tbvps_series) if v is not None), None)
        if tbvps_first is None or tbvps_last is None:
            _i0 = sh_o_idx[0] if sh_o_idx else 0
            tbvps_first = safe_div(_tce(_i0), sh_o_first) if sh_o_first else None
            tbvps_last = safe_div(tce_last, sh_o_last) if sh_o_last else None
            _tbv_span = span_obs
        else:
            _tbv_span = span
        tbvps_cagr = cagr(tbvps_first, tbvps_last, _tbv_span)
        cet1 = gv(L, "cet1")
        payout = None
        if ni_last:
            tot = (bb_s[-1] or 0) + (div_s[-1] or 0) if (bb_s and div_s) else None
            payout = safe_div(tot, ni_last)
        eps_cagr = cagr(next((v for v in ctx["eps_s"] if v is not None), None),
                        next((v for v in reversed(ctx["eps_s"]) if v is not None), None), span)
        sh_red = None
        if sh_o_first and sh_o_last and sh_o_first > 0:
            sh_red = (sh_o_first - sh_o_last) / sh_o_first * 100.0

        def P(x, s=False): return "n/a" if x is None else fmt_pct(x, signed=s)
        rows = [
            ["ROTCE", P(rotce), "Latest FY", f"{P(coe)} cost of equity"],
            ["Tangible book value / share (CAGR)", P(tbvps_cagr, True), period, "Per-share compounding (TBV, not FCF)"],
            ["CET1 ratio", P(cet1), "Latest FY", "Regulatory capital vs ~11% requirement"],
            ["Total payout ratio", P(payout * 100 if payout is not None else None), "Latest FY", "(Buyback + dividend) / net income"],
            ["EPS growth (CAGR)", P(eps_cagr, True), period, "—"],
            ["Share count change", P(-sh_red if sh_red is not None else None, True), period, "Buyback effect on per-share"],
        ]

        score = 5.0; flags = []
        if rotce is not None:
            if rotce >= coe + 5: score += 1.5; flags.append(f"ROTCE {P(rotce)} above cost of equity")
            elif rotce >= coe: score += 0.5
            else: score -= 1.5; flags.append(f"ROTCE {P(rotce)} below cost of equity")
        if tbvps_cagr is not None:
            if tbvps_cagr > 6: score += 1.5; flags.append(f"TBV/share compounding {P(tbvps_cagr, True)}")
            elif tbvps_cagr > 0: score += 0.5
            else: score -= 1; flags.append("TBV/share not growing")
        if cet1 is not None:
            if cet1 >= 13: score += 0.5
            elif cet1 < 10: score -= 1.5; flags.append(f"CET1 {P(cet1)} (thin headroom)")
        score = int(round(max(0, min(10, score))))
        verdict = ("Value Creating" if score >= 7 else "Value Neutral" if score >= 5 else "Value Destructive")
        drivers = ", ".join(flags[:3]) if flags else "mixed signals"
        basis = (f"{verdict}: bank outcome score {score}/10 (>=7 Value Creating, 5-6 Neutral, <5 Destructive). "
                 f"Driven by {drivers}. Judged on ROTCE vs cost of equity, tangible book value per share growth and "
                 f"CET1 headroom - capital return is governed by regulatory capital, not free cash flow.")

        ov = {"value_creation": score}
        if cet1 is not None:
            ov["balance_sheet"] = 9 if cet1 >= 13 else 7 if cet1 >= 11 else 5 if cet1 >= 10 else 3
        if payout is not None:
            ov["dividends"] = 8 if payout <= 0.5 else 7 if payout <= 0.7 else 6 if payout <= 0.9 else 4
        ov["capital_discipline"] = 7 if (cet1 is None or cet1 >= 11) else 5

        ev = {}
        if "value_creation" in ov:
            ev["value_creation"] = f"ROTCE {P(rotce)} vs {P(coe)} CoE; TBV/share {P(tbvps_cagr, True)}; EPS {P(eps_cagr, True)}."
        if "balance_sheet" in ov:
            ev["balance_sheet"] = f"CET1 {P(cet1)} vs ~11% requirement; capital return sized to headroom."
        if "dividends" in ov:
            ev["dividends"] = f"Total payout {P(payout*100 if payout is not None else None)} of net income (buyback + dividend)."
        if "capital_discipline" in ov:
            ev["capital_discipline"] = f"Returns funded from earnings within CET1 headroom (CET1 {P(cet1)})."

        dashboard = [
            (P(rotce), "ROTCE"),
            (P(tbvps_cagr, True), "TBV/share (CAGR)"),
            (P(cet1), "CET1 ratio"),
            (P(payout * 100 if payout is not None else None), "Total payout"),
        ]
        _rwa_s = series(by, years, "rwa")
        _rwa_first = next((v for v in _rwa_s if v is not None), None)
        _rwa_last = next((v for v in reversed(_rwa_s) if v is not None), None)
        _rwa_cagr = cagr(_rwa_first, _rwa_last, span)
        _rwa_rows = []
        if _rwa_last is not None:
            _rwa_rows.append(["Risk-weighted assets", fmt_usd(_rwa_last, cur),
                              "Latest FY", "The bank's deployed balance sheet"])
        if _rwa_cagr is not None:
            _rwa_rows.append(["RWA growth", P(_rwa_cagr, True), "%s-year CAGR" % span,
                              "Organic deployment: lending and market risk taken on"])
        if _rwa_cagr is not None and tbvps_cagr is not None:
            _rwa_rows.append(["RWA growth vs TBV/share growth",
                              "%s vs %s" % (P(_rwa_cagr, True), P(tbvps_cagr, True)),
                              "%s-year CAGRs" % span,
                              "Whether the capital base compounded with the book it supports"])
        if cet1 is not None:
            _rwa_rows.append(["CET1 ratio", P(cet1), "Latest FY",
                              "Capital held against those risk-weighted assets"])
        reinvestment = {
            "heading": "Reinvestment: organic growth and RWA",
            "note": ("For a bank, capex is immaterial; capital is reinvested by growing "
                     "risk-weighted assets - lending and market risk - and retaining earnings to "
                     "build CET1. Risk-weighted asset growth is the deployment measure, and the "
                     "CET1 ratio is the capital held against it."),
            "rows": _rwa_rows,
        }
        methodology = [
            ("Buybacks", "buybacks", ["Buyback yield", "Share-count change"],
             ["8.0-10.0  buying back below/near TBV", "6.5-7.9  modest", "5.0-6.4  none", "<5.0  issuing"]),
            ("Dividends", "dividends", ["Total payout of net income", "Sustainability vs CET1"],
             ["8.0-10.0  payout <=50%", "6.5-7.9  50-70%", "5.0-6.4  70-90%", "<5.0  >90%"]),
            ("M&A", "m_and_a", ["Deal spend", "Tangible book dilution / earnback"],
             ["8.0-10.0  accretive, short earnback", "6.5-7.9  neutral", "5.0-6.4  dilutive", "<5.0  value-destructive"]),
            ("Capex", "capex", ["Not scored for banks"], ["n/a  capex immaterial"]),
            ("Balance sheet / capital", "balance_sheet", ["CET1 ratio", "Headroom vs requirement"],
             ["8.0-10.0  CET1 >=13%", "6.5-7.9  11-13%", "5.0-6.4  10-11%", "<5.0  <10%"]),
            ("Capital discipline", "capital_discipline", ["Return sized to CET1 headroom", "No capital raise to fund returns"],
             ["8.0-10.0  within headroom", "6.5-7.9  minor", "5.0-6.4  stretched", "<5.0  raised to fund returns"]),
            ("Value creation (outcome)", "value_creation", ["ROTCE vs cost of equity", "TBV/share growth", "EPS growth"],
             ["8.0-10.0  ROTCE >=CoE+5, TBV/share up", "6.5-7.9  ROTCE >=CoE", "5.0-6.4  near CoE", "<5.0  below CoE"]),
        ]
        snapshot = {"yield_label": "ROTCE", "yield_value": P(rotce),
                    "capital_label": "CET1 ratio", "capital_value": P(cet1)}
        return {"rows": rows, "verdict": verdict, "verdict_basis": basis, "score": score,
                "scorecard_overrides": ov, "evidence_overrides": ev,
                "dashboard": dashboard, "reinvestment": reinvestment, "methodology": self.disclose(methodology, ov),
                "snapshot": snapshot,
                "notes": ("Bank outcome lens: capital return is governed by regulatory capital (CET1) and earnings, "
                          "not free cash flow; value creation is read through ROTCE vs cost of equity and tangible "
                          "book value per-share growth.")}


class InsuranceModule(SectorModule):
    key = "Insurance"
    label = "Insurance"

    def outcome(self, ctx):
        gv = ctx["gv"]
        span, coe, period, L = ctx["span"], ctx["coe"], ctx["period"], ctx["L"]
        safe_div, cagr = ctx["safe_div"], ctx["cagr"]
        P, X = ctx["fmt_pct"], ctx["fmt_x"]
        ni_s, eq_s, sh_s = ctx["ni_s"], ctx["eq_s"], ctx["sh_s"]
        div_s, bb_s = ctx["div_s"], ctx["bb_s"]

        def _first(xs):
            return next((v for v in xs if v is not None), None)

        def _last(xs):
            return next((v for v in reversed(xs) if v is not None), None)

        bvps = [safe_div(e, s) for e, s in zip(eq_s, sh_s)]
        _bv_idx = [i for i, v in enumerate(bvps) if v is not None]
        _bv_span = (_bv_idx[-1] - _bv_idx[0]) if len(_bv_idx) >= 2 else None
        bvps_cagr = cagr(_first(bvps), _last(bvps), _bv_span) if _bv_span else None
        ni_last, eq_last = _last(ni_s), _last(eq_s)
        roe = ctx.get("roe_canonical")
        if roe is None:
            roe = safe_div(ni_last, eq_last)
            roe = roe * 100.0 if roe is not None else None
        debt_now, eq_now = gv(L, "total_debt"), gv(L, "equity")
        tot_cap = ((debt_now or 0) + eq_now) if eq_now else None
        d_to_cap = (safe_div(debt_now, tot_cap) * 100.0) if (tot_cap and tot_cap > 0) else None
        payout = safe_div((div_s[-1] or 0) + (bb_s[-1] or 0), ni_last) if ni_last else None

        rows = [
            ["Return on equity", P(roe), "Latest FY", f"{P(coe)} cost of equity"],
            ["Book value / share (CAGR)", P(bvps_cagr, True), period, "Per-share compounding (book value)"],
        ]
        _comb = gv(L, "combined_ratio")
        if _comb is not None:
            rows.append(
                ["Combined ratio", P(_comb), "Latest FY",
                 ("underwriting profitable below 100%" if _comb < 100
                  else "underwriting loss above 100%") + " - reported, not scored"])
        rows += [
            ["Debt / total capital", P(d_to_cap), "Latest FY", "Balance-sheet capacity"],
            ["Total payout", P(payout * 100 if payout is not None else None),
             "Latest FY", "Buyback + dividend / net income"],
        ]
        score, flags = 5.0, []
        if roe is not None:
            if roe >= coe + 4:
                score += 1.5; flags.append(f"ROE {P(roe)} above cost of equity")
            elif roe >= coe:
                score += 0.5
            else:
                score -= 1.5; flags.append(f"ROE {P(roe)} below cost of equity")
        if bvps_cagr is not None:
            if bvps_cagr > 6:
                score += 1.5; flags.append(f"book value/share compounding {P(bvps_cagr, True)}")
            elif bvps_cagr > 0:
                score += 0.5
        if d_to_cap is not None and d_to_cap > 45:
            score -= 1.0; flags.append(f"debt {P(d_to_cap)} of total capital")
        score = int(round(max(0, min(10, score))))
        verdict = ("Value Creating" if score >= 7 else "Value Neutral" if score >= 5 else "Value Destructive")
        basis = (f"{verdict}: outcome score {score}/10. Judged on ROE vs cost of equity, book value per "
                 f"share growth and debt as a share of total capital, not free cash flow.")

        ov = {"capex": None}
        ev = {}
        if d_to_cap is not None:
            ov["balance_sheet"] = 8 if d_to_cap < 25 else 7 if d_to_cap < 35 else 5 if d_to_cap < 45 else 3
            ev["balance_sheet"] = (f"Scored {ov['balance_sheet']}/10 because debt was {P(d_to_cap)} of total "
                                   f"capital against equity of {ctx['fmt_usd'](eq_now, ctx['cur'])}.")
        ov["value_creation"] = score
        ev["value_creation"] = (f"Scored {score}/10 because ROE was {P(roe)} against a {P(coe)} cost of equity, "
                                f"with book value per share {P(bvps_cagr, True)} a year.")
        return {
            "rows": rows, "verdict": verdict, "verdict_basis": basis, "score": score,
            "scorecard_overrides": ov, "evidence_overrides": ev,
            "dashboard": [(P(roe), "Return on equity"),
                          (P(bvps_cagr, True), "Book value/share (CAGR)"),
                          (P(d_to_cap), "Debt / total capital"),
                          (P(payout * 100 if payout is not None else None), "Total payout")],
            "reinvestment": {"heading": "Reinvestment: underwriting and the investment portfolio",
                             "note": ("For an insurer, capital is 'reinvested' by writing more premium and "
                                      "growing the investment portfolio, not through capex; the standard feed "
                                      "carries no capex, OCF or FCF line for this sector.")},
            "snapshot": {"yield_label": "Return on equity", "yield_value": P(roe),
                         "capital_label": "Debt / total capital", "capital_value": P(d_to_cap)},
            "methodology": self.disclose([
                ("Buybacks", "buybacks", ["Buyback yield", "Share-count change"],
                 ["8.0-10.0  buying back with count falling", "6.5-7.9  modest", "5.0-6.4  none", "<5.0  issuing"]),
                ("Dividends", "dividends", ["Payout of net income", "Growth"],
                 ["8.0-10.0  payout <30%", "6.5-7.9  30-50%", "5.0-6.4  50-75%", "<5.0  >75%"]),
                ("M&A", "m_and_a", ["Deal spend", "Book-value dilution"],
                 ["8.0-10.0  accretive", "6.5-7.9  neutral", "5.0-6.4  dilutive", "<5.0  value-destructive"]),
                ("Balance sheet / capital", "balance_sheet", ["Debt / total capital"],
                 ["8.0-10.0  <25%", "6.5-7.9  25-35%", "5.0-6.4  35-45%", "<5.0  >45%"]),
                ("Capital discipline", "capital_discipline", ["Returns funded from earnings"],
                 ["8.0-10.0  within earnings", "6.5-7.9  minor", "5.0-6.4  stretched", "<5.0  raised to fund returns"]),
                ("Value creation (outcome)", "value_creation", ["ROE vs cost of equity", "Book value/share growth"],
                 ["8.0-10.0  ROE >=CoE+4, BV/share up", "6.5-7.9  ROE >=CoE", "5.0-6.4  near CoE", "<5.0  below CoE"]),
            ], ov),
            "notes": ("Insurance outcome lens: capital return is funded from underwriting and investment "
                      "earnings, not free cash flow; value creation is read through ROE versus cost of equity "
                      "and book value per share growth."),
        }


_MODULES = {m.key: m for m in (DefaultModule(), REITModule(), BankModule(), InsuranceModule())}


def get_module(sector_key):
    return _MODULES.get(sector_key, _MODULES["_default"])
