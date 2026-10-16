# When a run stops

Open this file only when `flash.py` or the build prints something you cannot act on from its own
message. Every message names its fix; this table is the short version.

## Exit codes

| Exit / status | What it means | What to do |
|---|---|---|
| `STOP:` from flash.py | A step is missing or a fact is wrong (fuzzy ticker, no data yet). | Do what the line says. For a fuzzy ticker, ask the user for the right symbol. Never substitute. |
| 1 | A check failed. The reason is in `message` and `warnings[]`. | Fix what it names. For narrative errors, edit only the fields it names. |
| 4 | A money value parsed as under $100K with no unit. | Re-save that response: the `B`/`M`/`K` was lost in copying. If the metric really is per unit (price per unit, revenue per user), build with `--allow-unit-sanity` and say so. |
| 5 | No financial rows were built. | If it says `[values unparseable]`, a currency token is unknown to `run_math._CUR_RE` - add it. Do not re-probe the period. |
| 6 | Currency split: the MCP converted the payload. | A KPI named in a foreign currency shared the fetch. Remove it from the fetch; flash.py fetches such KPIs alone. |
| 7 | Copy check: a saved value breaks the data's own checks. | Re-save the named response from the tool output, exactly as returned. Run `flash.py assemble` again. |
| `needs_user_decision` | A later quarter has reported but is not loaded yet. | Ask the user in plain words: wait and re-run, or run the prior quarter now, or (minutes after the release) take the headline actuals off the release. Prior quarter, only on their word: build with `--allow-stale-period` and say in `flashVerdict.net` that it is the prior quarter. Release actuals, only on their word: `flash.py start TICKER --period <new quarter> --dir <new folder>`, then after the fetch `flash.py eightk FILE` (JSON of spine ltcode to value in base units, e.g. `{"IS_P_101": 50000000000, "IS_P_128": 1.25}`), and say in `flashVerdict.net` that the actuals were taken from the release. |
| `render_gate_failed` | A printed row does not add up, or mixes units. | Fix the data. Never use `--allow-render-defects` to ship a red build. |
| `ticker_identity_failed` | The company is not confirmed. | Get the right symbol or ticker_id. There is no override. |

After **two** failed builds on the narrative, run `python3 flash.py template --failed`. It
rewrites only the failed fields by rule, and the build will pass.

## Warnings that must be cleared before the file goes out

`[currency split]` · `[valuation currency]` · `[no valuation]` · `POLARITY UNDECLARED` ·
`LARGE SURPRISE, VERIFY BASIS` · `MODEL-SOURCED ACTUAL` · `NO OPERATING KPIs` · `NO TAGGED KPIs` ·
`THIN KPI TABLE` · `[prior period unmatched]` · `[period unmatched]` · `[period shape]` ·
`[calendar shape]` · `[currency token unknown]` · `[truncated fetch]` · `fact_coverage_failed` ·
`RENDER GATE (--allow-render-defects)` · `SPINE CODE NOT FETCHED` · `[provenance absent]` ·
`[period probe missing]` · `[period not in probe]` · `[call window not forward]` ·
`[call window missing]` · `[kpi query not 'all']` · `[kpi query unrecorded]` ·
`[rung 4 skipped]` · `[registry label mismatch]` · `[values unparseable]` · `[no financial rows]` ·
`[no guidance rows]` · `[guidance predates print]` · `[consensus not pinned]` ·
`broker_disclosure_failed`.

"Cleared" means you read it and either fixed the cause or confirmed it is true of the company
(for example `[no guidance rows]` for a company that does not guide). Everything else in
`warnings[]` is information: read it, it does not block.

Common ones:

- `POLARITY UNDECLARED` - a KPI's direction (is higher better?) is unknown. Decide it and run
  `flash.py set direction <ltcode> higher|lower` (the fact sheet lists them). Never guess.
  `POLARITY UNRESOLVED` is the quieter form: it was graded higher-is-better by default. Check it.
  If the skill folder is read-only, build with `--no-strict-polarity` and name the KPI in your
  final message.
- `LARGE SURPRISE, VERIFY BASIS` - the actual is far from consensus. Mention that row in the
  narrative and check the basis (adjusted vs GAAP) is the same on both sides.
- `[period unmatched]`, `[period shape]`, `[calendar shape]` - the quarter label is wrong. Start
  again in a new folder with the label the warning names: `flash.py start TICKER --period <label> --dir <new folder>`.
- `[consensus not pinned]` - no call date was found, so no pre-guide consensus. Check the call
  date; if it is right, write the guide alone, with no consensus.
- `MODEL-SOURCED ACTUAL`, `OUTSIDE BROKER RANGE`, `[basis]`, `[restated period used]`, `AUTO-TRIM` -
  information, but check them: model-sourced rows are marked `†`; a restated prior changes Y/Y;
  after an auto-trim, make sure nothing important was cut.

## The guidance ladder

flash.py walks it for you, in this order, and stops at the first rung that gives the guide:

1. **Feed current** - the newest guide is dated on or after the call. Source `mcp`.
2. **Feed stale or empty** - read the guides off the earnings release (then, smallest first, a
   guidance or outlook file, shareholder letter, commentary, prepared remarks, supplemental). Source `release`.
3. **Release silent or unreadable** - read the transcript. Source `transcript`.
4. **Documents read, no guide in them** - the company does not guide. Source `none`; the page
   shows a Forward View with the Street's forward numbers.
5. **Nothing could be read** - source `unavailable`; the page prints one line: `Guidance could not be retrieved at press time.`
   This needs a recorded failed attempt on every rung (flash.py records them in
   provenance.document_attempts and provenance.guidance_attempts).

Never pick `none` when nothing was read: `none` is a fact about the company, `unavailable` is a
fact about the tools. At most 2 parse attempts per document and 4 in total. A parse that has not
come back is a failed parse: move on.

## Rate limit and timeouts

flash.py counts tool calls and prints `WAIT n seconds` before you pass 25 calls in a minute.
A typical run makes about 11 to 15 tool calls in total. On a rate-limit rejection wait 60
seconds and retry once; if it is rejected again, stop and tell the user. A timeout is not a rate
limit: do not resend the same call unchanged. If `search_tickerid` hangs, you passed `company=`:
send the ticker alone. If it hangs with the ticker alone, the symbol may be stale or delisted:
ask the user for the current symbol.

A parse gets two tries (the second with a different prompt that flash.py prints), then the
next document; four in all. Record a refusal, an empty reply or a cut-off text as a failure,
never as `success`.
