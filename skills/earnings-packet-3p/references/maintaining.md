# Maintaining this skill

Everything an EDITOR of the bundle needs and an operator of it does not.
Read before changing any bundled script, and never ship an edit without the matrix.

## Regression: run the fixture matrix before you trust an edit

**The command list lives in SKILL.md's "Editing this skill", and only there.** A second
copy here would drift from it. `self_check.py` asserts that SKILL.md invokes every gate, on
the principle that a gate nothing calls is a gate nobody runs. The fixture matrix and the
tests below ship in the internal edition only.

The matrix runner also re-derives `packet-schema.json` and fails on a stale one, so the
schema cannot drift away from the fixture set it is generated from. That check is skipped
under `--only` (a deliberately partial fixture set) and under `--record` (the run that
legitimately moves the baseline).

The fixture generator (internal edition only) writes twenty-four contract-conformant input sets, each
holding the line on one behaviour: the five legal peer-set sizes, the no-guidance path, the
single-broker path, a pruned target column, a 40-row table past `KEEP_MAX`, a non-dollar
issuer, thirteen guided items, every negative-cell shape, one cell of every `fmt`, and one
input that must be REJECTED. The matrix runner builds every one, runs every gate, and diffs
what each gate SAID against `EXPECTED.json`, so **a gate that goes quiet is a diff rather
than a green tick**.

Geometry is read out of `word/document.xml`, never off the page, because LibreOffice
silently reconciles a `<w:tblGrid>` that disagrees with its rows and Word does not: that is
how a malformed peer table can stay invisible to both geometry passes and to the PDF. And the
regression signal is the `word/document.xml` hash, never the archive's, because `docx-js`
stamps a build time into `docProps/core.xml`.

The round-trip test is the acceptance test for `build_computed.py`. It explodes every
fixture's hand-authored computed file into the plan-and-raw pair that would have produced
it, rebuilds it, and requires the rebuilt file to render a **byte-identical page**. The page
is the bar rather than the JSON, because key order, an absent field against an explicit
null, and a float at four decimals against one at two are exactly where a rebuild drifts,
and none of them matter if the page is the same.

The reaction-closes test is the acceptance test for the section 9 arithmetic.
It carries an eight-print close series and the five rates per print worked out from the
convention in `packet-sections.md` rather than from any code in this bundle, because the
fixtures generate their rates with the same helper the builder uses and a fixture agreeing
with the builder proves only that one function equals itself. It also checks that the
pre-computed fallback path agrees with the closes path, and that a half-supplied row refuses
by name instead of rendering a dash.

Two regression tests hold earlier fixes (bps on '%2' rows, verified scaffold codes, insurer
revenue, prose unit at M scale, section 10 sources, rounding, guide grading, `--verify`
positions, `mid`/`tol_*` guides, zero display).

The section 10 prose test is the acceptance test for pass 3 over section 10: the ten risk
and catalyst items are inside rule 1 like every other prose surface. The
fixture matrix cannot hold this line, because the base fixture's section 10 quotes `20%` and
`20` is also a computed value there, so the token resolves whether or not it is declared:
the same shape as the money scale, where a correctly authored fixture proved nothing about
the gate. So the four properties are stated here instead - an undeclared figure is refused
and named, a declared one is accepted, a declaration written `$149 million` normalises to
the `149` the scanner reads, and a day of month is not a figure.

The precision-gate test is a mutation test for the precision check, and it holds BOTH
edges: the two shapes the check must catch (a note one decimal finer than the cell; a raw
feed value where the page prints money) and the six it must not (trailing zeros, a token at
one fewer decimal, a figure printed exactly, a sign the page carries and the note does not,
a one-decimal token against a whole number, a declared document figure). The second list is
the longer one on purpose: trailing zeros, an under-recorded template-composed string and
over-rounding all produce false positives. A gate that invents findings gets switched off, and then it protects nothing.

**On sector coverage, stated plainly.** The sector fixtures are not all the same kind of
thing. `banking-ebit-tile` HOLDS A LINE: the contract refuses it, which proves the B12 gate
works. `semi-annual` holds a line differently: relabelling a quarterly grid leaves every
stored rate measuring the relation it had before, and the audit refuses it. The other four -
`banking`, `insurance`, `reit`, `software-growth` - are COVERAGE: their value is that a
future edit cannot break the sector layer silently.

The geometry-gate test is a mutation test: it re-renders one table 40 dxa narrow and
requires pass 2 to FAIL it. Every fixture passing pass 2 proves the false positive is gone
while proving nothing about whether the check still works; this proves the tolerances
have not blinded it.

## Editing the bundled scripts
If a build needs a change to `render_packet.py`, make it with an exact-string edit.
**Do not run pattern substitutions across the file.** Two rules that read as safe over
prose are not safe over code: a rule that tidies `, ` before punctuation will collapse
every `,...` spread into `...`, and a line-range replacement anchored on a string that
does not match will duplicate or truncate the file. After any edit to
a bundled script:

```
python3 -m py_compile <skill-dir>/references/render_packet.py
python3 <skill-dir>/references/self_check.py
```

then rebuild in a clean directory and re-run the validator. A syntax error is the good
case; a silently wrong table is the bad one.

## The gates

The one copy of the release command list lives in SKILL.md's "Editing this skill", which is
the copy `self_check.py` reads. Run it there; a second list here would be the copy that
drifts.

**After any edit to a bundled script, run the matrix.** An edit that reports no differences is
an edit you can defend. One that moves a page hash you did not intend to move is the only
warning you will get.

**A new sector key needs a scaffold target.** `build_computed.py`'s `SCAFFOLD_MAP` maps every
key in `sector_config.json` to the fixture `--scaffold` draws its shape from, and the
scaffold acceptance test fails on a key with no target, so adding a sector without one
cannot ship.

**The two phases are a context boundary, not a refactor.** Steps 7 and 8 read only what is on
disk, so they are handed to a fresh context. Anything that makes a build step need an MCP row
or a line-item decision breaks that boundary and puts the feed payload back into the build
turns; keep new work on the correct side of it.
