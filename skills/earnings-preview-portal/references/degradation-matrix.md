# Degradation matrix — coded in compute; never stop, never fabricate

Every row below is a rule inside `compute_portal.py` or `render_docx.py`, not a judgement
call. The document degrades and says so; it never refuses and never invents.

| Condition | Behaviour |
|---|---|
| No `top_kpi` universe | segment fallback, then financials-only. NEVER a refusal; Step 0 governs. |
| Company gives no guidance | guide columns and cells are conditional — an all-dash guide column is dropped entirely. A column opens only where a cell can actually be filled. |
| Missing adjusted metric | GAAP with an asterisk. |
| Mis-scaled consensus vintage | null + WARN (sanity band 0.4–2.5× actual). |
| Guided item with no Summary Financials row | commentary only, no evolution block — and the omission is disclosed in the note the renderer keeps even when the card is dropped. |
| No consensus published for a line | Consensus / vs-Cons rows auto-hide. |
| Fewer than 6 prints of history | shorter chart, relabelled. |
| Transcript parse refusal ×2 | proceed structured-only (stop-loss). |
| Broker-dispersion junk (e.g. GMV) | levels only, no growth commentary. |
| Thin, incoherent or unrepresentative pre-print vintage | the print keeps its cell, greyed and asterisked; the average and whisper exclude it, with the reason in `avg_basis` and the caption. Full rule: `beat-history-rules.md`. |
| Balance-sheet stock in the KPI block | no computed "% of Total Revenue" (a stock over a quarterly flow is meaningless); Y/Y and sequential are retained. |
| Reporting frequency not quarterly | half-yearly and annual filers run; `[frequency]` warnings name each relabelling. See `reporting-frequency.md`. |

The Word beat chart is native DrawingML — no browser, no image capture, anywhere.
