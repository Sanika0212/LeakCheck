"""Bronze to gold on a full generated world (rule extractor)."""

from __future__ import annotations

from typing import Any

import duckdb

from leakcheck import bronze


def test_bronze_holds_every_event_and_duckdb_reads_it(world: dict[str, Any]) -> None:
    assert world["loaded"] == len(world["events"])
    loc = bronze.metadata_location(world["lake"])
    con = duckdb.connect()
    con.execute("install iceberg; load iceberg")
    assert con.execute(f"select count(*) from iceberg_scan('{loc}')").fetchone()[0] == len(
        world["events"]
    )


def test_reloading_the_same_events_does_not_change_silver(world: dict[str, Any]) -> None:
    with duckdb.connect(str(world["warehouse"])) as con:
        jobs = con.execute("select count(*) from silver.stg_jobs").fetchone()[0]
    assert jobs == len(world["world"].jobs)


def test_quality_checks_pass_and_findings_exist(world: dict[str, Any]) -> None:
    result = world["result"]
    assert result["quality"]["success"]
    assert set(result["findings"]) == {
        "uninvoiced_job",
        "unbilled_hours",
        "unbilled_materials",
        "unbilled_extra_work",
        "unapproved_discount",
    }


def test_customer_pii_never_reaches_silver_in_the_clear(world: dict[str, Any]) -> None:
    customers = world["world"].customers
    with duckdb.connect(str(world["warehouse"])) as con:
        dump = str(con.execute("select * from silver.stg_customers").fetchall())
    for c in customers[:50]:
        assert c["email"] not in dump
        assert c["phone"] not in dump
        assert c["name"] not in dump


def test_findings_on_dev_businesses_are_mostly_real(world: dict[str, Any]) -> None:
    w = world["world"]
    dev = {b.business_id for b in w.businesses if b.split == "dev"}
    truth = {(t["job_id"], t["leak_type"]) for t in w.truth}
    with duckdb.connect(str(world["warehouse"])) as con:
        found = {
            (j, t)
            for j, t, b in con.execute(
                "select job_id, finding_type, business_id from gold.findings"
            ).fetchall()
            if b in dev
        }
    assert len(found & truth) / len(found) > 0.9
