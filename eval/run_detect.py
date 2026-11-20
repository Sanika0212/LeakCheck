"""Does LeakCheck find the leaked money? Held-out businesses only.

    python eval/run_detect.py --extractor rules     # or llm

Builds the warehouse with the chosen extractor, then compares ``gold.findings`` with the
ground truth the generator kept aside, on the three test businesses (detectors were
written while looking at the three dev businesses). A finding is right when the same job
has a planted leak of the same type. Dollar recall is the share of leaked dollars sitting on
jobs with a matching finding.

Baseline: the status-only report many tools offer, "completed jobs with no invoice".
Leaks on jobs completed after the as-of date minus the billing grace period cannot be seen
yet and are left out of the truth.
"""

from __future__ import annotations

import argparse
import datetime as dt
from collections import defaultdict

import duckdb
from common import prf, read_jsonl, test_businesses, write

from leakcheck import warehouse

TYPES = [
    "uninvoiced_job",
    "unbilled_hours",
    "unbilled_materials",
    "unbilled_extra_work",
    "unapproved_discount",
]


def evaluate(findings: set[tuple[str, str]], truth: dict[tuple[str, str], float]) -> dict:
    per_type = {}
    for t in TYPES:
        f = {k for k in findings if k[1] == t}
        g = {k for k in truth if k[1] == t}
        per_type[t] = prf(len(f & g), len(f - g), len(g - f)) | {
            "dollars": round(sum(truth[k] for k in g)),
            "dollars_found": round(sum(truth[k] for k in g & f)),
        }
    all_f, all_g = findings, set(truth)
    total = sum(truth.values())
    found = sum(v for k, v in truth.items() if k in all_f)
    return {
        "overall": prf(len(all_f & all_g), len(all_f - all_g), len(all_g - all_f)),
        "dollar_recall": round(found / total, 3) if total else 0.0,
        "leaked_dollars": round(total),
        "found_dollars": round(found),
        "per_type": per_type,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--extractor", default="rules", choices=["rules", "llm"])
    p.add_argument("--as-of", default="2025-10-31")
    p.add_argument("--grace-days", type=int, default=14)
    args = p.parse_args()

    warehouse.build(args.extractor, as_of=args.as_of, progress=args.extractor == "llm")
    test = test_businesses()
    cutoff = dt.datetime.fromisoformat(args.as_of) - dt.timedelta(days=args.grace_days)
    truth: dict[tuple[str, str], float] = defaultdict(float)
    for row in read_jsonl("truth"):
        if row["business_id"] in test and dt.datetime.fromisoformat(row["completed_at"]) < cutoff:
            truth[(row["job_id"], row["leak_type"])] += row["amount"]
    with duckdb.connect(str(warehouse.WAREHOUSE)) as con:
        rows = con.execute(
            "select job_id, finding_type, business_id, amount from gold.findings"
        ).fetchall()
    findings = {(j, t) for j, t, b, _ in rows if b in test}
    status_only = {(j, t) for j, t in findings if t == "uninvoiced_job"}
    result = {
        "extractor": args.extractor,
        "as_of": args.as_of,
        "test_businesses": sorted(test),
        "leakcheck": evaluate(findings, truth),
        "status_only_baseline": evaluate(status_only, truth),
    }
    print(
        {k: result["leakcheck"][k] for k in ("dollar_recall", "leaked_dollars", "found_dollars")},
        "baseline",
        result["status_only_baseline"]["dollar_recall"],
    )
    for t, m in result["leakcheck"]["per_type"].items():
        print(
            f"  {t:22s} P {m['precision']:.3f} R {m['recall']:.3f} F1 {m['f1']:.3f}",
            f"${m['dollars_found']}/{m['dollars']}",
        )
    print("wrote", write(f"detect-{args.extractor}", result))


if __name__ == "__main__":
    main()
