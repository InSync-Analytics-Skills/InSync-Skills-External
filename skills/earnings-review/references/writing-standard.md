# Writing standard

Reader: a PM with ten minutes. Each unit says what changed, then why it matters. Short declarative sentences, one idea each,
no em dashes. Reported figures stated plainly; management quoted verbatim in quotation marks; calculations named
("implied 4Q revenue of about … (deliveries × ASP midpoints)"); interpretation in judgment verbs ("reads as", "signals",
"assumes"). No tags or legends, no rating, target or trade language ("we like", "compelling"). Numbers are tokens; the
renderer formats them ($B two decimals, % one decimal, rate changes in bps, negatives in parentheses, colour = sign only).

## Sections
- **THE PRINT** (3-5 one-liners): 1 EPS vs pre-print consensus and prior guide · 2 revenue and the Primary KPI · 3 margin and
  cost line · 4 new guide vs the next-quarter consensus, FY change · 5 post-print revisions. Signs coloured with `|c`.
- **Analytical Summary** (3-5, same changes and order as What Changed): bold FACTUAL lead-in (metric, direction, reference:
  "SG&A above the guided range; resale competition cited", never "a clean beat"; no typed figures, a figure is a token), then two sentences: what changed (one or two
  numbers), why it matters. Do not restate THE PRINT surprise unless it is the change. No monitoring clauses.
- **Scorecard comment**: one clause, starting with `{id.verdict}` ("{eps.verdict}; below the lowest estimate; {eps.yoy|c} Y/Y").
  Explain Not comparable (no consensus series, a different line item, filed with the 10-Q). The † on a verdict speaks for itself:
  never comment on coverage depth ("thin coverage", "one analyst" are refused by the validator).
- **What Changed** (3-5 rows): facts (numbers, ranges, dates) · verbatim management quotes (stored in `data.json → quotes`; cite
  `{q.key}` or paste the exact stored text in quotation marks) · interpretation: nature (macro,
  structural, timing, unresolved) and significance; name the counter-reading when the facts support two and say which the
  data favours. No monitoring text.
- **Guidance read** (2-3 sentences): new guide vs pre-print consensus at midpoint and top; what it implies; what the FY guide
  implies for the remaining quarter. **Revisions tail**: one closing sentence (the renderer builds the rest).
- **Tone Shift** (3-6 dimensions that moved): verbatim quotes from each call; one-sentence note.
- **What to Monitor** (4-7): metric, threshold (guide range or prior level), date if known; each confirms or refutes an
  interpretation above.
- **Source**: documents and dates, InSync pre-print vintage (no contributor counts in prose: the scorecard shows them in brackets), not-graded lines and why, † definition,
  post-print vintage, price and valuation sources, gaps and exclusions, "arithmetic recalculated in code", prepared date,
  "no house view, no rating, no price target". Overrides, KPI provenance and the disclosure are appended automatically.
- **Valuation vs own history**: position only (`{val.hist.pe.ltm}` vs `{val.hist.pe.avg3y}`, `{val.hist.pe.vs3y|c}`); never
  cheap, rich or re-rating language.

## Verdicts (computed)
IN LINE within ±1.0% (levels) or ±10 bps (rates), override with `inline_band`; else BEAT / MISS in the company's favour via
`better` or the polarity registry (lower for costs, ratios, NCOs, capex as spend); an undeclared graded line is a validator error.
No consensus, or consensus on a different line item → Not comparable; ≤2 contributors → graded with †. Sequential change is Q/Q for
quarterly and H/H for half-year filers: write `{period.seq_label}`, never a hard-coded "Q/Q".

## Company types
- **No quarterly EPS guide** (banks, integrateds, semis): `guidance.eps_row: null` or point `guide_vs_frozen` at the guided
  line; FY rows carry the annual guide; say so in the guidance read.
- **EPS with disclosed one-offs**: grade ex-items EPS; GAAP via `not_graded` with the reason; state reconciling items.
- **Headline other than EPS** (midstream EBITDA, REIT FFO): lead THE PRINT with it; `guide_vs_frozen` on it.
- **Worded guides**: `{guide.implied_growth}` and `{guide.pos}`; never turn words into numbers yourself.
- **Restated or pro forma**: grade on the release's restated comparatives; cite `{id.alt_yoy}` with `{id.alt_label}`.

## Length
4-5 pages. Over: cut evolution rows, then tone rows, then scorecard rows, or shorten long cells; never cut a summary change.
