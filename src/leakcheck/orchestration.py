"""Dagster definitions: the pipeline as software-defined assets, run daily.

    dagster dev -m leakcheck.orchestration

Assets follow the medallion layers: generated source data, the Iceberg bronze table, the
warehouse (dbt silver, Great Expectations, extraction, dbt gold) and the quote model. In a
real deployment the first two are replaced by the webhook intake and the Kafka consumer,
which run continuously; the warehouse and model assets keep their schedule.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import dagster as dg

EXTRACTOR = os.environ.get("LC_EXTRACTOR", "rules")


@dg.asset(group_name="source", description="Six synthetic contractors with planted leakage.")
def generated_world() -> dg.MaterializeResult[Any]:
    from leakcheck.generator import generate

    world = generate()
    return dg.MaterializeResult(metadata={"jobs": len(world.jobs), "leaks": len(world.truth)})


@dg.asset(
    deps=[generated_world],
    group_name="bronze",
    description="Every event, as received, in the Iceberg table bronze.events.",
)
def bronze_events() -> dg.MaterializeResult[Any]:
    import shutil

    from leakcheck import bronze, events, ingest

    shutil.rmtree(bronze.LAKE, ignore_errors=True)  # the generator rebuilds the source too
    n = bronze.load_direct(events.events(), ingest.load_secrets())
    return dg.MaterializeResult(metadata={"events": n, "metadata": bronze.metadata_location()})


@dg.asset(
    deps=[bronze_events],
    group_name="warehouse",
    description="dbt silver, Great Expectations checks, extraction and dbt gold findings.",
)
def warehouse_findings() -> dg.MaterializeResult[Any]:
    from leakcheck import warehouse

    result = warehouse.build(EXTRACTOR)
    meta: dict[str, Any] = {f"{t}_count": v["count"] for t, v in result["findings"].items()}
    meta["quality_passed"] = result["quality"]["success"]
    return dg.MaterializeResult(metadata=meta)


@dg.asset(
    deps=[warehouse_findings],
    group_name="ml",
    description="LightGBM quote-hours model on Feast features, logged to MLflow.",
)
def quote_model() -> dg.MaterializeResult[Any]:
    from leakcheck import quote_model as qm
    from leakcheck.events import read_table

    splits = {
        b["business_id"]: b["split"] for b in read_table(Path("data/generated"), "businesses")
    }
    result = qm.train_and_evaluate(qm.frame(splits=splits))
    return dg.MaterializeResult(
        metadata={
            "mae_model": result["mae_hours"]["model"],
            "mae_owner": result["mae_hours"]["owner_estimate"],
            "champion": result.get("champion", False),
        }
    )


daily = dg.ScheduleDefinition(
    name="daily_findings",
    target=dg.AssetSelection.assets(warehouse_findings, quote_model),
    cron_schedule="0 6 * * *",
)

defs = dg.Definitions(
    assets=[generated_world, bronze_events, warehouse_findings, quote_model], schedules=[daily]
)
