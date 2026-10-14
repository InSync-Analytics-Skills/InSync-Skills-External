#!/usr/bin/env python3
"""merge_payload.py — build tearsheet_data.json from computed.json + prose.json.

WHY THIS EXISTS
    A merge described in prose is easy to get wrong: a hand-picked key list drops keys that
    are added later, and a flat `d.update(prose)` cannot place `trend`, which is
    model-authored but lives INSIDE each operational_kpis row. A merge that copies every
    prose key would also let a model's own figures replace the computed ones, silently. A
    described step cannot be tested; an executed one can, so the merge is this script.

WHAT IT DOES
    * refuses outright when the compute run that produced computed.json recorded a QC
      FAILURE — it re-checks nothing, it just honours the verdict compute wrote
      into the payload, so a lost exit code from Step 1 cannot launder a known-wrong figure
      into a document;
    * starts from ALL of computed.json, so a new computed key needs no edit anywhere;
    * applies the prose fields on top, but ONLY the whitelisted ones — an unexpected key is a
      refusal, not a copy, because the model must never overwrite a computed number;
    * applies `trend` PER ROW to operational_kpis, which is the one model-authored field that
      is not top-level, matching rows by KPI name;
    * refuses to write the payload when anything IT is responsible for is wrong — the
      prose fields, the trend labels, a stray key, an empty KPI list — naming the cause.

    It is not a second copy of eval_tearsheet.py and does not try to be: eval owns the full
    schema and the one-page limits, and duplicating those rules here would give the package
    two places to maintain the same truth. A merge that succeeds is not a promise that eval
    will pass.

    Keys beginning with "_" in prose.json are notes: ignored, and not copied into the payload.

    It does not invent anything. A missing trend label is an error, never a default: the label
    is a judgement about deceleration that only the model can make from the data it fetched.

USAGE
    python3 merge_payload.py COMPUTED.json PROSE.json OUT.json

    prose.json carries exactly the model-authored fields (SKILL.md 11):

      {
        "ticker": "EXM", "company_name": "Example Co",
        "prepared_date": "September 3, 2026", "product_name": "Tear Sheet", "period": "2Q26",
        "business_overview": "...", "supply_chain": "...",
        "bull_case": ["...","...","...","..."], "bear_case": ["...","...","...","..."],
        "key_debates": [{"question": "...?", "rationale": "... falsifier ..."}, ...],
        "catalysts": [{"date": "Oct 14", "event": "..."}, ...],
        "recent_news": ["...","...","...","..."],
        "trend": { "NIM (FTE)": "Decel", "Efficiency Ratio": "Stable", ... }
      }

    `trend` is a map from KPI NAME (exactly as computed.json prints it) to one of
    Stable / Accel / Decel. Every KPI row needs one.

    That list is the WHOLE of prose.json. `product_name` is optional (the renderer defaults it
    to "Tear Sheet"); every other field above is required, `period` included. Any other key is
    refused — nothing numeric belongs in this file.

EXIT
    0  payload written
    1  the merge cannot be completed — nothing written, reason printed
    2  bad arguments or unreadable input
"""
import json
import os
import sys

ALLOWED_TRENDS = ("Stable", "Accel", "Decel")

PROSE_ALLOWED = ("ticker", "company_name", "prepared_date", "product_name", "period",
                 "business_overview", "supply_chain", "bull_case", "bear_case",
                 "key_debates", "catalysts", "recent_news", "trend")
PROSE_REQUIRED = tuple(k for k in PROSE_ALLOWED if k not in ("product_name", "trend"))


