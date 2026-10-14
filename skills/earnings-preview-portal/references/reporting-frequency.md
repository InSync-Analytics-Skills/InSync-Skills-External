# Reporting frequency — quarterly, half-yearly, annual

Referenced from SKILL.md. Read this before Step 1 whenever `latest_fiscal_quarter` is not
a `nQyy` label. `compute_portal.py` raises a `[frequency]` warning for every case below, so
the code is the enforcement and this file is the explanation.


The pipeline was written for QUARTERLY filers. InSync also carries **half-yearly**
issuers (`1H26` / `2H26`) and **annual-only** issuers (a bare
year). Both now run, but the differences are yours to handle:

| | Quarterly | Half-yearly | Annual |
|---|---|---|---|
| Target rollover | `4Qxx -> 1Q(xx+1)` | `2Hxx -> 1H(xx+1)` | `20xx -> 20(xx+1)` |
| Sequential sub-row label | `Q/Q Growth` | `H/H Growth` | `Seq. Growth` |
| `meta.prints` realistic depth | 6 quarters | 3 years | 6 years |

- `periodRank` and the frequency detector understand all three forms; the sequential
  sub-row is relabelled automatically from `meta.quarters.target_q`.
- **`py_q` and `prior_q` must be DIFFERENT periods.** For a half-yearly filer targeting
  `1H26` they are `1H25` and `2H25`, NOT both `2H25`. If they collapse, the two columns
  render the same value twice and the sequential sub-row duplicates Y/Y; compute now
  raises `[frequency]` for this, and the warning must be resolved, not ignored.
- **Annual filers:** when `target_q` IS `fy0` the target-period guide and the FY guide are
  the SAME row. Compute suppresses the duplicate FY column and warns; do not re-add it.
- Beat history, revision buckets and pre-print snapshot windows all assume quarterly
  cadence. `get_call_date` frequently returns ONE row for a non-quarterly filer, so the
  beat chart will be thin. State that in the report rather than shipping it silently.
