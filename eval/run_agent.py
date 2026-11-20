"""Does the agent prepare the right drafts, and only those?

    python eval/run_agent.py --cases 100          # needs Ollama with qwen2.5:7b

Runs the LangChain agent on held-out jobs: 80 with findings (stratified by type) and 20
without. For each it checks:

1. evidence first: the first tool call reads this job's evidence;
2. right draft: one draft with exactly the job's findings, or no draft on a clean job;
3. owner notified: a notification exists for that draft in the app database;
4. amounts from tools: every dollar figure in the agent's answer appears in a tool result.

Check 3 used to ask whether the agent called ``notify_owner`` with the draft it had just
created. It did so correctly in 65 of 100 cases: the rest passed an invented draft id such
as ``draft_0001``, which the tool rejected, so the owner was never told. Creating a draft
now notifies the owner, and this check reads the notification instead of the tool call.

A case "needs no edits" when all four hold. Drafts go to a scratch app database, never to
the real approval queue.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import tempfile
import time
from collections import Counter
from pathlib import Path

import duckdb
from common import test_businesses, write

from leakcheck.agent.agent import ollama_model, run_job
from leakcheck.agent.tools import Tools

WAREHOUSE = Path("data/warehouse.duckdb")
DOLLARS = re.compile(r"\$\s?([\d,]+(?:\.\d{1,2})?)")


def cases(n: int, seed: int = 0) -> list[tuple[str, list[str]]]:
    test = sorted(test_businesses())
    with duckdb.connect(str(WAREHOUSE), read_only=True) as con:
        rows = con.execute(
            "select job_id, finding_id, finding_type, business_id from gold.findings"
        ).fetchall()
        jobs = con.execute("select job_id, business_id from gold.job_financials").fetchall()
    by_job: dict[str, list[str]] = {}
    job_type: dict[str, str] = {}
    for job, fid, ftype, biz in rows:
        if biz in test:
            by_job.setdefault(job, []).append(fid)
            job_type.setdefault(job, ftype)
    rng = random.Random(seed)
    positives = int(n * 0.8)
    per_type: dict[str, list[str]] = {}
    for job, t in job_type.items():
        per_type.setdefault(t, []).append(job)
    chosen: list[str] = []
    while len(chosen) < positives and any(per_type.values()):
        for t in sorted(per_type):
            if per_type[t] and len(chosen) < positives:
                chosen.append(per_type[t].pop(rng.randrange(len(per_type[t]))))
    clean = [j for j, b in jobs if b in test and j not in by_job]
    chosen_clean = rng.sample(clean, n - len(chosen))
    return [(j, by_job[j]) for j in chosen] + [(j, []) for j in chosen_clean]


def notified(app: Path) -> set[str]:
    """Drafts the owner has been told about, however the notification was created."""
    if not app.exists():
        return set()
    with duckdb.connect(str(app), read_only=True) as con:
        return {r[0] for r in con.execute("select distinct draft_id from notifications").fetchall()}


def score(case: tuple[str, list[str]], run: dict, notified_drafts: set[str]) -> dict[str, bool]:
    job, finding_ids = case
    calls = run["calls"]
    drafts = [c for c in calls if c["name"] == "draft_invoice"]
    evidence_first = (
        bool(calls)
        and calls[0]["name"] == "get_job_evidence"
        and calls[0]["args"].get("job_id") == job
    )
    if finding_ids:
        right_draft = (
            len(drafts) == 1
            and set(drafts[0]["args"].get("finding_ids", [])) == set(finding_ids)
            and drafts[0]["args"].get("job_id") == job
        )
        draft_ids = [
            json.loads(r).get("draft_id")
            for r in run["tool_results"]
            if r.startswith("{") and '"draft_id"' in r
        ]
        notified = bool(draft_ids) and all(d in notified_drafts for d in draft_ids if d is not None)
    else:
        right_draft = not drafts
        notified = True
    seen = {
        round(float(x.replace(",", "")), 2)
        for r in run["tool_results"]
        for x in re.findall(r"[\d,]+\.?\d*", r)
        if x.replace(",", "").replace(".", "").isdigit()
    }
    said = {round(float(x.replace(",", "")), 2) for x in DOLLARS.findall(str(run["answer"]))}
    amounts_from_tools = said <= seen
    return {
        "evidence_first": evidence_first,
        "right_draft": right_draft,
        "notified": notified,
        "amounts_from_tools": amounts_from_tools,
        "no_edits": evidence_first and right_draft and notified and amounts_from_tools,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cases", type=int, default=100)
    p.add_argument("--model", default="qwen2.5:7b")
    args = p.parse_args()
    model = ollama_model(args.model)
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        tools = Tools(WAREHOUSE, Path(tmp) / "app.duckdb")
        for i, case in enumerate(cases(args.cases), start=1):
            started = time.time()
            try:
                run = run_job(case[0], tools, model)
                checks = score(case, run, notified(Path(tmp) / "app.duckdb"))
            except Exception as exc:  # a failed run counts against every check
                run, checks = (
                    {"calls": [], "error": type(exc).__name__},
                    dict.fromkeys(
                        [
                            "evidence_first",
                            "right_draft",
                            "notified",
                            "amounts_from_tools",
                            "no_edits",
                        ],
                        False,
                    ),
                )
            results.append(
                {
                    "job_id": case[0],
                    "findings": len(case[1]),
                    **checks,
                    "seconds": round(time.time() - started, 1),
                    "tool_calls": [c["name"] for c in run["calls"]],
                }
            )
            if i % 10 == 0:
                done = Counter(k for r in results for k, v in r.items() if v is True)
                print(
                    f"  {i}/{args.cases}",
                    {k: done[k] for k in ("right_draft", "no_edits")},
                    flush=True,
                )
        # Every drafted amount must equal the warehouse's amount for that finding.
        with duckdb.connect(str(Path(tmp) / "app.duckdb")) as con:
            con.execute(f"attach '{WAREHOUSE}' as wh (read_only)")
            mismatched, drafted_lines = con.execute(
                "select count(*) filter (where abs(l.amount - f.amount) > 0.005), count(*) "
                "from draft_lines l join wh.gold.findings f using (finding_id)"
            ).fetchone()
    n = len(results)
    summary = {
        k: round(sum(r[k] for r in results) / n, 3)
        for k in ("evidence_first", "right_draft", "notified", "amounts_from_tools", "no_edits")
    }
    pos = [r for r in results if r["findings"]]
    neg = [r for r in results if not r["findings"]]
    out = {
        "model": args.model,
        "cases": n,
        "with_findings": len(pos),
        "clean_jobs": len(neg),
        "summary": summary,
        "no_edits_with_findings": round(sum(r["no_edits"] for r in pos) / len(pos), 3),
        "no_draft_on_clean_jobs": round(sum(r["right_draft"] for r in neg) / len(neg), 3),
        "median_seconds": sorted(r["seconds"] for r in results)[n // 2],
        "drafted_lines": drafted_lines,
        "drafted_amounts_not_from_warehouse": mismatched,
        "cases_detail": results,
    }
    print(summary, "wrote", write("agent", out))


if __name__ == "__main__":
    main()
