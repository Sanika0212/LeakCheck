"""Bronze layer: every event, as received, in an Apache Iceberg table.

The table ``bronze.events`` lives under ``data/lake`` with a SQL catalog (SQLite) locally;
in AWS the same table is registered in Glue with its files on S3. It is partitioned by
entity and holds the raw payload as JSON text, so nothing is interpreted before silver.

Two ways in:

1. :func:`consume` reads the ``lc.*`` Kafka topics the webhook intake produces to and
   appends in micro-batches. Offsets are committed only after the batch is in Iceberg, so a
   crash replays events instead of losing them (at least once; silver drops duplicates by
   event id).
2. :func:`load_direct` normalizes generated events with the same code as the webhook and
   appends them straight away, for CI and quick local runs without a broker.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pyarrow as pa

LAKE = Path("data/lake")
TABLE = "bronze.events"
FIELDS = [
    ("event_id", pa.string()),
    ("topic", pa.string()),
    ("entity", pa.string()),
    ("entity_id", pa.string()),
    ("business_id", pa.string()),
    ("occurred_at", pa.timestamp("us")),
    ("received_at", pa.timestamp("us")),
    ("ingested_at", pa.timestamp("us")),
    ("payload", pa.string()),
]
ARROW_SCHEMA = pa.schema([pa.field(n, t, nullable=(n == "payload")) for n, t in FIELDS])


def catalog(root: Path = LAKE) -> Any:
    from pyiceberg.catalog.sql import SqlCatalog

    root.mkdir(parents=True, exist_ok=True)
    return SqlCatalog(
        "leakcheck",
        uri=f"sqlite:///{root.resolve()}/catalog.db",
        warehouse=f"file://{root.resolve()}",
    )


def table(root: Path = LAKE) -> Any:
    from pyiceberg.transforms import IdentityTransform

    cat = catalog(root)
    if not cat.namespace_exists("bronze"):
        cat.create_namespace("bronze")
    if cat.table_exists(TABLE):
        return cat.load_table(TABLE)
    tbl = cat.create_table(TABLE, schema=ARROW_SCHEMA)
    with tbl.update_spec() as spec:
        spec.add_field("entity", IdentityTransform(), "entity")
    return cat.load_table(TABLE)


def metadata_location(root: Path = LAKE) -> str:
    """Current metadata file, which DuckDB's ``iceberg_scan`` reads."""
    return str(table(root).metadata_location).removeprefix("file://")


def _ts(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value).replace(tzinfo=None)


def append(records: list[dict[str, Any]], root: Path = LAKE) -> int:
    if not records:
        return 0
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    columns: dict[str, list[Any]] = {name: [] for name, _ in FIELDS}
    for r in records:
        for name, _ in FIELDS:
            if name == "ingested_at":
                columns[name].append(now)
            elif name in ("occurred_at", "received_at"):
                columns[name].append(_ts(r[name]))
            else:
                columns[name].append(r[name])
    table(root).append(pa.table(columns, schema=ARROW_SCHEMA))
    return len(records)


def load_direct(
    events: Iterable[dict[str, Any]], secrets: dict[str, str], root: Path = LAKE, batch: int = 5000
) -> int:
    """Run events through the webhook's checks into a memory sink, then into Iceberg."""
    from leakcheck.ingest import MemorySink, receive, sign

    sink = MemorySink()
    for e in events:
        body = json.dumps(
            {k: e[k] for k in ("event_id", "topic", "occurred_at", "account_id", "data")},
            separators=(",", ":"),
        ).encode()
        headers = {"X-LeakCheck-Signature": sign(secrets[e["account_id"]], body)}
        receive(e["account_id"], body, headers, secrets, sink)
    records = [json.loads(v) for msgs in sink.topics.values() for _, v in msgs]
    total = 0
    for i in range(0, len(records), batch):
        total += append(records[i : i + batch], root)
    return total


def consume(
    bootstrap: str = "localhost:9092",
    root: Path = LAKE,
    batch: int = 2000,
    flush_seconds: float = 5.0,
    idle_seconds: float | None = None,
) -> int:
    """Kafka to Iceberg in micro-batches; returns after ``idle_seconds`` with no messages."""
    from confluent_kafka import Consumer

    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": "leakcheck-bronze",
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
        }
    )
    consumer.subscribe(["^lc\\..*"])
    pending: list[dict[str, Any]] = []
    total, last_flush, last_message = 0, time.monotonic(), time.monotonic()
    try:
        while True:
            msg = consumer.poll(0.5)
            now = time.monotonic()
            value = msg.value() if msg is not None and not msg.error() else None
            if value:
                pending.append(json.loads(value))
                last_message = now
            if pending and (len(pending) >= batch or now - last_flush >= flush_seconds):
                total += append(pending, root)
                consumer.commit(asynchronous=False)
                pending, last_flush = [], now
            if idle_seconds is not None and now - last_message > idle_seconds and not pending:
                return total
    finally:
        if pending:
            total += append(pending, root)
            consumer.commit(asynchronous=False)
        consumer.close()
