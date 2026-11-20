"""The same four tools over the Model Context Protocol.

    leakcheck mcp              # stdio, for an MCP client such as a desktop assistant

Any MCP client can then list findings, read a job's evidence, draft for approval and notify
the owner, with the same guarantees as the built-in agent: amounts come from the
warehouse, and nothing is sent without the owner's approval.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from leakcheck.agent.tools import Tools


def build(
    warehouse: Path = Path("data/warehouse.duckdb"), app: Path = Path("data/app.duckdb")
) -> MCPServer:
    tools = Tools(warehouse, app, actor="mcp")
    server = MCPServer(
        "leakcheck", instructions="Find unbilled work and draft invoices for owner approval."
    )

    @server.tool()
    def list_open_findings(business_id: str) -> list[dict[str, Any]]:
        """Open revenue-leak findings for a business that no draft covers yet."""
        return tools.list_open_findings(business_id)

    @server.tool()
    def get_job_evidence(job_id: str) -> dict[str, Any]:
        """Quote, hours, materials, messages, crew notes and findings for a job."""
        return tools.get_job_evidence(job_id)

    @server.tool()
    def draft_invoice(job_id: str, finding_ids: list[str]) -> dict[str, Any]:
        """Draft an invoice or change order for these findings; amounts come from the warehouse."""
        return tools.draft_invoice(job_id, finding_ids)

    @server.tool()
    def notify_owner(draft_id: str, note: str) -> dict[str, Any]:
        """Tell the owner a draft is waiting for approval."""
        return tools.notify_owner(draft_id, note)

    return server


def main() -> None:
    build().run()
