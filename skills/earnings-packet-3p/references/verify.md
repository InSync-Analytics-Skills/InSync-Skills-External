# Verifying a built packet, in full

SKILL.md Step 8 carries the command and the escapes. This file carries what each gate IS,
what it can and cannot see, and the failures that motivated it. **Read it when a gate fires,
when you are deciding whether a verdict is real, or when you are editing a validator.** A
clean build never needs it, which is why it is not in SKILL.md: it was 2,028 tokens of prose
re-sent on every turn of every run to describe checks that pass silently.

```
python3 <skill-dir>/references/verify_all.py "<the built>.pdf" "<the built>.docx"
```

One call, five gates, and it runs **every** one even after a failure, then prints each by name
with its own verdict and exits on the worst. A gate that did not run is reported as
`did not run`, never omitted. It is a runner: same commands, same order, same arguments, no
check added and none relaxed. Escapes pass through (`--lenient-prose`, `--strict-precision`,
`--strict`). What it runs, and what each one is:

```
compute.py --figures            publish the figure set (pass 3 refuses without it)
validate_packet.py <pdf>        passes 1 to 6
validate_rules_style.py <pdf>   passes 2, 5 and 6 re-derived from horizontal rules
verify_delivered.py <docx>      pass 7, the delivered document
self_check.py "<the built>.pdf" armed with the PDF: masthead geometry + determinism
```

**`self_check.py` must be handed the rendered PDF**, which is what arms the masthead geometry
check and the determinism test; called bare, both report `skip`, silently, and did so on every
run this skill made for several releases. `self_check.py` asserts that this file invokes it
with one.

**Pass 3 reads two records the pipeline writes, and neither may be stale.** `--figures`
publishes the figure set into `packet_computed.json` (what the computed file CONTAINS);
`render_packet.py` writes `printed_figures.json` from inside the formatter (what the page
SHOWS). Validate after rendering, always. If the figure set is missing pass 3 refuses to run;
if the printed set is missing it reports that the precision check did not run, and fails
outright under `--strict-precision`. Neither ever no-ops behind a green tick.

A green pass 3 is worth something and is still not a proof. **It does NOT enforce table
precision, and this is its one remaining hole:** the figure set holds the DISPLAY FORMS of
computed values, so a note quoting `32.58x` beside a cell reading `32.6x` passes, and so does
a section 10 item writing `3,277` beside a cell printing `$3.28B`. Only the manual read below
catches that, and it has caught it twice. Pass 3 reads section 10 as of 7.7.1; those figures
are document-sourced by definition, so declare them in `document_figures` and write naturally,
`$149 million` normalises to the `149` the scanner reads.

Seven passes. Six read the **rendered PDF** rather than trusting the build exit code; the
seventh reads the delivered `.docx`. **Know which of them reads a NUMBER, because only one
does.** Passes 1, 2, 5 and 6 read colour and geometry, never a value. Pass 3 grades the
pipeline's own JSON, so it grades the producer against the producer. Pass 7 is the only check
that opens the document a reader receives.

1. **Colour**, every coloured numeric cell obeys green=positive / red=negative, and no
   negative number renders black. **Pass 1 carries the palette as literals**, so changing `GN`
   or `RD` in the renderer makes it report `0 green / 0 red` while every negative reads black.
   `self_check.py` compares the two files and fails the build on a mismatch.
2. **Geometry**, every table shares one left and one right edge, on every page.
3. **Prose**, every number written in the narrative exists in the computed file, as a strict
   MEMBERSHIP test against the figure set. `--lenient-prose` restores the old behaviour for
   one run and labels the output.
4. **Page count**, 3, and 4 is a warning to compress, which the validator prints rather than
   returning a bare PASS.
5. **Dash columns**, every span binned into a real column using the table's own vertical
   borders; a column more than half em-dash fails. If this fires, the fix is a `minFill`
   threshold or a dropped code, never a hand-edited cell.
6. **Pagination**, no table fragment shorter than `MIN_BLOCK` rows at the top or the bottom of
   a page. If this fires, the table exceeded `KEEP_MAX`; compress it by the ladder in
   `references/house-format.md` rather than raising the constant.
7. **Delivered document** (`verify_delivered.py`), the only pass that opens the `.docx` and
   re-derives what the page prints, from the page: Dispersion Low <= Mean <= High; the guidance
   Position following from the printed band and printed estimate; `vs 1-yr` equalling printed
   LTM over printed 1-year average; no row mixing `$B` with `$M` or a bare number; percentages
   at one decimal; the masthead ticker agreeing with the filename **and** with any
   `packet_computed.json` in the directory; ten numbered sections in order. It imports nothing
   from the renderer and reads no file the pipeline wrote, so it cannot inherit a mistake from
   the code that produced the document. **Report-only until `--strict` is passed**, because
   check V4 currently fires on documents correctly obeying the verbatim-guidance rule, and a
   gate that fails a correct build teaches an operator to edit the bundled scripts.

**Passes 2, 5 and 6 find a table by its VERTICAL borders, which this styling does not draw**,
which is why `validate_rules_style.py` re-derives the same three guarantees from the
horizontal rules and runs beside the bundled validator. A run where the bundled validator
reports one table has not checked geometry at all.

**What none of the six PDF passes can do, demonstrated rather than argued:** a delivered
packet whose printed guidance figure was changed from `$50.00B` to `$99,999.9B`, with eight
cells blanked, passed `check_table_widths`, `validate_packet`, `validate_rules_style` and
`check_masthead`, all four, without a complaint. Pass 7 catches it on the magnitude tripwire.
And because pass 3 reads the working directory rather than the file you named, running the
validator on one issuer's PDF from another issuer's build directory reports
`VALIDATION PASSED` while grading the wrong company's prose; pass 7's identity check is what
catches that.

Then read the PDF yourself, page by page, as an image: SKILL.md Step 8 lists what the passes
cannot see.
