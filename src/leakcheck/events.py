"""Turn the generated world into the webhook events a field-service system would send.

Every create or update is an event: ``{"event_id", "topic", "occurred_at", "account_id",
"data"}``, ordered by time. Fields only the simulator knows (true hours, rain days) are
dropped; the ground truth never leaves ``data/generated/truth.jsonl``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

HIDDEN = {"true_hours", "rain_days", "no_charge", "shop_stock"}

# topic -> (entity table, timestamp field, timestamp suffix for date-only fields)
TOPICS: list[tuple[str, str, str]] = [
    ("QUOTE_CREATE", "quotes", "created_at"),
    ("QUOTE_APPROVED", "quotes", "approved_at"),
    ("JOB_COMPLETE", "jobs", "completed_at"),
    ("VISIT_COMPLETE", "visits", "date"),
    ("EXPENSE_CREATE", "expenses", "date"),
    ("MESSAGE_CREATE", "messages", "sent_at"),
    ("CHANGE_ORDER_CREATE", "change_orders", "created_at"),
    ("INVOICE_CREATE", "invoices", "created_at"),
]
ENTITY = {
    "quotes": ("quote", "quote_id"),
    "jobs": ("job", "job_id"),
    "visits": ("visit", "visit_id"),
    "expenses": ("expense", "expense_id"),
    "messages": ("message", "message_id"),
    "change_orders": ("change_order", "change_order_id"),
    "invoices": ("invoice", "invoice_id"),
    "customers": ("customer", "customer_id"),
}


def read_table(root: Path, name: str) -> list[dict[str, Any]]:
    path = root / f"{name}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _when(row: dict[str, Any], field: str) -> str:
    value = str(row[field])
    return value if "T" in value else f"{value}T17:00:00"


def events(root: Path = Path("data/generated")) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    customers = {c["customer_id"]: c for c in read_table(root, "customers")}
    first_seen: dict[str, str] = {}
    for topic, table, field in TOPICS:
        entity, key = ENTITY[table]
        for row in read_table(root, table):
            data = {k: v for k, v in row.items() if k not in HIDDEN}
            occurred = _when(row, field)
            if table == "quotes":
                cid = row["customer_id"]
                first_seen[cid] = min(first_seen.get(cid, occurred), occurred)
            out.append(_event(topic, entity, data[key], row["business_id"], occurred, data))
    for cid, occurred in first_seen.items():
        c = customers[cid]
        out.append(_event("CLIENT_CREATE", "customer", cid, c["business_id"], occurred, c))
    out.sort(key=lambda e: (e["occurred_at"], e["topic"]))
    return out


def _event(
    topic: str, entity: str, entity_id: str, account: str, occurred: str, data: dict[str, Any]
) -> dict[str, Any]:
    event_id = hashlib.sha1(f"{topic}:{entity_id}:{occurred}".encode()).hexdigest()[:16]
    return {
        "event_id": event_id,
        "topic": topic,
        "entity": entity,
        "entity_id": entity_id,
        "account_id": account,
        "occurred_at": occurred,
        "data": data,
    }


def iter_bodies(evts: list[dict[str, Any]]) -> Iterator[tuple[str, bytes]]:
    """``(account_id, raw JSON body)`` pairs, as a webhook sender would post them."""
    for e in evts:
        body = {k: e[k] for k in ("event_id", "topic", "occurred_at", "account_id", "data")}
        yield e["account_id"], json.dumps(body, separators=(",", ":")).encode()
