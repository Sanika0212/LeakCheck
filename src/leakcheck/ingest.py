"""Webhook intake: verify, normalize, produce to Kafka.

One function, :func:`receive`, does the work; it is wrapped by a FastAPI app for local runs
(``leakcheck serve``) and by :func:`lambda_handler` for API Gateway and AWS Lambda.

1. **Verify.** The sender signs the raw body with the account's webhook secret
   (HMAC-SHA256, base64) in ``X-LeakCheck-Signature``. A missing or wrong signature is
   rejected with 401 before the body is parsed.
2. **Normalize.** The event becomes a flat record: event id, topic, entity, entity id,
   account, when it happened, when it arrived, and the payload as JSON text.
3. **Produce.** One Kafka topic per entity (``lc.job``, ``lc.visit`` ...), keyed by entity
   id so every version of an entity lands in order on one partition.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import os
from collections import defaultdict
from typing import Any, Protocol

TOPIC_ENTITY = {
    "QUOTE_CREATE": "quote", "QUOTE_APPROVED": "quote", "JOB_COMPLETE": "job",
    "VISIT_COMPLETE": "visit", "EXPENSE_CREATE": "expense", "MESSAGE_CREATE": "message",
    "CHANGE_ORDER_CREATE": "change_order", "INVOICE_CREATE": "invoice",
    "CLIENT_CREATE": "customer",
}
ID_FIELD = {"quote": "quote_id", "job": "job_id", "visit": "visit_id", "expense": "expense_id",
            "message": "message_id", "change_order": "change_order_id",
            "invoice": "invoice_id", "customer": "customer_id"}
SIGNATURE_HEADER = "x-leakcheck-signature"


class Rejected(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


class Sink(Protocol):
    def send(self, topic: str, key: str, value: bytes) -> None: ...
    def flush(self) -> None: ...


class MemorySink:
    def __init__(self) -> None:
        self.topics: dict[str, list[tuple[str, bytes]]] = defaultdict(list)

    def send(self, topic: str, key: str, value: bytes) -> None:
        self.topics[topic].append((key, value))

    def flush(self) -> None:
        return None


class KafkaSink:
    def __init__(self, bootstrap: str = "localhost:9092") -> None:
        from confluent_kafka import Producer

        self.producer = Producer({"bootstrap.servers": bootstrap, "enable.idempotence": True,
                                  "linger.ms": 20, "compression.type": "zstd"})

    def send(self, topic: str, key: str, value: bytes) -> None:
        self.producer.produce(topic, key=key.encode(), value=value)
        self.producer.poll(0)

    def flush(self) -> None:
        self.producer.flush(30)


def sign(secret: str, body: bytes) -> str:
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def receive(account: str, body: bytes, headers: dict[str, str], secrets: dict[str, str],
            sink: Sink) -> dict[str, Any]:
    lowered = {k.lower(): v for k, v in headers.items()}
    secret = secrets.get(account)
    signature = lowered.get(SIGNATURE_HEADER, "")
    if secret is None:
        raise Rejected(404, "unknown account")
    if not signature or not hmac.compare_digest(sign(secret, body), signature):
        raise Rejected(401, "bad signature")
    try:
        event = json.loads(body)
        entity = TOPIC_ENTITY[event["topic"]]
        entity_id = str(event["data"][ID_FIELD[entity]])
    except (ValueError, KeyError) as exc:
        raise Rejected(400, f"malformed event: {exc}") from exc
    if event.get("account_id") != account:
        raise Rejected(400, "account mismatch")
    record = {
        "event_id": event["event_id"],
        "topic": event["topic"],
        "entity": entity,
        "entity_id": entity_id,
        "business_id": account,
        "occurred_at": event["occurred_at"],
        "received_at": dt.datetime.now(dt.UTC).replace(tzinfo=None).isoformat(),
        "payload": json.dumps(event["data"], separators=(",", ":")),
    }
    sink.send(f"lc.{entity}", entity_id, json.dumps(record).encode())
    return {"accepted": True, "entity": entity, "entity_id": entity_id}


def load_secrets(path: str | None = None) -> dict[str, str]:
    """Webhook secrets by account; in AWS they come from Secrets Manager as the same JSON."""
    path = path or os.environ.get("LC_SECRETS", "data/generated/businesses.jsonl")
    out = {}
    with open(path) as fh:
        for line in fh:
            if line.strip():
                row = json.loads(line)
                out[row["business_id"]] = row["webhook_secret"]
    return out


def create_app(secrets: dict[str, str] | None = None, sink: Sink | None = None) -> Any:
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse

    app = FastAPI(title="LeakCheck webhooks")
    state: dict[str, Any] = {"secrets": secrets, "sink": sink}

    def resources() -> tuple[dict[str, str], Sink]:
        if state["secrets"] is None:
            state["secrets"] = load_secrets()
        if state["sink"] is None:
            state["sink"] = KafkaSink(os.environ.get("LC_KAFKA", "localhost:9092"))
        return state["secrets"], state["sink"]

    @app.post("/webhooks/{account}")
    async def webhook(account: str, request: Request) -> JSONResponse:
        body = await request.body()
        secrets_, sink_ = resources()
        try:
            return JSONResponse(receive(account, body, dict(request.headers), secrets_, sink_))
        except Rejected as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


_LAMBDA_STATE: dict[str, Any] = {}


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """API Gateway (REST, proxy integration) entry point; same checks as the local app."""
    if "sink" not in _LAMBDA_STATE:
        _LAMBDA_STATE["secrets"] = json.loads(os.environ["LC_WEBHOOK_SECRETS"])
        _LAMBDA_STATE["sink"] = KafkaSink(os.environ["LC_KAFKA"])
    body = event.get("body") or ""
    raw = base64.b64decode(body) if event.get("isBase64Encoded") else body.encode()
    account = (event.get("pathParameters") or {}).get("account", "")
    try:
        result = receive(account, raw, event.get("headers") or {}, _LAMBDA_STATE["secrets"],
                         _LAMBDA_STATE["sink"])
        _LAMBDA_STATE["sink"].flush()
        return {"statusCode": 200, "body": json.dumps(result)}
    except Rejected as exc:
        return {"statusCode": exc.status, "body": json.dumps({"detail": exc.detail})}
