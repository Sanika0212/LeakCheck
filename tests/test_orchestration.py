from __future__ import annotations


def test_dagster_definitions_load() -> None:
    import dagster as dg

    from leakcheck.orchestration import defs

    dg.Definitions.validate_loadable(defs)
    names = {k.to_user_string() for k in defs.resolve_asset_graph().get_all_asset_keys()}
    assert names == {"generated_world", "bronze_events", "warehouse_findings", "quote_model"}
