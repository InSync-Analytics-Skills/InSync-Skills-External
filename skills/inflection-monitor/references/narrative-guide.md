# Writing narrative.json

Read this once, before writing `narrative.json`. `render.py` and `format_lint.py` enforce
every rule below and name the section that broke one; when `publish` stops on a gate, fix
that section and run `publish` again.

## The contract

- **Sections:** `setup`, `dd1` to `dd3` (one per deep dive the engine chose; fewer when
  the scan supports fewer), `divergence`, `debate`, `guidance`. Any other key is refused by
  name, including `watch` (retired: the What To Monitor Next table carries those
  questions) and `dd4`/`dd5`.
- **Caps:** `setup`, each `dd` and `divergence` at most **45 words** (two sentences).
  `debate` is ONE question of at most **30 words**. `guidance` is a **JSON list** of at most
  **four bullets** of at most **22 words** each.
- **No digits and no figures in words.** Every figure arrives as a `{{placeholder}}` from
  `_available_placeholders` in `narrative_template.json`; an unknown token or a literal
  digit is refused. A figure written out is refused too: "sixty-three percent", "about four
  and a half billion dollars", "doubled", "a third above", "high teens", "mid-single
  digits". Words that state no quantity stay legal: "one-time", "a third consecutive
  quarter", "three of the five lines".
- **Every block answers "by how much?"** Each section and each guidance bullet carries at
  least one placeholder figure. The no-digits rule never meant prose without figures.
- **Every observation carries its evidence.** A sentence saying a margin improved carries the
  margin, the prior-period margin and the change; a sentence saying a line accelerated carries
  the rate, the prior rate and the change in pace.
- **House voice.** Would a buy-side analyst or PM understand it on first read? Plain words,
  numbers first, standard shorthand (y/y, q/q, bps, pp, FY guide, SD). No method language on
  page one, no engine vocabulary, no hedging that adds nothing ("one quarter does not
  establish...").

## Binding each paragraph to its row

- **Each dive states its own row's direction** (accelerating or decelerating, matching the
  engine's finding for that row) in pace words.
- **Each dive touches its own line and names no other.** Use at least one of that dive's
  `_binding_words` (printed in the template) anywhere in the paragraph; do not open on a
  different published row's name.
- **A figure sits next to its own name.** Slots are positional (`{{acc2.*}}` is whatever
  ranked second this run): before writing a figure, check that slot's `.name` in
  `_available_placeholders`; the nearest line name before each placeholder must be that
  slot's. `{{slotN.name}}` always binds. Full-year guides: `{{fyN.first}}`,
  `{{fyN.latest}}`, `{{fyN.direction}}` for revised guides and `{{fhN.guide}}` for the rest.
- **One written paragraph per concept.** Never spend two dives on one number (a ratio and
  its variant, a margin and its own EPS effect). Ship fewer dives rather than padding.

## Level versus pace

The engine measures a change in a growth rate. Keep the LEVEL and the PACE apart.
- Level-fall words (fell, declined, dropped, went backwards) never describe a decelerating
  line that is still growing. Never present a slowing growth rate as an absolute decline.
- Pace words (slowed, eased, cooled, came off, lost pace, picked up, quickened, rebounded)
  attach to a pace noun: "the growth rate slowed", "the rate of growth rose"; "it rose" does
  not parse.
- Where level and pace point opposite ways, state both; where they agree, saying both is
  padding.
- `{{ddN.prev_growth}}` is LAST QUARTER'S y/y rate and `{{ddN.prev_q}}` names that quarter:
  "+40.0% y/y against +50.0% in {{dd1.prev_q}}". `{{ddN.prior_level}}` is the year-ago
  LEVEL; "a year earlier" belongs only beside it, never beside a pace comparison.
- **A rate KPI is a reading, not growth** (rent change, same-store NOI, organic growth,
  price/volume contributions): "net effective rent change rose to {{dd2.growth}} from
  {{dd2.prev_growth}} in {{dd2.prev_q}}", never "rent change grew {{dd2.growth}}".
- Write unusualness as "a {{dd1.z}} SD move", never with the sigma sign.

## Describe; never interpret

- State what a line did, by how much, and what it is arithmetically connected to. Do not
  nominate which finding matters, declare what the next print tests, or speak for
  investors ("carried the quarter", "the finding is", "the real question is" are refused).
- **No borrowed frame:** no beat, miss, surprise, consensus, the Street, in-line, estimate,
  whisper. **No broker or analyst counts and no comment on coverage depth** ("twenty
  brokers", "thin coverage").
- **No driver the engine did not assign.** Never call a move price-, volume-, mix-driven or
  organic, or locate it in one part of the income statement while ruling out another,
  unless the engine recorded that driver. "Unresolved" is an honest answer.
- **Never claim an engine state the engine did not record:** a base or comparison effect
  needs `comp_flag` on that row; "no divergence fired" needs `divergences` to be empty; "the
  implication reads X" must match that row's `implication`; a no-guidance statement needs
  `guidance_check.scored` to be empty.
- **Add to an absence; never repeat it.** When the page already prints "No management
  guidance targeting <Q> was returned" or that no divergence fired, say what stands in for
  the missing test.
- **Write about the company, not the note:** no "the note does not", "the scan did not
  read", "the engine records"; say "the disclosure will not say which line", "rate and
  volume are not split out".
- Never write that growth is being bought, or claim every disclosed metric was scanned.
- Where a ratio is shown, show the amount behind it.
- Vary the construction across the dives; three paragraphs built from one sentence read as
  generated (a warning, not a failure).

## What the engine flags and the prose should use

- `comp_flag`: `easy_base` (the comparison quarter's growth was in the bottom quartile of
  the line's history, so part of the acceleration is the comparison) or `hard_base` (the
  mirror). Address it when set. A weak year-ago comparison has `{{ddN.ya_growth}}`,
  `{{ddN.two_year}}` and `{{ddN.two_year_q}}`.
- `improving_from_decline` (positive acceleration on a line still shrinking: the decline
  is narrowing) and `slowing_but_growing` (the mirror): say which.
- `streak` of three or more: say so; one quarter is noise as often as not.
- `{{ddN.effect}}` is required in a dive whose commentary carries an `effect`.
- The next-quarter bar `{{ddN.threshold}}` is InSync arithmetic (the level that holds the
  current y/y rate), never guidance or a forecast. It is printed in What to Monitor Next;
  never use it in a `guidance` bullet. A guide may be compared with it as arithmetic.

## The debate and the guidance bullets

- `debate`: one question, ending in "?", grounded in at least one placeholder, stating no
  view ("should", "we think", "likely" are refused). It asks what the figures leave open:
  "How much of the medical care ratio's fall to {{dd1.level}} from {{dd1.prior_level}}
  holds once the prior-period reserve development in management's statement is set aside?"
- `guidance`: bullets about the company's own guides (`fyN`, `fhN`, `fgN`, `dgN`
  placeholders), each with a figure. Where no guide exists, say what stands in for the
  missing test, not that it is missing.

## Examples

- **Good:** "Revenue was {{dd1.level}}. It grew {{dd1.growth}} y/y, slowing from
  {{dd1.prev_growth}} in {{dd1.prev_q}}." / "Gross margin was {{dd2.level}} against
  {{dd2.prior_level}} a year earlier, {{dd2.growth}} y/y after {{dd2.prev_growth}} in
  {{dd2.prev_q}}: the expansion picked up."
- **Bad:** "Fares did the work this quarter." (No figure, and it names a cause the
  disclosure does not support.)
