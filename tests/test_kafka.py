"""Webhook to Kafka to Iceberg on a real broker (CI runs one as a service container)."""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("LC_TEST_KAFKA") != "1",
    reason="set LC_TEST_KAFKA=1 with a broker on localhost:9092",
)


def test_events_flow_from_the_webhook_through_kafka_into_iceberg(
    world: dict[str, Any], tmp_path: Any
) -> None:
    from fastapi.testclient import TestClient

    from leakcheck import bronze, ingest

    group = f"lc-test-{uuid.uuid4().hex[:6]}"
    sink = ingest.KafkaSink("localhost:9092")
    client = TestClient(ingest.create_app(world["secrets"], sink))
    sample = world["events"][:200]
    for e in sample:
        body = json.dumps(
            {k: e[k] for k in ("event_id", "topic", "occurred_at", "account_id", "data")}
        ).encode()
        headers = {"X-LeakCheck-Signature": ingest.sign(world["secrets"][e["account_id"]], body)}
        assert (
            client.post(f"/webhooks/{e['account_id']}", content=body, headers=headers).status_code
            == 200
        )
    sink.flush()
    os.environ["LC_BRONZE_GROUP"] = group
    appended = bronze.consume(root=tmp_path / "lake", flush_seconds=1.0, idle_seconds=10)
    assert appended >= len(sample)
    ids = set(bronze.table(tmp_path / "lake").scan().to_arrow()["event_id"].to_pylist())
    assert {e["event_id"] for e in sample} <= ids
