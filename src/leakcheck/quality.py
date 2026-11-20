"""Great Expectations checks on silver, run between the silver and gold builds.

A failing suite stops the build: gold findings are only computed from data that passed.
The expectations encode what a field-service business's records must look like (a visit
is between 6 minutes and 12 hours, a billing rate between $40 and $150 an hour, a masked
identifier is 16 characters) rather than statistics fitted to this data.
"""

from __future__ import annotations

from typing import Any

import duckdb


def suites() -> dict[str, list[Any]]:
    import great_expectations.expectations as expectations

    gxe: Any = expectations  # the classes are exported dynamically

    return {
        "stg_visits": [
            gxe.ExpectColumnValuesToNotBeNull(column="job_id"),
            gxe.ExpectColumnValuesToBeBetween(column="hours", min_value=0.1, max_value=12),
            gxe.ExpectColumnValuesToBeUnique(column="visit_id"),
        ],
        "stg_quotes": [
            gxe.ExpectColumnValuesToBeBetween(column="rate", min_value=40, max_value=150),
            gxe.ExpectColumnValuesToBeInSet(
                column="pricing", value_set=["fixed", "time_materials"]
            ),
            gxe.ExpectColumnValuesToBeBetween(column="est_hours", min_value=0.5, max_value=2000),
            gxe.ExpectColumnValuesToBeBetween(column="markup", min_value=0, max_value=1),
        ],
        "stg_invoices": [
            gxe.ExpectColumnValuesToNotBeNull(column="total"),
            gxe.ExpectColumnValuesToBeInSet(column="kind", value_set=["deposit", "final"]),
        ],
        "stg_expenses": [
            gxe.ExpectColumnValuesToBeBetween(column="amount", min_value=-500, max_value=10000),
            gxe.ExpectColumnValuesToNotBeNull(column="job_id"),
        ],
        "stg_messages": [
            gxe.ExpectColumnValuesToBeInSet(column="direction", value_set=["inbound", "outbound"]),
            gxe.ExpectColumnValuesToNotBeNull(column="body"),
        ],
        "stg_customers": [
            gxe.ExpectColumnValueLengthsToEqual(column="email_hash", value=16),
            gxe.ExpectColumnValueLengthsToEqual(column="phone_hash", value=16),
            gxe.ExpectColumnValuesToBeUnique(column="customer_id"),
        ],
    }


def validate(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    import great_expectations as gx
    from great_expectations.data_context.types.base import ProgressBarsConfig

    context = gx.get_context(mode="ephemeral")
    context.variables.progress_bars = ProgressBarsConfig(globally=False)
    source = context.data_sources.add_pandas("silver")
    report: dict[str, Any] = {"success": True, "tables": {}}
    for table, expectations in suites().items():
        df = con.execute(f"select * from silver.{table}").df()
        batch = (
            source.add_dataframe_asset(name=table)
            .add_batch_definition_whole_dataframe("all")
            .get_batch(batch_parameters={"dataframe": df})
        )
        suite = context.suites.add(gx.ExpectationSuite(name=f"{table}_suite"))
        for e in expectations:
            suite.add_expectation(e)
        result = batch.validate(suite)
        failed = [
            r.expectation_config.type if r.expectation_config else "unknown"
            for r in result.results
            if not r.success
        ]
        report["tables"][table] = {
            "rows": len(df),
            "expectations": len(expectations),
            "failed": failed,
        }
        report["success"] = report["success"] and result.success
    return report


class QualityFailed(RuntimeError):
    pass
