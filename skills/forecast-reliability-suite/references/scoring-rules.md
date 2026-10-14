# Scoring Rules — dispersion gate and predictability weights

Design rationale for two rules in SKILL.md. Nothing here changes behaviour; it is the reasoning behind
two rules a reader may want to challenge.

---

## 1. Estimate dispersion and the collapsed-range gate

**Broker counts are never DISPLAYED.** Dispersion is computed from the MIN/MAX range width only.

The **"thin panel" ⚠** marks a *collapsed* range: MIN = MAX in at least half the quarters that carry a
range at all.

**Why a collapsed band is not "Very low" disagreement.** A zero-width band cannot express disagreement.
With a single undifferentiated estimate on file there is nobody to disagree with, so scoring it "Very low"
asserts a consensus that was never measured — the arithmetic is right and the sentence it becomes is false,
which is the hardest class of error to notice. Those rows therefore show the consensus **LEVEL** and leave
Range and Disagreement unscored: `… (no range published)` / `n/a`.

**The one exception, and why it fails closed.** Two or more brokers posting the same number genuinely IS
very low disagreement and must stay scored. So collapse is the trigger and an optional
`est_range[q].brokers` is the confirmation:

| `est_range[q].brokers` | Outcome |
|---|---|
| 2 or more | genuine unanimity — the row stays scored |
| 1, or absent | left unscored |

The absent case fails **closed** deliberately. Falling through when no count is supplied would silently
restore the label the gate exists to remove. The count is read only on a row that has already collapsed, so
it is never load-bearing in the normal path, and it is never printed.

**The gate is display-only.** `_metrics.json` — including `dispersion`, `thin_panel`, `range_quarters` and
the Predictability Score — keeps its computed values, so no screener consumer needs migrating.

---

## 2. Predictability weights are client-configurable

The Consensus Predictability Score has four components, weighted by default:

| Component | Default weight |
|---|---|
| surprise | 0.35 |
| direction | 0.20 |
| dispersion | 0.25 |
| revision | 0.20 |

Override per run with an optional top-level `pred_weights` block in `input.json`. Any subset is accepted;
missing keys keep their defaults and the set is re-normalised. Unusable overrides (not a dict, all
non-positive, unknown keys) fall back to defaults and are flagged in `data_flags`. The weights actually used
and a `weights_custom` flag are echoed into `_metrics.json`.

**Scope worth knowing before tuning them.** The Predictability Score is computed to `_metrics.json` only,
for optional screening. It is **not rendered in the report**. These weights therefore affect that JSON
value, not the visible output.
