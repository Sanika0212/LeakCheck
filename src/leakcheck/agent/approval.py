"""The owner's approval page: every draft waits here, with its evidence, until approved.

    leakcheck approve          # http://localhost:8070

Approving queues the draft in the outbox for the field-service system; rejecting closes
it. Both are written to the audit table with who decided and when.
"""

from __future__ import annotations

import html
from pathlib import Path

import duckdb
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from leakcheck.agent.tools import Tools

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>LeakCheck: drafts to approve</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 860px; margin: 2rem auto; color: #1f2328; }}
.card {{ border: 1px solid #d0d7de; border-radius: 8px; padding: 1rem 1.25rem; margin: 1rem 0; }}
table {{ width: 100%; border-collapse: collapse; }} td {{ padding: .3rem 0; vertical-align: top; }}
td.amt {{ text-align: right; white-space: nowrap; }}
button {{ padding: .4rem .9rem; border-radius: 6px; border: 1px solid #8c959f; cursor: pointer; }}
button.ok {{ background: #1a7f37; color: white; border-color: #1a7f37; }}
.muted {{ color: #656d76; }}
</style></head><body>
<h1>Drafts waiting for you</h1>
<p class="muted">Each draft was prepared from your own records. Nothing is sent until you approve it.</p>
{cards}
</body></html>"""


def create_app(tools: Tools | None = None, owner: str = "owner") -> FastAPI:
    t = tools or Tools(actor=owner)
    app = FastAPI(title="LeakCheck approvals")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        with duckdb.connect(str(t.app)) as con:
            drafts = con.execute(
                "select draft_id, job_id, kind, total from drafts where status = 'pending_approval' "
                "order by created_at"
            ).fetchall()
            cards = []
            for draft_id, job_id, kind, total in drafts:
                lines = con.execute(
                    "select finding_type, description, amount from draft_lines where draft_id = ?",
                    [draft_id],
                ).fetchall()
                rows = "".join(
                    f"<tr><td>{html.escape(ft.replace('_', ' '))}<br><span class='muted'>"
                    f"{html.escape(desc)}</span></td><td class='amt'>${amt:,.2f}</td></tr>"
                    for ft, desc, amt in lines
                )
                cards.append(
                    f"<div class='card'><b>{html.escape(kind.replace('_', ' ').title())}</b> for job "
                    f"{html.escape(job_id)}<table>{rows}<tr><td><b>Total</b></td>"
                    f"<td class='amt'><b>${total:,.2f}</b></td></tr></table>"
                    f"<form method='post' action='/drafts/{draft_id}' style='margin-top:.6rem'>"
                    "<button class='ok' name='decision' value='approve'>Approve</button> "
                    "<button name='decision' value='reject'>Reject</button></form></div>"
                )
        return PAGE.format(cards="".join(cards) or "<p>No drafts waiting.</p>")

    @app.post("/drafts/{draft_id}")
    def decide(draft_id: str, decision: str = Form(...)) -> RedirectResponse:
        t.decide(draft_id, decision == "approve", owner)
        return RedirectResponse("/", status_code=303)

    return app


def default_app() -> FastAPI:
    return create_app(Tools(Path("data/warehouse.duckdb"), Path("data/app.duckdb"), actor="owner"))
