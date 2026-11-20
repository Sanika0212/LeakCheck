"""The four tools the invoicing agent (and the MCP server) can call.

1. ``list_open_findings(business_id)``: findings the warehouse has not resolved.
2. ``get_job_evidence(job_id)``: quote, hours, materials, messages and invoices for a job.
3. ``draft_invoice(job_id, finding_ids)``: a draft for the owner to approve, which also
   notifies the owner. A step that must always happen is not left to the model: in a run of
   100 held-out jobs the agent passed ``notify_owner`` a draft id it had made up in 35% of
   cases, so the notification now happens where the draft is created.
4. ``notify_owner(draft_id, note)``: an extra note about a draft that already exists.

Every amount in a draft comes from the warehouse: ``draft_invoice`` takes finding ids, not
dollars, so the model cannot write an amount even by mistake. Drafts, notifications and an
audit row for every call live in a small app database next to the warehouse, which the
tools open read-only.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from pathlib import Path
from typing import Any

import duckdb

APP_SCHEMA = """
create table if not exists drafts (
  draft_id varchar primary key, business_id varchar, job_id varchar, kind varchar,
  total double, status varchar, created_at timestamp, decided_at timestamp, decided_by varchar);
create table if not exists draft_lines (
  draft_id varchar, finding_id varchar, finding_type varchar, description varchar, amount double);
create table if not exists notifications (
  notification_id varchar, draft_id varchar, business_id varchar, note varchar, created_at timestamp);
create table if not exists audit (
  logged_at timestamp, actor varchar, action varchar, arguments varchar, outcome varchar);
create table if not exists outbox (
  draft_id varchar, job_id varchar, payload varchar, created_at timestamp);
