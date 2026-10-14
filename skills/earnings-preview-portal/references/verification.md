# Step 6 verification checklist

Referenced from SKILL.md Step 6.


No browser needed. Verify against `computed.json` (the single source of every cell)
and the docx text (unzip `word/document.xml` and grep, e.g.
Python's `zipfile` or any text extraction):
- compute_portal.py exited 0 and its WARN list is clean. **Which classes gate, and how each
  may be closed:**
  | class | closed by |
  |---|---|
  | `[completeness]` `[currency]` `[guidance-freshness]` | RE-FETCH only. Never ignored. |
  | `[valuation]` | **re-fetch OR disclose — read the warning's own remedy** |
  | `[check]` `[coverage]` | re-fetch, OR explicit disclosure in the read-out where the data is genuinely thin |
  | `[reaction]` (unknown call timing) | disclosure: say which convention the table uses and that it could not be established |
  | `[guidance]` SAME CONCEPT, DIFFERENT LTCODE | confirm the two codes measure the same thing and bind in Step 0, or say they do not |
  | `[subrow]` `[absent]` `[polarity]` `[growth]` `[beat]` `[evolution]` `[revisions]` | disclosure |
  | `[ingest]` `[scope]` `[units]` `[currency]` | **STOP — these say the INPUT is wrong, not the document** |
  | `[cadence]` `[frequency]` | confirm `meta.quarters`/`growth_bases` are the cadence you intend, then disclose which cadence the document is built on |
  | `[metricset]` `[market]` `[setup]` `[irr]` | disclosure, or re-fetch where the warning names one |
  **Two classes are among the most serious the pipeline raises:** **`[ingest]`** is
  foreign-ticker contamination (another issuer's figure in a target-quarter cell), and
  **`[cadence]`** is the half-yearly/quarterly label collision that lifts a six-month figure
  into a quarterly column, a ~2x overstatement that renders perfectly. Neither is a disclosure:
  both mean the INPUT is wrong and the fix is upstream.

  **`[valuation]` is NOT re-fetch-only.** Several `[valuation]` warnings
  state in their own text that NO re-fetch will close them — the EV-bridge residual says so
  outright ("BOTH legs are already on the basis InSync EV uses ... it is non-controlling
  interests, preferred stock or an equity-method structure ... **Disclose** the basis and the
  gap rather than chasing it"), and so do the lens cross-check NM, the exactly-zero dividend
  yield and the incl-lease leverage basis. An operator obeying "RE-FETCH only. Never ignored."
  re-fetches forever. **The warning text is the authority on how to close it**: if it names a
  fetch, fetch; if it says disclose, disclose in the read-out and the footer. The classes that
  are genuinely re-fetch-only are the three left in the row above.

  `[check]` and `[coverage]` gate too: an FCF row can contradict its own OCF and capex
  several-fold with the warning sitting unread in the stream.
- **Scan the revisions and guidance-evolution data in computed.json for nulls in
  cells that should hold data (a revisions row needs every DISTINCT bucket column filled —
  `revisions.columns` says how many there are, and it is fewer than 4 when two buckets
  resolve to one observation — unless the metric is genuinely un-estimated; every past evolution column with an Actual needs
  a Consensus) — if you see a gap that compute did not already flag, treat it as a
  completeness failure and re-fetch.**
- docx text contains: the company name + target quarter, all 10 section headings **minus any
  card STRICT RULE 3 or 4 legitimately omitted** (an annual-only guider has no Guidance
  Evolution; a no-consensus line has no Beat History) — and that omission must be disclosed in
  the footer or the evolution note. Requiring 10 unconditionally would fail a correct
  document,
  no "Peer Comps" / "Market Expectations" headings, and no template remnants of
  other tickers (grep the docx text for stray ticker symbols).
- File size > 50KB and it unzips cleanly (valid OOXML).
