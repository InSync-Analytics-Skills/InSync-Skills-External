# Writing standard — the full text of the earnings-wrap content rules

> This file is the authoritative long form of the content standard and the only full statement of
> content rules 4 to 9. SKILL.md carries the three rules a build has to hold in mind while writing,
> and `config_skeleton.json` states the rest at the field each governs.

## Original Rules 9-14 long form (superseded)

## Deterministic KPI architecture (Rules 9-14) — enforced by the renderer + `wrap_eval`
The single most important consistency rule: KPI SELECTION, ORDERING and FORMATTING cannot vary by
run or environment. Writing may vary slightly; the information architecture may not.

- **Rule 9 - Event Snapshot is standardized (3-column grid).** A `Metric | Actual | Change`
  grid with the same 7 rows, in this order, for every company: (1) Primary KPI `(Primary KPI)`,
  (2) a second Key KPI `(Key KPI)`, (3) Key Margin `(Key Margin)`, (4) Revenue, (5) Adj EPS or the
  sector bottom line, (6) Stock reaction, (7) Current price. Never reorder by company.
- **Rule 10 - KPI Hierarchy is deterministic.** Every KPI shows BOTH its actual value AND a Y/Y.
  Never a percentage alone; never an absolute alone. The renderer enforces this — `kpiHistoryTable`
  appends the Y/Y column automatically from the value series, so the model cannot choose the format.
- **Rule 11 - KPI history table shows value + Y/Y for every row.** The table is 5 consecutive
  quarters (printed quarter last) with the trailing Y/Y column. `wrap_eval` FAILS the build if the
  KPI table is not the `KPI ... Y/Y` shape, or if any row shows a printed value with no Y/Y (a Y/Y of
  `n/a` is allowed ONLY when the printed-quarter value is itself unreported).
- **Rule 12 - Fixed sector KPI templates.** KPI selection comes from the MCP ladder ranking (Primary /
  Secondary / Supporting), not from model discretion, so two runs pick the same KPIs. Canonical
  spines: Retail = Comparable Sales (P) / Traffic, Ticket (S) / Digital, Membership, Advertising,
  Stores (Sup); Payments = Gross Dollar Volume (P) / Cross-Border, Transactions (S) / Take Rate,
  Margin (Sup); Ride-sharing = Gross Bookings (P) / Trips, Active Users (S) / Take Rate, Segment EBITDA
  (Sup); Software = Revenue (P) / ARR, RPO (S) / NRR, Customers, Margin (Sup); Semis = Revenue (P) /
  Data-Center Revenue (S) / Margin, Backlog, Units (Sup); Industrials = Orders (P) / Backlog, Revenue
  (S) / Margin, Book-to-Bill (Sup). Utilities/REITs/Banks/Insurance use their capital-structure
  spine (Capex, FCF, RoE, segment earnings, EPS, DPS; AFFO; NII, NIM; combined ratio, PIF, NPE, BVPS).
- **Rule 13 - Determinism test (run before shipping).** Ask: if five analysts ran this in five
  environments, would the Event Snapshot, KPI selection, KPI ordering and KPI formatting be
  identical? If not, it is not done. Numbers come from InSync; structure comes from the renderer and
  the MCP ladder — neither is the model's to improvise.
- **Rule 14 - Writing style.** Internal PM-note voice, not sell-side prose: short sentences (<=25
  words), one observation each, fact first then interpretation, bullet-driven.
## Tables section (superseded, duplicated the FIXED STRUCTURE list)

## Tables — standardized by the renderer, no custom formats
The renderer draws exactly these, so a company's uniqueness shows up in WHICH rows appear, never in
table design: Event Snapshot (Metric|Value), Results versus Expectations (banded, Actual|Consensus|
Y/Y|Q/Q|Δ vs Cons|Dir.), KPI Performance (KPI-by-quarter), Estimate Revisions (Item|Before|After|
Change), and Valuation (Metric|Current|Basis). Numbers are computed in code, never placed by hand.

## The content standard in full

## Content quality standard (evidence, opinions, signal density) — enforced by `content_lint`
The framework is fixed; these rules govern the WRITING inside it. Every conclusion traces to reported
numbers, consensus, guidance, revisions, management commentary or KPIs. If the evidence does not
exist, state the fact and stop. Worked good/bad pairs for each rule are in
`references/writing-standard.md`.

1. **Always quantify.** No qualitative claim without a number. Observation -> Evidence ->
   Implication. `content_lint` FAILS any bullet carrying a movement word (beat, rose, expanded,
   slowed, record...) with no number in it.
2. **No unsupported opinions.** Never "premium/cheap/attractive valuation", "future optionality",
   "investors believe", "the market thinks", "priced for perfection". Print the datum instead.
3. **One question per section, one home per figure.** No unit-bearing figure may headline more than
   three prose bullets; tables are exempt. Section purposes: Executive Summary = what SURPRISED;
   Variant View = what the market may MISUNDERSTAND; What Drove = WHY it happened; KPI Hierarchy =
   each KPI's ROLE; Guidance = what CHANGED forward; Revisions = how the STREET reacted; Management
   Commentary = what management SAID; Monitoring = what to WATCH.
4. **Market Reaction is three bullets**: `Positive:`, `Negative:`, `Net:` (the stock move plus its
   one-line reason). State the reaction percentage exactly once, here.
5. **Variant View is four pairs**: Street View / What Changed / Potential Variant / Variant
   Assessment. It is analytical, not directional advice: where the same evidence supports both a
   bull and a bear reading, the Potential Variant names the upside and the downside case and the
   Assessment says which the data supports. Never a buy or sell view. The Assessment verdict is Supported, Partially Supported or Not Supported, each with
   a one-line evidence anchor. No separate "Key Evidence" pair.
6. **Guidance answers "so what?".** Close the block with an `Implication.` bullet stating what the
   change means. A guide change reported without its implication is incomplete.
7. **Revisions are interpreted.** `revisionsCap` says what the revision MEANS, not just the percent.
8. **Management Commentary = statements, not results.** Section 8 carries what management SAID or
   DID. A bullet citing a figure with no attribution cue (guided, said, reaffirmed, raised, targets,
   expects, plans, cited...) FAILS the build. With no management color, populate from the guide and
   strategy statements; never restate results and never drop the section.
9. **Monitoring is structured.** `monitor` is 3 to 5 objects `{metric, fact, monitor}`, each tying a
   reported number to a forward watch question, each distinct from the Key Debate.

