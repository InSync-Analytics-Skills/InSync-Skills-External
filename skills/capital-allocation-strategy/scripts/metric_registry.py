"""
metric_registry.py - single source of truth for every published figure.  (v3 architecture, step 3)

WHY THIS MODULE EXISTS
----------------------
v2 allowed any section to compute its own version of a metric.  That produced published reports
in which the SAME LABEL carried DIFFERENT VALUES:

    issuer A  "Net debt / EBITDA"   two values (4 and 3 occurrences)
    issuer B  "Net debt / EBITDA"   two values (4 and 1 occurrences)
    issuer A  "capital returned"    three values

Each arises when two different inputs are allowed to share one label:
  * reported EBITDA (IS_P_113) vs adjusted EBITDA (IS_P_114);
  * a locally computed ratio vs the vendor's LTM ratio from get_valuation;
  * three different denominators (latest-year FCF, cumulative FCF, cumulative OCF).

The registry makes that structurally impossible.  A metric is identified by the TRIPLE

        (metric_id, period, basis)

and the printed label is DERIVED FROM THE KEY.  Two different values therefore cannot share a
label: if you want a second value you must register a second key, which produces a second,
visibly different label.  There is no code path that yields one label with two values.

CONTRACT
--------
  * register()  is called only by the compute layer.
  * get()/fmt() RAISE on an unregistered key.  A missing metric is a build failure at development
    time, never an "n/a" in a client PDF.
  * unavailable() records a metric as explicitly unavailable WITH A REASON.  fmt() then returns
    the disclosure string rather than a number, and the metric is reported as UNSCORED.
  * The store is frozen before rendering; the renderer can read but not write, and performs no
    arithmetic of its own.

Deliberately dependency-free: standard library only, same as the rest of the skill.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


PERIODS = {
    "LFY":  "latest fiscal year",
    "LTM":  "last twelve months",
    "CUM5": "5-year cumulative",
    "LQ":   "latest quarter",
    "PFY":  "prior fiscal year",
}

BASES = {
    "reported": "reported",
    "adjusted": "adjusted",
    "vendor":   "vendor-supplied",
    "derived":  "derived",
    "n/a":      "",
}

_UNIT_SUFFIX = {"x": "x", "%": "%", "pp": "pp", "$": "", "shares": "", "ratio": ""}


class MetricError(KeyError):
    pass


class _Metric:
    __slots__ = ("metric_id", "period", "basis", "value", "unit", "formula", "numerator",
                 "denominator", "source", "as_of", "label_override", "available", "reason")

    def __init__(self, metric_id, period, basis, value, unit, formula, numerator,
                 denominator, source, as_of, label_override, available, reason):
        self.metric_id = metric_id
        self.period = period
        self.basis = basis
        self.value = value
        self.unit = unit
        self.formula = formula
        self.numerator = numerator
        self.denominator = denominator
        self.source = source
        self.as_of = as_of
        self.label_override = label_override
        self.available = available
        self.reason = reason


class MetricStore:

    def __init__(self):
        self._m = {}
        self._frozen = False
        self._emissions = []
        self._labels = {}


    def define(self, metric_id, display_name):
        self._labels[metric_id] = display_name

    def register(self, metric_id, value, *, period="LFY", basis="reported", unit="$",
                 formula="", numerator="", denominator="", source="InSync MCP",
                 as_of="", label=None):
        if self._frozen:
            raise MetricError("store is frozen; cannot register %s" % metric_id)
        self._validate(period, basis)
        key = (metric_id, period, basis)
        if key in self._m:
            prev = self._m[key].value
            if not _same(prev, value):
                raise MetricError(
                    "conflicting values for %s @%s:%s - already %r, now %r. "
                    "Two different values cannot share one key. Register the second under a "
                    "different period or basis so it gets its own label."
                    % (metric_id, period, basis, prev, value))
            return
        self._m[key] = _Metric(metric_id, period, basis, value, unit, formula, numerator,
                               denominator, source, as_of, label, True, "")

    def unavailable(self, metric_id, reason, *, period="LFY", basis="reported", unit="$",
                    label=None):
        if self._frozen:
            raise MetricError("store is frozen; cannot register %s" % metric_id)
        self._validate(period, basis)
        if not reason:
            raise MetricError("unavailable(%s) requires a reason" % metric_id)
        self._m[(metric_id, period, basis)] = _Metric(
            metric_id, period, basis, None, unit, "", "", "", "", "", label, False, reason)

    def not_applicable(self, metric_id, framework, *, period="LFY", basis="reported",
                       unit="$", label=None):
        self.unavailable(metric_id, "Not applicable for this sector framework.",
                         period=period, basis=basis, unit=unit, label=label)
        self._m[(metric_id, period, basis)].reason = (
            "Not applicable for the %s framework." % framework)

    @staticmethod
    def _validate(period, basis):
        if period not in PERIODS:
            raise MetricError("unknown period %r (allowed: %s)" % (period, sorted(PERIODS)))
        if basis not in BASES:
            raise MetricError("unknown basis %r (allowed: %s)" % (basis, sorted(BASES)))

    def freeze(self):
        self._frozen = True
        return self


    def has(self, metric_id, period="LFY", basis="reported"):
        return (metric_id, period, basis) in self._m

    def is_available(self, metric_id, period="LFY", basis="reported"):
        m = self._m.get((metric_id, period, basis))
        return bool(m and m.available)

    def get(self, metric_id, period="LFY", basis="reported"):
        key = (metric_id, period, basis)
        m = self._m.get(key)
        if m is None:
            raise MetricError(
                "%s @%s:%s was never registered. The renderer may only read metrics the compute "
                "layer computed; add the registration rather than falling back to n/a."
                % (metric_id, period, basis))
        if not m.available:
            raise MetricError("%s @%s:%s is unavailable: %s"
                              % (metric_id, period, basis, m.reason))
        return m.value

    def reason(self, metric_id, period="LFY", basis="reported"):
        m = self._m.get((metric_id, period, basis))
        return m.reason if m else "Metric not registered."


    def label(self, metric_id, period="LFY", basis="reported"):
        m = self._m.get((metric_id, period, basis))
        if m is not None and m.label_override:
            return m.label_override
        stem = self._labels.get(metric_id, metric_id.replace("_", " ").lower().capitalize())
        quals = []
        if period != "LFY":
            quals.append(PERIODS[period])
        if basis in ("adjusted", "vendor"):
            quals.append(BASES[basis])
        return "%s (%s)" % (stem, ", ".join(quals)) if quals else stem

    def fmt(self, metric_id, period="LFY", basis="reported", *, dash="n/a", decimals=None):
        key = (metric_id, period, basis)
        m = self._m.get(key)
        if m is None:
            raise MetricError("%s @%s:%s was never registered" % (metric_id, period, basis))
        if not m.available:
            out = m.reason
        elif m.value is None:
            out = dash
        else:
            out = _render(m.value, m.unit, decimals)
        self._emissions.append((key, out))
        return out


    def audit(self):
        by_label = {}
        for key, text in self._emissions:
            by_label.setdefault(self.label(*key), set()).add(text)
        return {lab: sorted(v) for lab, v in by_label.items() if len(v) > 1}

    def coverage(self, required):
        if not required:
            return 1.0, []
        missing = [r for r in required
                   if not self.is_available(*(r if isinstance(r, tuple) else (r,)))]
        return (len(required) - len(missing)) / float(len(required)), missing

    def unavailable_report(self):
        return [{"metric": m.metric_id, "period": m.period, "basis": m.basis,
                 "reason": m.reason}
                for m in self._m.values() if not m.available]

    def provenance_table(self):
        rows = []
        for (mid, per, bas), m in sorted(self._m.items()):
            rows.append({
                "metric": self.label(mid, per, bas),
                "formula": m.formula,
                "numerator": m.numerator,
                "denominator": m.denominator,
                "period": PERIODS[per],
                "basis": BASES[bas] or "-",
                "unit": m.unit,
                "source": m.source,
                "as_of": m.as_of,
                "status": "available" if m.available else "unavailable",
            })
        return rows

    def emit(self):
        return {
            "metrics": {"%s@%s:%s" % k: {"value": m.value, "unit": m.unit,
                                         "label": self.label(*k),
                                         "available": m.available, "reason": m.reason}
                        for k, m in self._m.items()},
            "provenance": self.provenance_table(),
            "unavailable": self.unavailable_report(),
        }



def _same(a, b, tol=1e-9):
    if a is None or b is None:
        return a is b
    try:
        return abs(float(a) - float(b)) <= tol * max(1.0, abs(float(a)))
    except (TypeError, ValueError):
        return a == b



def _xr(v, d):
    return str(Decimal(repr(float(v))).quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP))


def _sep(t):
    i, _, frac = t.partition(".")
    neg = i.startswith("-")
    i = "{:,}".format(int(i.lstrip("-")))
    return ("-" if neg else "") + i + ("." + frac if frac else "")


def _render(v, unit, decimals=None):
    if unit == "x":
        return _xr(v, 2 if decimals is None else decimals) + "x"
    if unit == "%":
        return _xr(v, 1 if decimals is None else decimals) + "%"
    if unit == "pp":
        _t = _xr(v, 1 if decimals is None else decimals)
        return (_t if _t.startswith("-") else "+" + _t) + "pp"
    if unit == "shares":
        return _sep(_xr(v, 1)) if abs(v) < 1e3 else _sep(_xr(v, 0))
    if unit == "$":
        a = abs(v)
        if a >= 1e9:
            return "$" + _xr(v / 1e9, 2) + "B"
        if a >= 1e6:
            return "$" + _xr(v / 1e6, 1) + "M"
        return "$" + _sep(_xr(v, 0))
    return _xr(v, 2 if decimals is None else decimals)



CANONICAL = [
    ("NET_DEBT",                       "Net debt"),
    ("TOTAL_DEBT",                     "Total debt"),
    ("CASH",                           "Cash and equivalents"),
    ("EBITDA",                         "EBITDA"),
    ("NET_DEBT_TO_EBITDA",             "Net debt / EBITDA"),
    ("DEBT_TO_TOTAL_CAPITAL",          "Debt / total capital"),
    ("INTEREST_COVERAGE",              "Interest coverage"),
    ("OCF",                            "Operating cash flow"),
    ("CAPEX",                          "Capital expenditure"),
    ("FCF",                            "Free cash flow"),
    ("CAPITAL_RETURNED",               "Capital returned"),
    ("CAPITAL_RETURNED_PCT_FCF",       "Capital returned as % of free cash flow"),
    ("CAPITAL_RETURNED_PCT_OCF",       "Capital returned as % of operating cash flow"),
    ("DIVIDEND_PAYOUT_RATIO",          "Dividend payout ratio"),
    ("BUYBACK_PAYOUT_RATIO",           "Buyback payout ratio"),
    ("TOTAL_SHAREHOLDER_PAYOUT_RATIO", "Total shareholder payout ratio"),
    ("BUYBACK_YIELD",                  "Buyback yield"),
    ("DIVIDEND_YIELD",                 "Dividend yield"),
    ("ROIC",                           "Return on invested capital"),
    ("ROE",                            "Return on equity"),
    ("TSR",                            "Total shareholder return"),
    ("SHARE_COUNT_CHANGE",             "Diluted share count change"),
    ("REINVESTMENT_RATE",              "Reinvestment rate"),
    ("CAPEX_INTENSITY",                "Capital intensity"),
]

FORMULAS = {
    "NET_DEBT":                       ("total debt - cash - short-term investments", "BS_P_114", ""),
    "NET_DEBT_TO_EBITDA":             ("net debt / EBITDA", "NET_DEBT", "EBITDA"),
    "DEBT_TO_TOTAL_CAPITAL":          ("total debt / (total debt + equity)", "BS_P_114", "BS_P_114 + BS_P_113"),
    "INTEREST_COVERAGE":              ("EBIT / interest expense", "IS_P_109", "IS_P_141"),
    "FCF":                            ("operating cash flow - capital expenditure", "CF_P_108", "CF_P_109"),
    "CAPITAL_RETURNED":               ("dividends paid + common share repurchases", "CF_P_104 + CF_P_101", ""),
    "CAPITAL_RETURNED_PCT_FCF":       ("capital returned / free cash flow", "CAPITAL_RETURNED", "FCF"),
    "CAPITAL_RETURNED_PCT_OCF":       ("capital returned / operating cash flow", "CAPITAL_RETURNED", "OCF"),
    "DIVIDEND_PAYOUT_RATIO":          ("dividends paid / net income", "CF_P_104", "IS_P_124"),
    "BUYBACK_PAYOUT_RATIO":           ("share repurchases / net income", "CF_P_101", "IS_P_124"),
    "TOTAL_SHAREHOLDER_PAYOUT_RATIO": ("(dividends + repurchases) / net income", "CF_P_104 + CF_P_101", "IS_P_124"),
    "BUYBACK_YIELD":                  ("share repurchases / market capitalisation", "CF_P_101", "Market_Cap"),
    "ROIC":                           ("EBIT x (1 - effective tax rate) / (total debt + equity - cash)", "IS_P_109", "BS_P_114 + BS_P_113 - BS_P_101"),
    "ROE":                            ("net income / average shareholders equity", "IS_P_124", "BS_P_113 (average)"),
    "REINVESTMENT_RATE":              ("capital expenditure / operating cash flow", "CF_P_109", "CF_P_108"),
    "CAPEX_INTENSITY":                ("capital expenditure / revenue", "CF_P_109", "IS_P_101"),
    "DIVIDEND_YIELD":                 ("(dividends paid / diluted shares) / share price",
                                       "CF_P_104 / IS_P_125", "market price per share"),
    "SHARE_COUNT_CHANGE":             ("(last reported diluted share count / first reported "
                                       "diluted share count) - 1, over the observed endpoints",
                                       "IS_P_125", "IS_P_125 (first observed year)"),
    "TSR":                            ("((price_end + div_per_share_cumulative) / price_start) "
                                       "^ (1 / years) - 1",
                                       "market data: price_end + div_per_share_cumulative",
                                       "market data: price_start"),
}


def new_store():
    s = MetricStore()
    for mid, name in CANONICAL:
        s.define(mid, name)
    return s
