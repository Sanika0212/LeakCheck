"""The generator's ground truth must match what it wrote into the records."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def test_every_leak_is_visible_in_the_records(world: dict[str, Any]) -> None:
    w = world["world"]
    invoices = defaultdict(list)
    for inv in w.invoices:
        invoices[inv["job_id"]].append(inv)
    jobs = {j["job_id"]: j for j in w.jobs}
    for leak in w.truth:
        job = jobs[leak["job_id"]]
        if leak["leak_type"] == "uninvoiced_job":
            assert not any(i["kind"] == "final" for i in invoices[job["job_id"]])
        else:
            assert any(i["kind"] == "final" for i in invoices[job["job_id"]])
        assert leak["amount"] > 0


def test_time_and_materials_invoices_bill_hours_and_materials(world: dict[str, Any]) -> None:
    w = world["world"]
    tm = {j["job_id"] for j in w.jobs if j["pricing"] == "time_materials"}
    finals = [i for i in w.invoices if i["kind"] == "final" and i["job_id"] in tm]
    assert finals
    for inv in finals:
        kinds = {line["kind"] for line in inv["lines"]}
        assert {"labor", "materials"} <= kinds


def test_simulator_only_fields_never_reach_events(world: dict[str, Any]) -> None:
    for e in world["events"]:
        assert not {"true_hours", "rain_days", "no_charge", "shop_stock"} & set(e["data"])


def test_held_out_businesses_use_their_own_phrasing(world: dict[str, Any]) -> None:
    from leakcheck.messages import EXPLICIT, IMPLICIT

    w = world["world"]
    test = {b.business_id for b in w.businesses if b.split == "test"}
    kinds = {m["message_id"]: m["kind"] for m in w.message_truth}
    dev_starts = {t.split("{")[0].strip()[:20] for t in EXPLICIT + IMPLICIT}
    for m in w.messages:
        if m["business_id"] in test and kinds[m["message_id"]] == "extra_request":
            assert not any(len(s) >= 12 and s in m["body"] for s in dev_starts)
