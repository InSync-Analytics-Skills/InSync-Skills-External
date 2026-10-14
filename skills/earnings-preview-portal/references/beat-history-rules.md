# Beat History & Reaction — credibility rules (STRICT RULE 4, full text)

`compute_portal.py` owns every decision below. Nothing here is a judgement call for the
operator; the only operator duty is to fetch snapshots per `data-collection.md` §7 with
`MIN`, `MAX` and `BROKERCOUNT` intact, and to read the `[beat]` warnings.

## Series selection
Revenue plus the FIRST BEAT-ELIGIBLE Top Operating KPI. Eligible = absolute AND directional:
skip `%`-format rows and `polarity: 'neutral'` rows. A loan book above consensus could be
growth or risk, so printing it as a beat asserts a view the data does not carry. If none is
eligible the chart is revenue-only; compute substitutes and warns.

## Which prints the AVERAGE and the WHISPER rest on
Five ordered tests. A print that fails one keeps its cell — greyed, asterisked, and named in
a generated caption — but leaves the average. `avg_basis` records used / excluded / why.

**(a) Broker minimum.** The minimum is ONE broker: a 1-2 broker vintage counts like any
other, and only a zero-broker mark is "no pre-print consensus". Tests (b) and (c) need 3 or more brokers to have a range at all.

**(b) Coherence.** A vintage whose range spans more than 40% of its own mean, or
more than 1.5x its own floor, is a stale or partial snapshot rather than the Street's view.
Neither the beat nor its sign can be read from it. This is the test a bank's print needed: its
snapshot read MIN 10,000 / MEAN 18,000 / MAX 22,000 on 3 brokers, breached MAX by 0.50%,
passed the flat 1% tolerance, and would read as a clean +22.8% beat that lifted the average to
+11.6% on n=2 and set a whisper above the entire live broker range (figures made up).

**(c) Range breach.** An actual outside the whole range by more than 1% of consensus is a
definitional change or a one-off, not a consensus miss. The tolerance is deliberately still
a flat 1%: a version scaled to the vintage's own half-width was tried and rejected, because
it gave a ±8.0% range a 4.00% allowance and re-admitted a bank's 2Q26 print, the definitional break the
filter exists for.

**(d) Two POPULATION guards on (c).** Test (c) asks whether one actual sits outside
one range. Both of these ask something no single print can answer, and without them (c) was
wrong on four of five sectors.
- *Coverage.* "Outside the whole Street's range" is only a claim about the Street if the
  snapshot IS the Street. Below 60% of the LIVE consensus broker count the range is a
  minority of analysts and narrower than the real one by construction, so (c) is NOT
  APPLIED — testing against it is not conservative, it is a different test. (a) and (b)
  still apply: those are data-quality questions, not population ones.
- *Prevalence.* (c)'s premise is "no analyst was there, so this is a definitional change".
  When more than a third of prints breach, the breaches are the issuer's pattern; they are
  reinstated with one warning naming the pattern. Excluding them reported +0.6% on a chipmaker
  against +2.40% across all prints, +0.4% on a footwear maker against +2.10%, +1.50% on a
  machinery maker against +4.00%, and a single print on a drugmaker against +7.90% — every one understating the setup
  enough to mislead a reader.

**(e) Outlier against the issuer's own record.** A beat more than 3x the issuer's
own median absolute beat, floored at 5pp, is a one-off whatever the coverage. Needs at least
four scored prints to have a distribution at all. This is coverage-independent, which is the
point: it catches a bank's 2Q26 print (+11.70%, 9.0x its own 1.30% median) even though that print's
snapshot was itself minority-coverage and (c) could not be applied to it. It is scale-free,
so it does not fire on a serial big beater whose prints cluster high (one drugmaker averages +7%).

## Disclosure fields
- `avg_basis.{revenue,kpi1}` — `used`, `excluded[{q,why}]`, `n`, `avg`, `avg_all_prints`.
- `avg_thin` — the published average rests on fewer than 4 prints. A disclosure flag; the
  average is still real.
- `avg_withheld` — compute refused to publish an average at all (fewer than 2 credible
  prints). These two are NOT the same field: keying the "Avg is WITHHELD" caption on `avg_thin`
  would print "Avg is WITHHELD ... one observation is not an average" above a real average.
- The caption prints the all-prints figure alongside, because the size of the adjustment is
  the number that says whether the exclusions matter.

## Revisions grid (same hazard, different table)
STRICT RULE 4 governs Beat History, and the same hazard exists in Earnings Revisions: a bucket
can be a 1-broker mark sitting between many-broker neighbours, so the grid prints a "revision"
that is one analyst arriving and computes Δ vs Post off it. A bucket IS the observation at its
date, so the value stays on the page. There is no thin-bucket warning: one broker is a
consensus, and the document never comments on coverage depth.
