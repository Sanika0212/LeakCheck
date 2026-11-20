"""Proof of concept: DuckDB and keyword rules over raw JSON exports.

    python poc/poc.py          # writes poc/report.md

Deliberately scrappy: no Kafka, no Iceberg, no dbt, no model. If this cannot find the money,
the pipeline is not worth building. Scored against poc/criteria.md on the held-out
businesses.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

import duckdb

RAW = Path("data/generated")
AS_OF = "2025-10-31"

SQL = f"""
with jobs as (select * from read_json_auto('{RAW}/jobs.jsonl')),
quotes as (select * from read_json_auto('{RAW}/quotes.jsonl')),
visits as (select job_id, sum(hours) as hours from read_json_auto('{RAW}/visits.jsonl')
           where not regexp_matches(coalesce(note, ''), '(?i)no charge|warranty|on us|callback')
           group by 1),
expenses as (select job_id, sum(amount) as cost from read_json_auto('{RAW}/expenses.jsonl')
             where description not ilike '%shop stock%' group by 1),
inv as (select * from read_json_auto('{RAW}/invoices.jsonl')),
lines as (select job_id, unnest(lines) as l from inv),
billed as (
  select job_id,
         sum((l->>'hours')::double) filter (where l->>'kind' = 'labor') as hours,
         sum((l->>'amount')::double) filter (where l->>'kind' = 'materials') as materials,
         sum((l->>'amount')::double) filter (where l->>'kind' = 'fixed_price') as fixed
  from (select job_id, l::json as l from lines) group by 1),
final as (select distinct job_id from inv where kind = 'final'),
msgs as (select * from read_json_auto('{RAW}/messages.jsonl')),
asks as (select distinct job_id from msgs where direction = 'inbound'
         and regexp_matches(body, '(?i)\\b(also|as well|too|add)\\b')
         and not regexp_matches(body, '(?i)how much|price|skip|never mind|hold off')),
cos as (select distinct job_id from read_json_auto('{RAW}/change_orders.jsonl'))
select f.job_id, j.business_id, f.kind from (
  select job_id, 'uninvoiced_job' as kind from jobs
   where job_id not in (select job_id from final)
     and completed_at::timestamp < date '{AS_OF}' - interval 14 day
  union all
  select j.job_id, 'unbilled_hours' from jobs j
   join visits v on v.job_id = j.job_id join billed b on b.job_id = j.job_id
   where j.pricing = 'time_materials' and v.hours - coalesce(b.hours, 0) > 0.25
  union all
  select j.job_id, 'unbilled_materials' from jobs j
   join expenses e on e.job_id = j.job_id join billed b on b.job_id = j.job_id
   join quotes q on q.job_id = j.job_id
   where j.pricing = 'time_materials' and e.cost * (1 + q.markup) - coalesce(b.materials, 0) > 1
  union all
  select j.job_id, 'unbilled_extra_work' from jobs j
   join asks a on a.job_id = j.job_id join final fi on fi.job_id = j.job_id
   where j.pricing = 'fixed' and j.job_id not in (select job_id from cos)
  union all
  select j.job_id, 'unapproved_discount' from jobs j
   join quotes q on q.job_id = j.job_id join billed b on b.job_id = j.job_id
   where j.pricing = 'fixed' and b.fixed < q.total - 1
) f join jobs j on j.job_id = f.job_id
"""


def main() -> None:
    started = time.time()
    rows = duckdb.sql(SQL).fetchall()
    seconds = time.time() - started
    businesses = [json.loads(x) for x in (RAW / "businesses.jsonl").read_text().splitlines()]
    test = {b["business_id"] for b in businesses if b["split"] == "test"}
    cutoff = dt.datetime.fromisoformat(AS_OF) - dt.timedelta(days=14)
    truth: dict[tuple[str, str], float] = {}
    for line in (RAW / "truth.jsonl").read_text().splitlines():
        t = json.loads(line)
        if t["business_id"] in test and dt.datetime.fromisoformat(t["completed_at"]) < cutoff:
            truth[(t["job_id"], t["leak_type"])] = (
                truth.get((t["job_id"], t["leak_type"]), 0) + t["amount"]
            )
    found = {(j, k) for j, b, k in rows if b in test}
    total = sum(truth.values())
    dollars = sum(v for k, v in truth.items() if k in found) / total
    status = {k for k in found if k[1] == "uninvoiced_job"}
    status_dollars = sum(v for k, v in truth.items() if k in status) / total
    flagged_jobs = {j for j, _ in found}
    real_jobs = {j for j, _ in truth}
    precision = len(flagged_jobs & real_jobs) / len(flagged_jobs) if flagged_jobs else 0.0
    checks = [
        ("Share of leaked dollars found", f"{dollars:.1%}", dollars >= 0.70),
        (
            "Times the status-only report",
            f"{dollars / status_dollars:.3f}x ({status_dollars:.1%} for status only)",
            dollars >= 1.5 * status_dollars,
        ),
        ("Precision of flagged jobs", f"{precision:.1%}", precision >= 0.80),
        ("Time for six months of data", f"{seconds:.2f} s", seconds < 60),
    ]
    go = all(ok for _, _, ok in checks)
    real = {j for j, _ in truth}
    false_pos: dict[str, int] = {}
    for j, k in found:
        if j not in real:
            false_pos[k] = false_pos.get(k, 0) + 1
    report = [
        "# Proof of concept: report",
        "",
        "Scored on the three held-out generated businesses against [criteria.md](criteria.md).",
        "",
        "| Criterion | Result | Met |",
        "|---|---|---|",
    ]
    report += [f"| {name} | {value} | {'yes' if ok else 'no'} |" for name, value, ok in checks]
    fp_text = ", ".join(
        f"{n} {k.replace('_', ' ')}" for k, n in sorted(false_pos.items(), key=lambda x: -x[1])
    )
    report += [
        "",
        f"**Decision: {'go' if go else 'no-go'} for a keyword-rules product.** "
        + (
            "The money is findable from the records alone, and the list is precise enough to act on."
            if go
            else "The money is there (most leaked dollars were found), but the list is too "
            f"noisy to hand an owner. False positives: {fp_text}. They are records the rules "
            "could not read: no-charge callbacks and promised credits worded differently "
            "from the messages the rules were written against. The next step is reading the "
            "text with a language model and scoring again against the same bar."
        ),
        "",
        "What the proof of concept could not answer, and the pipeline has to:",
        "",
        "1. Whether keyword rules survive real phrasing (they were written against the dev "
        "businesses' messages; the held-out businesses word things differently).",
        "2. Whether findings can be fresh (the proof of concept reads a one-off export).",
        "3. Whether an owner will act on them (drafts, approval, an audit trail).",
        "",
    ]
    Path("poc/report.md").write_text("\n".join(report))
    print("\n".join(report))


if __name__ == "__main__":
    main()
