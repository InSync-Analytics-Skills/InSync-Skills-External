# Earnings Review (InSync skill)

Post-print, 4-5 page PDF for portfolio managers: print vs InSync pre-print consensus and prior guide, the three to five changes that matter,
KPI evolution, new guide vs the consensus it faces, management tone shift, what to monitor. Any InSync-covered company.

Quick start:
```
python scripts/run_pipeline.py <workdir>
```
builds `<workdir>/<TICKER>_<nQyy>_Earnings_Review.pdf` from a prepared workdir and runs the validator.

New name: read `SKILL.md`, then `references/data-collection.md` → fetch and save payloads → `scripts/build_dataset.py` →
fill the hand items → `review_config.json` → `prose.json` (tokens, see `references/pipeline-contracts.md`) → `run_pipeline.py`.

Requirements: Python 3.10+, PyMuPDF (`pip install -r requirements.txt`), Edge, Chrome or Chromium for the headless print (found on Windows, macOS and Linux; else set `BROWSER_PATH`). No Word, no weasyprint.
