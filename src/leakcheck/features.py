"""Feature store for the quote model: the same features for training and at quote time.

``gold.quote_features`` is exported to Parquet (Feast's offline store). ``apply`` registers
the entity and feature view, ``materialize`` loads the latest values into the SQLite online
store, and ``online`` reads them back the way the quoting tool would before showing an
estimate. In Snowflake the offline source would be the gold table itself and Redis the
online store; the definitions stay the same.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

ROOT = Path("data/features")
FEATURES = [
    "job_type_code",
    "condition_code",
    "stories",
    "rooms",
    "doors",
    "area_sqft",
    "month",
    "est_hours",
]
JOB_TYPES = ["interior", "exterior", "cabinets", "deck", "commercial"]
CONDITIONS = ["good", "fair", "poor"]


def export(warehouse: Path = Path("data/warehouse.duckdb"), root: Path = ROOT) -> Path:
    """Write gold.quote_features as Parquet with numeric codes Feast and LightGBM can use."""
    root.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(warehouse)) as con:
        df = con.execute("select * from gold.quote_features").df()
    df["job_type_code"] = (
        df["job_type"].map({t: i for i, t in enumerate(JOB_TYPES)}).astype("int64")
    )
    df["condition_code"] = (
        df["condition"].map({c: i for i, c in enumerate(CONDITIONS)}).astype("int64")
    )
    for col in ("stories", "rooms", "doors", "month"):
        df[col] = df[col].astype("int64")
    df["event_timestamp"] = pd.to_datetime(df["quoted_at"])
    df["created"] = df["event_timestamp"]
    path = root / "quote_features.parquet"
    df.to_parquet(path, index=False)
    return path


def _write_config(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "feature_store.yaml").write_text(
        "project: leakcheck\n"
        f"registry: {(root / 'registry.db').resolve()}\n"
        "provider: local\n"
        "online_store:\n"
        "  type: sqlite\n"
        f"  path: {(root / 'online.db').resolve()}\n"
        "offline_store:\n"
        "  type: file\n"
        "entity_key_serialization_version: 3\n"
    )


def definitions(root: Path = ROOT) -> tuple[Any, Any]:
    from feast import Entity, FeatureView, Field, FileSource
    from feast.types import Float64, Int64

    job = Entity(name="job", join_keys=["job_id"], description="A quoted painting job")
    source = FileSource(
        name="quote_features_source",
        path=str((root / "quote_features.parquet").resolve()),
        timestamp_field="event_timestamp",
        created_timestamp_column="created",
    )
    int_fields = {"job_type_code", "condition_code", "stories", "rooms", "doors", "month"}
    view = FeatureView(
        name="quote_features",
        entities=[job],
        ttl=dt.timedelta(days=400),
        schema=[Field(name=f, dtype=Int64 if f in int_fields else Float64) for f in FEATURES],
        source=source,
        online=True,
    )
    return job, view


def store(root: Path = ROOT) -> Any:
    from feast import FeatureStore

    _write_config(root)
    return FeatureStore(repo_path=str(root))


def apply(root: Path = ROOT) -> Any:
    fs = store(root)
    fs.apply(list(definitions(root)))
    return fs


def training_frame(entities: pd.DataFrame, root: Path = ROOT) -> pd.DataFrame:
    """Point-in-time features for ``entities`` (job_id, event_timestamp)."""
    fs = store(root)
    refs = [f"quote_features:{f}" for f in FEATURES]
    result: pd.DataFrame = fs.get_historical_features(entity_df=entities, features=refs).to_df()
    return result


def materialize(end: dt.datetime, root: Path = ROOT) -> None:
    store(root).materialize(start_date=end - dt.timedelta(days=400), end_date=end)


def online(job_ids: list[str], root: Path = ROOT) -> list[dict[str, Any]]:
    fs = store(root)
    refs = [f"quote_features:{f}" for f in FEATURES]
    rows = fs.get_online_features(features=refs, entity_rows=[{"job_id": j} for j in job_ids])
    data = rows.to_dict()
    return [{k: data[k][i] for k in data} for i in range(len(job_ids))]
