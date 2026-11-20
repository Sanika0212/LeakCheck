"""Webhook intake: signatures, normalization, topics."""

from __future__ import annotations

import json
from typing import Any

import pytest

from leakcheck.ingest import MemorySink, Rejected, create_app, lambda_handler, receive, sign


def _body(world: dict[str, Any]) -> tuple[str, bytes]:
    e = world["events"][0]
    body = json.dumps(
        {k: e[k] for k in ("event_id", "topic", "occurred_at", "account_id", "data")}
    ).encode()
    return e["account_id"], body


def test_signed_event_is_normalized_onto_its_entity_topic(world: dict[str, Any]) -> None:
    account, body = _body(world)
    sink = MemorySink()
    out = receive(
        account,
        body,
        {"X-LeakCheck-Signature": sign(world["secrets"][account], body)},
        world["secrets"],
        sink,
    )
    assert out["accepted"]
    ((topic, msgs),) = sink.topics.items()
    assert topic == f"lc.{out['entity']}"
    record = json.loads(msgs[0][1])
    assert record["entity_id"] == msgs[0][0] == out["entity_id"]
    assert json.loads(record["payload"])


@pytest.mark.parametrize("signature", ["", "bm90IHRoZSBzaWduYXR1cmU="])
def test_missing_or_wrong_signature_is_rejected(world: dict[str, Any], signature: str) -> None:
    account, body = _body(world)
    with pytest.raises(Rejected) as exc:
        receive(account, body, {"X-LeakCheck-Signature": signature}, world["secrets"], MemorySink())
    assert exc.value.status == 401


def test_tampered_body_fails_the_signature(world: dict[str, Any]) -> None:
    account, body = _body(world)
    sig = sign(world["secrets"][account], body)
    with pytest.raises(Rejected):
        receive(
            account,
            body.replace(b"20", b"21", 1),
            {"X-LeakCheck-Signature": sig},
            world["secrets"],
            MemorySink(),
        )


def test_event_for_another_account_is_rejected(world: dict[str, Any]) -> None:
    account, body = _body(world)
    other = next(a for a in world["secrets"] if a != account)
    with pytest.raises(Rejected) as exc:
        receive(
            other,
            body,
            {"X-LeakCheck-Signature": sign(world["secrets"][other], body)},
            world["secrets"],
            MemorySink(),
        )
    assert exc.value.status == 400


def test_http_app_and_lambda_share_the_checks(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    account, body = _body(world)
    sink = MemorySink()
    client = TestClient(create_app(world["secrets"], sink))
    headers = {"X-LeakCheck-Signature": sign(world["secrets"][account], body)}
    assert client.post(f"/webhooks/{account}", content=body, headers=headers).status_code == 200
    assert client.post(f"/webhooks/{account}", content=body, headers={}).status_code == 401

    import leakcheck.ingest as ingest

    monkeypatch.setitem(ingest._LAMBDA_STATE, "secrets", world["secrets"])
    monkeypatch.setitem(ingest._LAMBDA_STATE, "sink", sink)
    event = {"body": body.decode(), "headers": headers, "pathParameters": {"account": account}}
    assert lambda_handler(event, None)["statusCode"] == 200
    assert lambda_handler({**event, "headers": {}}, None)["statusCode"] == 401
