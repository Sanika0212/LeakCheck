"""A LangChain tool-calling agent that turns findings into drafts for the owner to approve.

The model (local qwen2.5:7b through Ollama by default) gets one job at a time. It must look
at the evidence before drafting, include the job's open findings, ask for the owner's
approval and never state an amount a tool did not return. It cannot send anything: drafts
wait on the approval page.
"""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool

from leakcheck.agent.tools import Tools

SYSTEM = """You help the owner of a small painting business bill for work that was done but not invoiced.
For the job you are given:
1. Call get_job_evidence first and read it.
2. If the job has open findings, call draft_invoice once with the ids of all of them.
3. Then call notify_owner with the draft id and one short sentence saying what the draft covers.
4. If the job has no findings, do not draft anything; say there is nothing to bill.
Never state a dollar amount that a tool did not return. You cannot send invoices; the owner approves them."""


def langchain_tools(tools: Tools) -> list[StructuredTool]:
    return [
        StructuredTool.from_function(tools.get_job_evidence),
        StructuredTool.from_function(tools.draft_invoice),
        StructuredTool.from_function(tools.notify_owner),
        StructuredTool.from_function(tools.list_open_findings),
    ]


def ollama_model(model: str = "qwen2.5:7b") -> BaseChatModel:
    from langchain_ollama import ChatOllama

    return ChatOllama(model=model, temperature=0, num_predict=400)


def run_job(
    job_id: str, tools: Tools, model: BaseChatModel, recursion_limit: int = 12
) -> dict[str, Any]:
    """Run the agent on one job; returns the tool calls it made and its final answer."""
    agent = create_agent(model=model, tools=langchain_tools(tools), system_prompt=SYSTEM)
    request: Any = {
        "messages": [("user", f"Review job {job_id} and prepare anything the owner should bill.")]
    }
    out = agent.invoke(request, config={"recursion_limit": recursion_limit})
    calls: list[dict[str, Any]] = []
    results: list[str] = []
    for m in out["messages"]:
        if isinstance(m, AIMessage):
            calls += [{"name": c["name"], "args": c["args"]} for c in m.tool_calls]
        elif isinstance(m, ToolMessage):
            results.append(str(m.content))
    final = out["messages"][-1]
    return {
        "job_id": job_id,
        "calls": calls,
        "tool_results": results,
        "answer": final.content if isinstance(final, AIMessage) else "",
    }
