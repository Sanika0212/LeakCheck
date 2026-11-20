"""Agent tools, the MCP server and the agent loop (with a scripted model, no Ollama)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import duckdb
import pytest

from leakcheck.agent.tools import Tools


def _job_with_findings(warehouse: Path) -> tuple[str, list[str], float]:
    with duckdb.connect(str(warehouse)) as con:
        job, ids, total = con.execute(
            "select job_id, list(finding_id), sum(amount) from gold.findings group by 1 "
            "having count(*) >= 1 order by job_id limit 1"
        ).fetchone()
    return job, list(ids), float(total)


def test_draft_amounts_come_from_the_warehouse(world: dict[str, Any], app_db: Path) -> None:
    tools = Tools(world["warehouse"], app_db)
    job, ids, total = _job_with_findings(world["warehouse"])
    evidence = tools.get_job_evidence(job)
    assert {f["finding_id"] for f in evidence["findings"]} == set(ids)
    draft = tools.draft_invoice(job, ids)
    assert draft["total"] == pytest.approx(round(total, 2))
    assert draft["status"] == "pending_approval"
    assert tools.notify_owner(draft["draft_id"], "Draft ready")["sent"]


def test_drafts_reject_foreign_and_repeated_findings(world: dict[str, Any], app_db: Path) -> None:
    tools = Tools(world["warehouse"], app_db)
    job, ids, _ = _job_with_findings(world["warehouse"])
    assert "error" in tools.draft_invoice(job, ["made-up-id"])
    assert "error" in tools.draft_invoice(job, [])
    tools.draft_invoice(job, ids)
    assert "error" in tools.draft_invoice(job, ids)  # already drafted
    business = tools.get_job_evidence(job)["business_id"]
    assert all(f["job_id"] != job for f in tools.list_open_findings(business))


def test_approval_queues_the_draft_and_is_audited(world: dict[str, Any], app_db: Path) -> None:
    from fastapi.testclient import TestClient

    from leakcheck.agent.approval import create_app

    tools = Tools(world["warehouse"], app_db, actor="owner")
    job, ids, _ = _job_with_findings(world["warehouse"])
    draft = tools.draft_invoice(job, ids)
    client = TestClient(create_app(tools, owner="maria"))
    assert draft["draft_id"] in client.get("/").text or job in client.get("/").text
    client.post(f"/drafts/{draft['draft_id']}", data={"decision": "approve"})
    with duckdb.connect(str(app_db)) as con:
        status = con.execute(
            "select status, decided_by from drafts where draft_id = ?", [draft["draft_id"]]
        ).fetchone()
        outbox = con.execute("select count(*) from outbox").fetchone()[0]
        audit = con.execute("select action from audit where actor = 'maria'").fetchall()
    assert status == ("approved", "maria")
    assert outbox == 1
    assert ("draft_approved",) in audit


def test_mcp_client_sees_and_calls_the_tools(world: dict[str, Any], app_db: Path) -> None:
    from mcp.client.client import Client

    from leakcheck.agent.mcp_server import build

    job, ids, _ = _job_with_findings(world["warehouse"])
    server = build(world["warehouse"], app_db)

    async def run() -> tuple[set[str], dict[str, Any]]:
        async with Client(server) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            result = await client.call_tool("get_job_evidence", {"job_id": job})
            return names, json.loads(result.content[0].text)

    names, evidence = asyncio.run(run())
    assert names == {"list_open_findings", "get_job_evidence", "draft_invoice", "notify_owner"}
    assert {f["finding_id"] for f in evidence["findings"]} == set(ids)


def test_agent_loop_with_a_scripted_model(world: dict[str, Any], app_db: Path) -> None:
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    from leakcheck.agent.agent import run_job

    class Scripted(GenericFakeChatModel):
        def bind_tools(self, tools: Any, **kwargs: Any) -> Scripted:  # type: ignore[override]
            return self

    tools = Tools(world["warehouse"], app_db)
    job, ids, _ = _job_with_findings(world["warehouse"])
    # The draft id is not known in advance, so the script drafts and stops before notifying.
    script = iter(
        [
            AIMessage(
                content="",
                tool_calls=[{"name": "get_job_evidence", "args": {"job_id": job}, "id": "1"}],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "draft_invoice",
                        "args": {"job_id": job, "finding_ids": ids},
                        "id": "2",
                    }
                ],
            ),
            AIMessage(content="Drafted the invoice for the owner to approve."),
        ]
    )
    run = run_job(job, tools, Scripted(messages=script))
    assert [c["name"] for c in run["calls"]] == ["get_job_evidence", "draft_invoice"]
    assert any('"pending_approval"' in r for r in run["tool_results"])