def main(argv):
    if len(argv) != 4:
        print(__doc__.split("USAGE")[1].split("EXIT")[0].strip(), file=sys.stderr)
        return 2
    computed_path, prose_path, out_path = argv[1], argv[2], argv[3]
    try:
        computed = json.load(open(computed_path, encoding="utf-8"))
        prose = json.load(open(prose_path, encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"ERROR: could not read inputs: {exc}", file=sys.stderr)
        return 2
    if not isinstance(computed, dict) or not isinstance(prose, dict):
        print("ERROR: both inputs must be JSON objects", file=sys.stderr)
        return 2
    for _k, _want in (("operational_kpis", list), ("key_financials", list)):
        if _k in computed and not isinstance(computed[_k], _want):
            print("ERROR: computed.json '%s' must be a JSON array, got %s. This did not come "
                  "from compute_tearsheet.py — re-run Step 1 rather than editing "
                  "computed.json by hand."
                  % (_k, type(computed[_k]).__name__), file=sys.stderr)
            return 2
    if "trend" in prose and prose["trend"] is not None and not isinstance(prose["trend"], dict):
        print("ERROR: prose.json 'trend' must be an object mapping KPI name -> label, got "
              "%s" % type(prose["trend"]).__name__, file=sys.stderr)
        return 2

    problems = []

    _qc = computed.get("qc")
    _qc_ok = (isinstance(_qc, dict)
              and isinstance(_qc.get("failed"), int) and not isinstance(_qc.get("failed"), bool)
              and isinstance(_qc.get("passed"), int) and not isinstance(_qc.get("passed"), bool)
              and _qc["failed"] >= 0 and _qc["passed"] >= 0)
    if not _qc_ok:
        problems.append(
            "computed.json carries no usable 'qc' verdict (found %r), so there is no way to "
            "tell whether the run that produced it passed its own consistency checks. A "
            "verdict must carry integer 'passed' and 'failed' counts. This file was produced "
            "by a compute_tearsheet.py older than 7.2.18, was not produced by "
            "compute_tearsheet.py at all, or has been edited. Re-run Step 1 with this release "
            "rather than merging a payload whose QC state is unknown."
            % (_qc if not isinstance(_qc, dict) else
               {k: _qc.get(k) for k in ("passed", "failed")},))
    elif _qc["failed"] > 0:
        _named = [str(x) for x in (_qc.get("failed_checks") or [])]
        problems.append(
            "the compute run that produced this computed.json recorded %s QC FAILURE(S): %s. "
            "A failed QC check means a figure on the sheet is wrong or untrustworthy, so this "
            "payload must not be built. Read the QC FAIL entries in run_manifest.json, fix "
            "raw_data.json, and re-run Step 1. Do not edit computed.json to clear this."
            % (_qc["failed"], ", ".join(_named) or "see run_manifest.json"))

    payload = json.loads(json.dumps(computed))

    trend_map = prose.get("trend")
    stray = [k for k in prose if k not in PROSE_ALLOWED and not k.startswith("_")]
    if stray:
        problems.append(
            "prose.json carries keys that are not prose fields: "
            + ", ".join(repr(s) for s in sorted(stray))
            + ". Every number on the sheet comes from computed.json; prose.json may only "
              "carry the fields in SKILL.md 11. Move any figure back to raw_data.json and "
              "re-run Step 1 - do not write it here.")
    applied = []
    for k, v in prose.items():
        if k == "trend" or k in stray or k.startswith("_"):
            continue
        payload[k] = v
        applied.append(k)
    missing_prose = [k for k in PROSE_REQUIRED if not payload.get(k)]
    if missing_prose:
        problems.append("prose.json is missing " + ", ".join(missing_prose))

    collisions = [k for k in PROSE_ALLOWED if k != "trend" and k in computed]
    if collisions:
        problems.append("PACKAGING BUG: computed.json and the prose whitelist both claim "
                        + ", ".join(collisions)
                        + " — a prose field would overwrite a computed one. Rename the "
                          "computed key or narrow PROSE_ALLOWED before shipping.")

    kpis = payload.get("operational_kpis") or []
    if not kpis:
        problems.append(
            "compute produced NO operational_kpis rows, and the sheet requires at least one. "
            "This is an upstream problem, not a prose one: the KPI universe was empty or no "
            "code in it had data for this period. Go back to the live "
            "search_lineitem(queries: \"all\", filter: \"top_kpi\") call, widen it with "
            "sector keywords and no filter if it came back thin, and re-run Step 1. Thin KPI "
            "coverage is normal for annual and half-yearly filers (SKILL.md 6.4b).")
    else:
        if trend_map is None:
            problems.append(
                "prose.json has no 'trend' map, so none of the %d operational_kpis rows can "
                "carry a trend label and eval_tearsheet.py will reject the payload. Supply "
                "{\"trend\": {\"<KPI name>\": \"Stable|Accel|Decel\", ...}} using the names "
                "computed.json prints." % len(kpis))
        elif not isinstance(trend_map, dict):
            problems.append("prose.json 'trend' must be an object mapping KPI name -> label")
        else:
            names = [str(r.get("name", "")) for r in kpis]
            for r, nm in zip(kpis, names):
                label = trend_map.get(nm)
                if label is None:
                    problems.append(f"no trend label for KPI {nm!r}")
                elif label not in ALLOWED_TRENDS:
                    problems.append(f"trend {label!r} for KPI {nm!r} is not one of "
                                    f"{', '.join(ALLOWED_TRENDS)}")
                else:
                    r["trend"] = label
            unused = [k for k in trend_map if k not in names]
            if unused:
                problems.append("trend labels supplied for names that match no KPI row: "
                                + ", ".join(repr(u) for u in unused))

    if problems:
        print("MERGE REFUSED — nothing written:", file=sys.stderr)
        for p in problems:
            print(f"   {p}", file=sys.stderr)
        return 1

    try:
        json.dump(payload, open(out_path, "w", encoding="utf-8"), indent=1)
    except OSError as exc:
        print(f"ERROR: could not write {out_path}: {exc}", file=sys.stderr)
        return 2
    labelled = sum(1 for r in kpis if r.get("trend") in ALLOWED_TRENDS)
    print(f"OK — merged {len(computed)} computed keys + {len(applied)} prose keys into "
          f"{os.path.basename(out_path)}; {labelled} of {len(kpis)} KPI rows carry a "
          f"trend label")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
