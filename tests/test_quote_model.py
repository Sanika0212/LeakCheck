"""Quote model end to end: Feast features, LightGBM, MLflow registry."""

from __future__ import annotations

from typing import Any

import pytest


def test_model_beats_the_owner_and_is_registered(
    world: dict[str, Any], tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from leakcheck import features, quote_model

    monkeypatch.setattr(quote_model, "MLFLOW_URI", f"sqlite:///{tmp_path / 'mlruns.db'}")
    monkeypatch.chdir(tmp_path)  # MLflow artifacts land here
    splits = {b.business_id: b.split for b in world["world"].businesses}
    df = quote_model.frame(world["warehouse"], tmp_path / "features", splits)
    result = quote_model.train_and_evaluate(df)
    assert result["mae_hours"]["model"] < result["mae_hours"]["owner_estimate"]
    assert result["registered_version"] >= 1
    import datetime as dt

    features.materialize(dt.datetime(2025, 12, 31), tmp_path / "features")
    job = df["job_id"].iloc[0]
    online = features.online([job], tmp_path / "features")
    assert online[0]["est_hours"] == pytest.approx(
        float(df.loc[df["job_id"] == job, "est_hours"].iloc[0])
    )
