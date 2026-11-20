"""Azure Functions app (Python v2 model): the invoicing agent on Azure OpenAI.

1. A daily timer drafts invoices for every open finding, one job at a time.
2. ``POST /api/jobs/{job_id}/review`` runs the agent on one job on demand.

Drafts wait for the owner on the approval page; nothing is sent from here.
"""

from __future__ import annotations

import json
import os

import azure.functions as func

from leakcheck.agent.agent import chat_model, run_job
from leakcheck.agent.tools import Tools

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


def _tools() -> Tools:
    from pathlib import Path

    return Tools(Path(os.environ.get("LC_WAREHOUSE", "/home/data/warehouse.duckdb")),
                 Path(os.environ.get("LC_APP_DB", "/home/data/app.duckdb")))


@app.timer_trigger(schedule="0 0 7 * * *", arg_name="timer", run_on_startup=False)
def daily_drafts(timer: func.TimerRequest) -> None:
    tools, model = _tools(), chat_model()
    for business in os.environ.get("LC_BUSINESSES", "").split(","):
        jobs = {f["job_id"] for f in tools.list_open_findings(business.strip())} if business else set()
        for job in sorted(jobs):
            run_job(job, tools, model)


@app.route(route="jobs/{job_id}/review", methods=["POST"])
def review_job(req: func.HttpRequest) -> func.HttpResponse:
    result = run_job(req.route_params["job_id"], _tools(), chat_model())
    return func.HttpResponse(json.dumps(result, default=str), mimetype="application/json")
