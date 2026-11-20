from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(scope="session")
def world(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """One generated world, its bronze table and a warehouse built with the rule extractor."""
    from leakcheck import bronze, events, ingest, warehouse
    from leakcheck.generator import generate

    root = tmp_path_factory.mktemp("lc")
    gen = root / "generated"
    w = generate(out=gen)
    secrets = ingest.load_secrets(str(gen / "businesses.jsonl"))
    evts = events.events(gen)
    lake = root / "lake"
    loaded = bronze.load_direct(evts, secrets, root=lake)
    db = root / "warehouse.duckdb"
    os.environ["LC_WAREHOUSE"] = str(db)
    os.environ["LC_DBT_TARGET"] = str(root / "dbt-target")
    warehouse.WAREHOUSE = db
    result = warehouse.build("rules", lake=lake)
    return {
        "root": root,
        "gen": gen,
        "world": w,
        "secrets": secrets,
        "events": evts,
        "lake": lake,
        "loaded": loaded,
        "warehouse": db,
        "result": result,
    }


@pytest.fixture
def app_db(tmp_path: Path) -> Path:
    return tmp_path / "app.duckdb"
