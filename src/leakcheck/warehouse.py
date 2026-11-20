"""Bronze to gold: dbt silver, extraction, dbt gold, in one call.

    leakcheck warehouse --extractor rules     # or llm

The dbt project in ``warehouse/dbt`` runs on DuckDB and reads bronze straight from Iceberg
(``iceberg_scan`` on the table's current metadata file, which this module looks up in the
catalog). Silver models and their tests run first, then the extractor labels messages and
crew notes, then the gold models (financials, extra-work signals, findings) and their tests.
Great Expectations checks run on silver before anything reads it; a failure stops the build.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import duckdb

from leakcheck import bronze, quality
from leakcheck.extract import Extractor, LLMExtractor, RuleExtractor
from leakcheck.extract import run as run_extraction

DBT_DIR = Path("warehouse/dbt")
WAREHOUSE = Path("data/warehouse.duckdb")


class DbtFailed(RuntimeError):
    pass


def dbt(args: list[str], variables: dict[str, Any]) -> None:
    from dbt.cli.main import dbtRunner

    os.environ.setdefault("LC_WAREHOUSE", str(WAREHOUSE.resolve()))
    extra = []
    if target := os.environ.get("LC_DBT_TARGET"):  # lets tests run beside a real build
        extra = ["--target-path", target, "--log-path", target]
    result = dbtRunner().invoke(
        [
            *args,
            "--project-dir",
            str(DBT_DIR),
            "--profiles-dir",
            str(DBT_DIR),
            "--vars",
            json.dumps(variables),
            "--quiet",
            *extra,
        ]
    )
    if not result.success:
        raise DbtFailed(f"dbt {' '.join(args)} failed: {result.exception or 'see output'}")


def extractor(name: str) -> Extractor:
    return LLMExtractor() if name == "llm" else RuleExtractor()


def build(
    extractor_name: str = "rules",
    lake: Path = bronze.LAKE,
    as_of: str = "2025-10-31",
    progress: bool = False,
) -> dict[str, Any]:
    WAREHOUSE.parent.mkdir(parents=True, exist_ok=True)
    variables = {"bronze_metadata": bronze.metadata_location(lake), "as_of": as_of}
    dbt(["seed"], variables)
    dbt(["build", "--select", "silver"], variables)
    with duckdb.connect(str(WAREHOUSE)) as con:
        report = quality.validate(con)
        if not report["success"]:
            raise quality.QualityFailed(json.dumps(report["tables"]))
        counts = run_extraction(con, extractor(extractor_name), progress=progress)
    dbt(["build", "--select", "gold"], variables)
    with duckdb.connect(str(WAREHOUSE)) as con:
        findings = con.execute(
            "select finding_type, count(*), round(sum(amount)) from gold.findings group by 1 order by 1"
        ).fetchall()
    return {
        "quality": report,
        "extracted": counts,
        "findings": {t: {"count": n, "amount": a} for t, n, a in findings},
    }
