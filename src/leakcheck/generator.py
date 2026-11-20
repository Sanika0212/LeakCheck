"""Synthetic painting contractors with revenue leakage planted at known places.

Six businesses over six months (April to September 2025). Each business gets its own crew
size, billing rate (the BLS painter wage times a burden and a margin), materials markup and
mix of fixed-price and time-and-materials jobs. Job hours follow the paintable area, the
surface condition, stories and rain days (Open-Meteo's wet-day share for the month);
materials follow the BLS paint price index for the month the job runs.

Leakage is planted with dollar amounts recorded in a ground-truth table that never enters
the pipeline:

1. uninvoiced_job: a completed job is never invoiced.
2. unbilled_hours: a time-and-materials invoice leaves out one or more visits.
3. unbilled_materials: a time-and-materials invoice leaves out a materials purchase.
4. unbilled_extra_work: the customer asks for extra work in a message, the crew does it,
   and no change order or invoice line covers it.
5. unapproved_discount: a fixed-price invoice comes in under the approved quote without a
   discount line or change order.

The data also contain the things that make detection hard: extra work that was declined,
or billed through a change order; price questions that are not requests; explicit
discounts; deposits; invoices that simply arrive a few days late; no-charge callback visits
(the crew fixing its own work); shop-stock purchases booked against a job; and goodwill
credits the owner promised in a message instead of adding a discount line.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from leakcheck import calibration
from leakcheck.messages import (
    EXTRA_ITEMS,
    MessageWriter,
)

START = dt.date(2025, 4, 1)
END = dt.date(2025, 9, 30)
BURDEN = 1.32  # payroll taxes, insurance and benefits on top of the wage
JOB_TYPES = ["interior", "exterior", "cabinets", "deck", "commercial"]
NAMES = ["Lakeshore", "Elmwood", "Hertel", "Niagara", "Kenmore", "Delaware"]
CONDITIONS = {"good": 0.9, "fair": 1.0, "poor": 1.35}


@dataclass
class Business:
    business_id: str
    name: str
    split: str  # "dev" (detectors may be tuned on it) or "test" (held out)
    crew_size: int
    billing_rate: float
    materials_markup: float
    tm_share: float  # share of jobs billed time and materials
    jobs_per_month: float
    estimator_bias: dict[str, float]  # how far this owner's quotes run from true hours, by type
    webhook_secret: str


@dataclass
class World:
    """Everything a business system would hold, plus the ground truth."""

    businesses: list[Business] = field(default_factory=list)
    customers: list[dict[str, Any]] = field(default_factory=list)
    quotes: list[dict[str, Any]] = field(default_factory=list)
    jobs: list[dict[str, Any]] = field(default_factory=list)
    visits: list[dict[str, Any]] = field(default_factory=list)
    expenses: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    change_orders: list[dict[str, Any]] = field(default_factory=list)
    invoices: list[dict[str, Any]] = field(default_factory=list)
    truth: list[dict[str, Any]] = field(default_factory=list)
    message_truth: list[dict[str, Any]] = field(default_factory=list)


def _ts(day: dt.date, hour: float) -> str:
    h = min(max(hour, 0.0), 23.98)
    return dt.datetime(day.year, day.month, day.day, int(h), int((h % 1) * 60)).isoformat()


class Generator:
    def __init__(self, seed: int = 7, calibration_data: dict[str, Any] | None = None) -> None:
        self.rng = np.random.default_rng(seed)
        self.cal = calibration_data or calibration.load()
        self.wage = float(self.cal["painter_mean_hourly_wage"]["value"])
        ppi = self.cal["paint_ppi"]["values"]
        self.ppi = {k: float(v) for k, v in ppi.items()}
        self.ppi_base = self.ppi["2025-01"]
        self.wet = {int(k): float(v) for k, v in self.cal["wet_day_share"]["values"].items()}
        self.writer = MessageWriter(self.rng)
        self.world = World()
        self._ids: dict[str, int] = {}

    # ------------------------------------------------------------------ helpers

    def _id(self, prefix: str) -> str:
        self._ids[prefix] = self._ids.get(prefix, 0) + 1
        return f"{prefix}_{self._ids[prefix]:06d}"

    def _paint_price(self, day: dt.date) -> float:
        """Contractor price of a gallon of paint, moving with the producer price index."""
        index = self.ppi.get(f"{day.year}-{day.month:02d}", self.ppi_base)
        return 42.0 * index / self.ppi_base

    def _business(self, i: int, split: str) -> Business:
        rng = self.rng
        margin = rng.uniform(1.65, 2.25)
        bias = {t: float(rng.normal(-0.06, 0.08)) for t in JOB_TYPES}
        bias["exterior"] -= 0.08  # owners often under-quote exterior work
        return Business(
            business_id=f"biz_{i:02d}",
            name=f"{NAMES[i - 1]} Painting",
            split=split,
            crew_size=int(rng.integers(2, 6)),
            billing_rate=round(self.wage * BURDEN * margin, 2),
            materials_markup=round(float(rng.uniform(0.15, 0.30)), 2),
            tm_share=round(float(rng.uniform(0.2, 0.5)), 2),
            jobs_per_month=float(rng.uniform(22, 38)),
            estimator_bias=bias,
            webhook_secret=f"whsec_{i:02d}_{int(rng.integers(10**9, 10**10))}",
        )

    # -------------------------------------------------------------- job physics

    def _job_spec(self, job_type: str) -> dict[str, Any]:
        rng = self.rng
        condition = str(rng.choice(list(CONDITIONS), p=[0.45, 0.4, 0.15]))
        spec: dict[str, Any] = {"job_type": job_type, "condition": condition, "stories": 1}
        if job_type == "interior":
            rooms = int(rng.integers(1, 7))
            spec |= {"rooms": rooms, "area_sqft": float(rooms * rng.uniform(350, 520))}
        elif job_type == "exterior":
            spec |= {
                "stories": int(rng.choice([1, 2, 3], p=[0.35, 0.55, 0.10])),
                "area_sqft": float(rng.uniform(1400, 3200) * rng.uniform(1.1, 1.5)),
            }
        elif job_type == "cabinets":
            spec |= {"doors": int(rng.integers(12, 41))}
            spec["area_sqft"] = spec["doors"] * 9.0
        elif job_type == "deck":
            spec |= {"area_sqft": float(rng.uniform(150, 600))}
        else:
            spec |= {"area_sqft": float(rng.uniform(2000, 12000))}
        return spec

    def _true_hours(self, spec: dict[str, Any], rain_days: int) -> float:
        t, area = spec["job_type"], spec["area_sqft"]
        cond = CONDITIONS[spec["condition"]]
        if t == "interior":
            base = 4 + 1.1 * area / 100 * cond
        elif t == "exterior":
            base = (6 + 0.9 * area / 100 * cond) * (1 + 0.25 * (spec["stories"] - 1))
            base += 1.5 * rain_days  # packing up and coming back
        elif t == "cabinets":
            base = 6 + 1.4 * spec["doors"] * (1.1 if spec["condition"] == "poor" else 1.0)
        elif t == "deck":
            base = 4 + 0.6 * area / 100 * cond
        else:
            base = 8 + 0.8 * area / 100 * cond
        return float(base * self.rng.lognormal(0, 0.18))

    def _quoted_hours(self, spec: dict[str, Any], biz: Business) -> float:
        """The owner's estimate: right structure, but blind to condition and stories."""
        t, area = spec["job_type"], spec["area_sqft"]
        if t == "interior":
            base = 4 + 1.1 * area / 100
        elif t == "exterior":
            base = 6 + 0.9 * area / 100 * (1 + 0.15 * (spec["stories"] - 1))
        elif t == "cabinets":
            base = 6 + 1.4 * spec["doors"]
        elif t == "deck":
            base = 4 + 0.6 * area / 100
        else:
            base = 8 + 0.8 * area / 100
        return float(base * (1 + biz.estimator_bias[t]) * self.rng.lognormal(0, 0.12))

    def _materials(self, spec: dict[str, Any], day: dt.date) -> float:
        gallons = max(1.0, spec["area_sqft"] / 350 * 2)
        return float(gallons * self._paint_price(day) * self.rng.uniform(1.10, 1.25))

    # ------------------------------------------------------------------- build

    def build(self) -> World:
        splits = ["dev", "dev", "dev", "test", "test", "test"]
        for i, split in enumerate(splits, start=1):
            biz = self._business(i, split)
            self.world.businesses.append(biz)
            self._simulate(biz)
        return self.world

    def _simulate(self, biz: Business) -> None:
        rng = self.rng
        self.writer.style = "B" if biz.split == "test" else "A"
        day = START
        while day <= END:
            month_rate = biz.jobs_per_month * (1.2 if day.month in (5, 6, 7, 8) else 0.85)
            for _ in range(int(rng.poisson(month_rate / 30))):
                self._job(biz, day)
            day += dt.timedelta(days=1)

    def _job(self, biz: Business, request_day: dt.date) -> None:
        rng, w = self.rng, self.world
        job_type = str(rng.choice(JOB_TYPES, p=[0.42, 0.28, 0.10, 0.10, 0.10]))
        spec = self._job_spec(job_type)
        pricing = "time_materials" if rng.random() < biz.tm_share else "fixed"
        quote_day = request_day + dt.timedelta(days=int(rng.integers(1, 5)))
        start = quote_day + dt.timedelta(days=int(rng.integers(3, 21)))
        if start > END:
            return
        customer = self.writer.customer(biz.business_id)
        customer["customer_id"] = self._id("cust")
        w.customers.append(customer)
        q_hours = self._quoted_hours(spec, biz)
        q_materials = self._materials(spec, start)
        quote_total = round(
            q_hours * biz.billing_rate + q_materials * (1 + biz.materials_markup), 2
        )
        quote_id, job_id = self._id("quote"), self._id("job")
        w.quotes.append(
            {
                "quote_id": quote_id,
                "business_id": biz.business_id,
                "customer_id": customer["customer_id"],
                "job_id": job_id,
                "created_at": _ts(quote_day, 10),
                "approved_at": _ts(quote_day + dt.timedelta(days=int(rng.integers(0, 3))), 16),
                "pricing": pricing,
                "est_hours": round(q_hours, 1),
                "rate": biz.billing_rate,
                "est_materials": round(q_materials, 2),
                "markup": biz.materials_markup,
                "total": quote_total,
                **{k: v for k, v in spec.items()},
            }
        )
        w.messages.append(
            self._message(
                biz, customer, job_id, request_day, self.writer.request(spec), "inbound", "request"
            )
        )

        # Extra work the customer asks for while the job is under way.
        extra: dict[str, Any] | None = None
        if rng.random() < 0.20:
            item = str(rng.choice(list(EXTRA_ITEMS)))
            extra = {"item": item, "hours": float(rng.uniform(*EXTRA_ITEMS[item]))}
            extra["outcome"] = str(
                rng.choice(["declined", "change_order", "unbilled"], p=[0.2, 0.5, 0.3])
            )

        rain_days = 0
        if job_type in ("exterior", "deck"):
            rain_days = int(rng.binomial(4, self.wet[start.month]))
        hours = self._true_hours(spec, rain_days)
        total_hours = hours + (extra["hours"] if extra and extra["outcome"] != "declined" else 0.0)

        # Visits: the crew works full days until the hours are done.
        visits: list[dict[str, Any]] = []
        remaining, day = total_hours, start
        while remaining > 0.05:
            crew = int(min(biz.crew_size, max(1, math.ceil(remaining / 8))))
            for _ in range(crew):
                h = float(min(remaining, rng.uniform(6.0, 8.5)))
                if h < 0.5 and visits:  # a few leftover minutes go on the last timesheet
                    visits[-1]["hours"] = round(float(visits[-1]["hours"]) + h, 2)
                    remaining -= h
                    break
                if h <= 0.05:
                    break
                visits.append(
                    {
                        "visit_id": self._id("visit"),
                        "job_id": job_id,
                        "business_id": biz.business_id,
                        "date": day.isoformat(),
                        "hours": round(h, 2),
                        "note": "",
                    }
                )
                remaining -= h
            day += dt.timedelta(days=1 + (1 if rng.random() < 0.15 else 0))
        done = dt.date.fromisoformat(str(visits[-1]["date"]))
        visits[-1]["note"] = self.writer.closing_note()

        # Materials bought for the job.
        expenses = []
        materials = self._materials(spec, start)
        for share in self._split(materials, int(rng.integers(1, 4))):
            expenses.append(
                {
                    "expense_id": self._id("exp"),
                    "job_id": job_id,
                    "business_id": biz.business_id,
                    "date": (start + dt.timedelta(days=int(rng.integers(0, 2)))).isoformat(),
                    "vendor": str(
                        rng.choice(["Sherwin-Williams", "Benjamin Moore", "Home Depot", "PPG"])
                    ),
                    "amount": round(share, 2),
                    "description": "paint and supplies",
                }
            )
        extra_cost = 0.0
        if extra and extra["outcome"] != "declined":
            extra_cost = round(float(extra["hours"] * rng.uniform(8, 18)), 2)
            expenses.append(
                {
                    "expense_id": self._id("exp"),
                    "job_id": job_id,
                    "business_id": biz.business_id,
                    "date": done.isoformat(),
                    "vendor": "Home Depot",
                    "amount": extra_cost,
                    "description": f"supplies, {extra['item']}",
                }
            )

        # Messages during the job: noise, and the extra-work thread.
        mid = start + (done - start) / 2
        for _ in range(int(rng.poisson(1.2))):
            kind = str(
                rng.choice(
                    ["scheduling", "question", "thanks", "price_inquiry", "cancel_extra"],
                    p=[0.35, 0.3, 0.15, 0.12, 0.08],
                )
            )
            w.messages.append(
                self._message(biz, customer, job_id, mid, self.writer.noise(kind), "inbound", kind)
            )
        if extra:
            ask_day = start + dt.timedelta(
                days=int(rng.integers(0, max(1, (done - start).days + 1)))
            )
            w.messages.append(
                self._message(
                    biz,
                    customer,
                    job_id,
                    ask_day,
                    self.writer.extra_request(extra["item"]),
                    "inbound",
                    "extra_request",
                )
            )
            if extra["outcome"] == "declined":
                w.messages.append(
                    self._message(
                        biz,
                        customer,
                        job_id,
                        ask_day,
                        self.writer.decline(extra["item"]),
                        "outbound",
                        "decline",
                    )
                )
            else:
                w.messages.append(
                    self._message(
                        biz,
                        customer,
                        job_id,
                        ask_day,
                        self.writer.accept(extra["item"]),
                        "outbound",
                        "accept",
                    )
                )
                if rng.random() < 0.8:  # the crew usually writes it down
                    visits[-1]["note"] = self.writer.extra_done_note(extra["item"])
        # Confounders: work and purchases that are legitimately not billed.
        if rng.random() < 0.08:
            visits.append(
                {
                    "visit_id": self._id("visit"),
                    "job_id": job_id,
                    "business_id": biz.business_id,
                    "date": (done + dt.timedelta(days=int(rng.integers(3, 12)))).isoformat(),
                    "hours": round(float(rng.uniform(1.0, 3.0)), 2),
                    "note": self.writer.callback_note(),
                    "no_charge": True,
                }
            )
        if rng.random() < 0.05:
            expenses.append(
                {
                    "expense_id": self._id("exp"),
                    "job_id": job_id,
                    "business_id": biz.business_id,
                    "date": start.isoformat(),
                    "vendor": "Home Depot",
                    "amount": round(float(rng.uniform(30, 120)), 2),
                    "description": "shop stock restock (caulk, tape, rollers)",
                    "shop_stock": True,
                }
            )
        w.visits.extend(visits)
        w.expenses.extend(expenses)

        job = {
            "job_id": job_id,
            "business_id": biz.business_id,
            "customer_id": customer["customer_id"],
            "quote_id": quote_id,
            "pricing": pricing,
            "start_date": start.isoformat(),
            "completed_at": _ts(done, 17),
            "status": "completed",
            **spec,
            "rain_days": rain_days,
            "true_hours": round(hours, 2),
        }
        w.jobs.append(job)

        extra_value = 0.0
        if extra and extra["outcome"] != "declined":
            extra_value = round(
                extra["hours"] * biz.billing_rate + extra_cost * (1 + biz.materials_markup), 2
            )
            if extra["outcome"] == "change_order":
                w.change_orders.append(
                    {
                        "change_order_id": self._id("co"),
                        "job_id": job_id,
                        "business_id": biz.business_id,
                        "created_at": _ts(ask_day, 18),
                        "description": extra["item"],
                        "amount": extra_value,
                        "approved": True,
                    }
                )
        self._invoice(biz, job, quote_total, visits, expenses, extra, extra_value, done)

    def _invoice(
        self,
        biz: Business,
        job: dict[str, Any],
        quote_total: float,
        visits: list[dict[str, Any]],
        expenses: list[dict[str, Any]],
        extra: dict[str, Any] | None,
        extra_value: float,
        done: dt.date,
    ) -> None:
        rng, w = self.rng, self.world
        job_id = job["job_id"]
        rate, mk = biz.billing_rate, biz.materials_markup
        leaks: list[dict[str, Any]] = []
        lines: list[dict[str, Any]] = []
        if job["pricing"] == "fixed":
            lines.append(
                {"kind": "fixed_price", "description": "Painting as quoted", "amount": quote_total}
            )
            if extra and extra["outcome"] == "change_order":
                lines.append(
                    {"kind": "change_order", "description": extra["item"], "amount": extra_value}
                )
            elif extra and extra["outcome"] == "unbilled":
                leaks.append({"leak_type": "unbilled_extra_work", "amount": extra_value})
            if rng.random() < 0.05:
                disc = round(quote_total * float(rng.uniform(0.03, 0.1)), 2)
                lines.append(
                    {"kind": "discount", "description": "Repeat-customer discount", "amount": -disc}
                )
            elif rng.random() < 0.045:
                cut = round(quote_total * float(rng.uniform(0.05, 0.15)), 2)
                lines[0]["amount"] = round(quote_total - cut, 2)
                leaks.append({"leak_type": "unapproved_discount", "amount": cut})
            elif rng.random() < 0.04:
                # A credit promised in a message: approved, just not written as a line.
                credit = float(
                    max(50, round(quote_total * float(rng.uniform(0.03, 0.08)) / 25) * 25)
                )
                lines[0]["amount"] = round(quote_total - credit, 2)
                customer = {"customer_id": job["customer_id"]}
                w.messages.append(
                    self._message(
                        biz,
                        customer,
                        job_id,
                        done,
                        self.writer.concession(credit),
                        "outbound",
                        "concession",
                    )
                )
        else:
            billable = [v for v in visits if not v.get("no_charge")]
            billed_visits = list(billable)
            if len(billable) > 2 and rng.random() < 0.10:
                dropped = [v for v in billable if rng.random() < 0.25] or [billable[-2]]
                billed_visits = [v for v in billable if v not in dropped]
                leaks.append(
                    {
                        "leak_type": "unbilled_hours",
                        "amount": round(sum(v["hours"] for v in dropped) * rate, 2),
                    }
                )
            job_exp = [e for e in expenses if not e.get("shop_stock")]
            billed_exp = list(job_exp)
            if len(job_exp) > 1 and rng.random() < 0.10:
                dropped_e = job_exp[int(rng.integers(0, len(job_exp)))]
                billed_exp = [e for e in job_exp if e is not dropped_e]
                leaks.append(
                    {
                        "leak_type": "unbilled_materials",
                        "amount": round(dropped_e["amount"] * (1 + mk), 2),
                    }
                )
            hours = sum(v["hours"] for v in billed_visits)
            lines.append(
                {
                    "kind": "labor",
                    "description": f"{hours:.1f} h at ${rate}",
                    "hours": round(hours, 2),
                    "amount": round(hours * rate, 2),
                }
            )
            lines.append(
                {
                    "kind": "materials",
                    "description": "Materials at cost plus markup",
                    "amount": round(sum(e["amount"] for e in billed_exp) * (1 + mk), 2),
                }
            )
            if extra and extra["outcome"] == "unbilled":
                # On a time-and-materials job the hours are billed anyway; only a missed
                # change order on fixed-price work is extra-work leakage.
                pass

        total = round(sum(line["amount"] for line in lines), 2)
        never = rng.random() < 0.03
        if never:
            leaks = [{"leak_type": "uninvoiced_job", "amount": total}]
        for leak in leaks:
            w.truth.append(
                {
                    "job_id": job_id,
                    "business_id": biz.business_id,
                    **leak,
                    "completed_at": job["completed_at"],
                }
            )
        if never:
            return
        invoice_day = done + dt.timedelta(
            days=int(
                rng.choice([0, 1, 2, 3, 5, 8, 12], p=[0.25, 0.25, 0.15, 0.12, 0.1, 0.08, 0.05])
            )
        )
        deposit = 0.0
        if total > 3000 and rng.random() < 0.6:
            deposit = round(total * 0.3, 2)
            w.invoices.append(
                {
                    "invoice_id": self._id("inv"),
                    "job_id": job_id,
                    "business_id": biz.business_id,
                    "kind": "deposit",
                    "created_at": _ts(
                        dt.date.fromisoformat(job["start_date"]) - dt.timedelta(days=2), 9
                    ),
                    "lines": [{"kind": "deposit", "description": "30% deposit", "amount": deposit}],
                    "total": deposit,
                }
            )
        final_lines = [*lines]
        if deposit:
            final_lines.append(
                {"kind": "deposit_credit", "description": "Less deposit", "amount": -deposit}
            )
        w.invoices.append(
            {
                "invoice_id": self._id("inv"),
                "job_id": job_id,
                "business_id": biz.business_id,
                "kind": "final",
                "created_at": _ts(invoice_day, 11),
                "lines": final_lines,
                "total": round(total - deposit, 2),
            }
        )

    def _split(self, amount: float, n: int) -> list[float]:
        weights = self.rng.dirichlet(np.ones(n))
        return [float(amount * x) for x in weights]

    def _message(
        self,
        biz: Business,
        customer: dict[str, Any],
        job_id: str,
        day: dt.date,
        body: str,
        direction: str,
        kind: str,
    ) -> dict[str, Any]:
        msg = {
            "message_id": self._id("msg"),
            "business_id": biz.business_id,
            "job_id": job_id,
            "customer_id": customer["customer_id"],
            "sent_at": _ts(day, float(self.rng.uniform(8, 20))),
            "direction": direction,
            "body": body,
        }
        self.world.message_truth.append(
            {
                "message_id": msg["message_id"],
                "kind": kind,
                "is_extra_request": kind == "extra_request",
            }
        )
        return msg


def generate(out: Path = Path("data/generated"), seed: int = 7) -> World:
    """Write the world as JSON lines, one file per entity, plus the ground truth."""
    world = Generator(seed).build()
    out.mkdir(parents=True, exist_ok=True)
    tables: dict[str, list[dict[str, Any]]] = {
        "businesses": [asdict(b) for b in world.businesses],
        "customers": world.customers,
        "quotes": world.quotes,
        "jobs": world.jobs,
        "visits": world.visits,
        "expenses": world.expenses,
        "messages": world.messages,
        "change_orders": world.change_orders,
        "invoices": world.invoices,
        "truth": world.truth,
        "message_truth": world.message_truth,
    }
    for name, rows in tables.items():
        with (out / f"{name}.jsonl").open("w") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
    return world