"""


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


class Tools:
    def __init__(
        self,
        warehouse: Path = Path("data/warehouse.duckdb"),
        app: Path = Path("data/app.duckdb"),
        actor: str = "agent",
    ) -> None:
        self.warehouse = warehouse
        self.app = app
        self.actor = actor
        app.parent.mkdir(parents=True, exist_ok=True)
        with duckdb.connect(str(app)) as con:
            con.execute(APP_SCHEMA)

    # ------------------------------------------------------------------ helpers

    def _wh(self) -> duckdb.DuckDBPyConnection:
        try:
            return duckdb.connect(str(self.warehouse), read_only=True)
        except duckdb.ConnectionException:
            # This process already has the warehouse open for writing (right after a build).
            return duckdb.connect(str(self.warehouse))

    def _audit(self, action: str, arguments: dict[str, Any], outcome: str) -> None:
        with duckdb.connect(str(self.app)) as con:
            con.execute(
                "insert into audit values (?, ?, ?, ?, ?)",
                [_now(), self.actor, action, json.dumps(arguments), outcome],
            )

    def _drafted(self) -> set[str]:
        with duckdb.connect(str(self.app)) as con:
            rows = con.execute(
                "select l.finding_id from draft_lines l join drafts d using (draft_id) "
                "where d.status <> 'rejected'"
            ).fetchall()
        return {r[0] for r in rows}

    # -------------------------------------------------------------------- tools

    def list_open_findings(self, business_id: str) -> list[dict[str, Any]]:
        """Open revenue-leak findings for a business that no draft covers yet."""
        with self._wh() as con:
            rows = con.execute(
                "select finding_id, job_id, finding_type, amount, evidence from gold.findings "
                "where business_id = ? order by amount desc",
                [business_id],
            ).fetchall()
        drafted = self._drafted()
        out = [
            {"finding_id": f, "job_id": j, "finding_type": t, "amount": a, "evidence": e}
            for f, j, t, a, e in rows
            if f not in drafted
        ]
        self._audit("list_open_findings", {"business_id": business_id}, f"{len(out)} findings")
        return out

    def get_job_evidence(self, job_id: str) -> dict[str, Any]:
        """What the records say about a job: money, work, messages and its open findings."""
        with self._wh() as con:
            fin = con.execute(
                "select business_id, pricing, job_type, quote_total, est_hours, logged_hours, "
                "invoiced_hours, materials_cost, invoiced_materials, invoiced_total, "
                "has_final_invoice, change_orders from gold.job_financials where job_id = ?",
                [job_id],
            ).fetchone()
            if fin is None:
                self._audit("get_job_evidence", {"job_id": job_id}, "unknown job")
                return {"error": f"no job {job_id}"}
            names = [
                "business_id",
                "pricing",
                "job_type",
                "quote_total",
                "est_hours",
                "logged_hours",
                "invoiced_hours",
                "materials_cost",
                "invoiced_materials",
                "invoiced_total",
                "has_final_invoice",
                "change_orders",
            ]
            evidence: dict[str, Any] = dict(zip(names, fin, strict=True))
            evidence["messages"] = [
                {"direction": d, "body": b}
                for d, b in con.execute(
                    "select direction, body from silver.stg_messages where job_id = ? order by sent_at",
                    [job_id],
                ).fetchall()
            ]
            evidence["crew_notes"] = [
                n
                for (n,) in con.execute(
                    "select note from silver.stg_visits where job_id = ? and coalesce(note, '') <> ''",
                    [job_id],
                ).fetchall()
            ]
            evidence["findings"] = [
                {"finding_id": f, "finding_type": t, "amount": a, "evidence": e}
                for f, t, a, e in con.execute(
                    "select finding_id, finding_type, amount, evidence from gold.findings where job_id = ?",
                    [job_id],
                ).fetchall()
            ]
        evidence["job_id"] = job_id
        self._audit("get_job_evidence", {"job_id": job_id}, f"{len(evidence['findings'])} findings")
        return evidence

    def draft_invoice(self, job_id: str, finding_ids: list[str]) -> dict[str, Any]:
        """Draft an invoice (or a change order, for extra work) covering these findings.

        Amounts are the warehouse's; the caller only chooses which findings to include.
        """
        with self._wh() as con:
            rows = con.execute(
                "select finding_id, business_id, finding_type, amount, evidence from gold.findings "
                "where job_id = ?",
                [job_id],
            ).fetchall()
        by_id = {r[0]: r for r in rows}
        unknown = [f for f in finding_ids if f not in by_id]
        if unknown or not finding_ids:
            self._audit(
                "draft_invoice",
                {"job_id": job_id, "finding_ids": finding_ids},
                f"rejected: unknown findings {unknown}" if unknown else "rejected: no findings",
            )
            return {
                "error": "every finding id must be an open finding of this job",
                "unknown": unknown,
            }
        already = self._drafted() & set(finding_ids)
        if already:
            self._audit(
                "draft_invoice",
                {"job_id": job_id, "finding_ids": finding_ids},
                "rejected: already drafted",
            )
            return {"error": "already drafted", "finding_ids": sorted(already)}
        lines = [by_id[f] for f in finding_ids]
        kind = "change_order" if all(r[2] == "unbilled_extra_work" for r in lines) else "invoice"
        draft_id = f"draft_{uuid.uuid4().hex[:10]}"
        total = round(sum(r[3] for r in lines), 2)
        notification_id = f"note_{uuid.uuid4().hex[:10]}"
        note = f"{kind.replace('_', ' ')} for job {job_id}: ${total:,.2f} waiting for approval"
        with duckdb.connect(str(self.app)) as con:
            con.execute(
                "insert into drafts values (?, ?, ?, ?, ?, 'pending_approval', ?, null, null)",
                [draft_id, lines[0][1], job_id, kind, total, _now()],
            )
            con.executemany(
                "insert into draft_lines values (?, ?, ?, ?, ?)",
                [(draft_id, r[0], r[2], r[4], r[3]) for r in lines],
            )
            # The owner is told here, not by a later tool call the model has to remember.
            con.execute(
                "insert into notifications values (?, ?, ?, ?, ?)",
                [notification_id, draft_id, lines[0][1], note, _now()],
            )
        self._audit(
            "draft_invoice",
            {"job_id": job_id, "finding_ids": finding_ids},
            f"{draft_id} {kind} ${total}",
        )
        return {
            "draft_id": draft_id,
            "kind": kind,
            "job_id": job_id,
            "total": total,
            "lines": [{"finding_type": r[2], "description": r[4], "amount": r[3]} for r in lines],
            "status": "pending_approval",
            "owner_notified": True,
            "notification_id": notification_id,
        }

    def notify_owner(self, draft_id: str, note: str) -> dict[str, Any]:
        """Send the owner an extra note about a draft that already exists.

        Creating a draft already notifies the owner, so this is for anything beyond that.
        """
        with duckdb.connect(str(self.app)) as con:
            row = con.execute(
                "select business_id from drafts where draft_id = ?", [draft_id]
            ).fetchone()
            if row is None:
                self._audit("notify_owner", {"draft_id": draft_id}, "unknown draft")
                return {"error": f"no draft {draft_id}"}
            nid = f"note_{uuid.uuid4().hex[:10]}"
            con.execute(
                "insert into notifications values (?, ?, ?, ?, ?)",
                [nid, draft_id, row[0], note[:500], _now()],
            )
        self._audit("notify_owner", {"draft_id": draft_id}, nid)
        return {"notification_id": nid, "draft_id": draft_id, "sent": True}

    # ------------------------------------------------------------- approval

    def decide(self, draft_id: str, approve: bool, who: str) -> dict[str, Any]:
        """Owner approves (queued to send) or rejects a draft; both are audited."""
        status = "approved" if approve else "rejected"
        with duckdb.connect(str(self.app)) as con:
            row = con.execute(
                "select job_id, status from drafts where draft_id = ?", [draft_id]
            ).fetchone()
            if row is None or row[1] != "pending_approval":
                return {"error": "not a pending draft"}
            con.execute(
                "update drafts set status = ?, decided_at = ?, decided_by = ? where draft_id = ?",
                [status, _now(), who, draft_id],
            )
            if approve:
                lines = con.execute(
                    "select finding_type, description, amount from draft_lines where draft_id = ?",
                    [draft_id],
                ).fetchall()
                con.execute(
                    "insert into outbox values (?, ?, ?, ?)",
                    [draft_id, row[0], json.dumps([list(x) for x in lines]), _now()],
                )
            con.execute(
                "insert into audit values (?, ?, ?, ?, ?)",
                [_now(), who, f"draft_{status}", json.dumps({"draft_id": draft_id}), status],
            )
        return {"draft_id": draft_id, "status": status}
