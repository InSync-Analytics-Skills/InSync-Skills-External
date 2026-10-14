# Valuation card scope

Referenced from SKILL.md.

The card's multiple slots are the SECTOR LENS SET: `meta.metricset.multiples`, resolved by
`resolve_metricset.py` from `assets/valuation_rules.json` against the ticker's InSync
subindustry. Up to three lenses, in the rule's own ranked order, then **Dividend Yield** and
(when positive and not already listed) **FCF Yield (NTM)** appended, so no card loses a line
it used to carry. Each lens resolves out of the `get_valuation` NTM index, keeps the
adjusted-first / GAAP-sibling fallback per metric, and records `basis`.

> **The lens OWNS the basis, and every reported multiple is marked.** (1) The lens set is the
> single owner of adjusted-vs-reported for its concept: `ntm_pe`, `ntm_ev_ebitda`, `pe_summary`
> and `pe_monthly` all follow it. Some rules request a reported code, so four independent
> selectors could disagree and print a GAAP lens P/E beside an Adjusted Earnings Setup P/E.
> (2) `(rep.)` marks every multiple on the reported basis, whether the rule REQUESTS the
> reported series or an ADJ metric FELL BACK to GAAP. The reader sees the basis, not the
> operator's intent, so the basis is what carries the mark. `check_invariants.py` INV-1 and
> INV-5 hold both.
>
> A rule may also DECLARE the concept its label names (`concept: "P/FFO"`) when the vendor
> code does not carry it - a REIT's FFO arrives in the FCF series - so INV-2 can check the
> label against declared intent instead of trusting convention. A lens InSync does
not carry for the ticker is **dropped, not dashed** — the same rule as an all-dash guide
column — and the omission is warned. `Net Debt / EBITDA` joins the left column when net debt
is positive and a positive FY0 EBITDA exists (omitted for a net-cash issuer and for any
financial, which has no EBITDA).

**Why the lens set drives the card.** Fixed slots throw the resolver's sector lens away:
**a REIT** would print a GAAP P/E (a REIT's GAAP EPS is suppressed by real-estate
depreciation, so the number is not a valuation statistic) while the P/FFO lens sits unread in
the same payload; **an insurer** would omit P/B, the primary P&C lens, and spend slots on an
EV/EBITDA an insurer cannot have and an FCF Yield that is not an insurer lens.

**LENS CROSS-CHECK.** Every lens whose ratio can be rebuilt from `$M` aggregates the document
already prints (EV/EBITDA, EV/Sales, EV/FCF, P/FCF, FCF Yield) is recomputed and compared. The
vendor figure is kept unless the two disagree by **an order of magnitude**, or the vendor
figure is itself not credible (a multiple beyond 500x, a yield beyond ±100%) — then the slot
renders `NM` and a `[valuation]` warning names both numbers. Measured across the whole fixture
corpus the two agree inside 0.60–1.14x on every real ticker, so the 10x threshold leaves a 9x
margin over the widest legitimate NTM-versus-FY0 basis difference. **P/E and P/B are
deliberately NOT cross-checked here** (`check_invariants.py` INV-3 does recompute P/B
against the card's own BVPS on a major-unit document, which is where the exemption below does
not apply and where a wrong equity base hides): their recompute divides a per-share PRICE by a per-share
denominator, and on a minor-unit issuer (pence, cents) the recompute — not the vendor — is
wrong, so checking them would produce false NMs. The check also abstains entirely on any
document carrying a minor-unit row, and whenever the recomputed figure is itself implausible.

---

**P/E basis.** Adjusted wins (`P/E_Adj_Ratio`); GAAP (`P/E_Ratio`) is the fallback, and the
`[valuation]` warning fires on the GAAP case (the degraded one). `P/E_Ratio` IS the reported
series, so the P/E must sit on the same basis as the EPS row and the 1-year comparisons beside
it. A NEGATIVE FCF Yield renders `NM` (an artefact of negative free cash flow, not a valuation
signal). When fetching, always request BOTH `P/E_Ratio` and `P/E_Adj_Ratio`.
