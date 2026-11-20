"""How many hours will this job really take? A check on quotes before they go out.

Owners quote from experience; on painting jobs the misses come from surface condition,
stories and season, which a quick estimate under-weights. A LightGBM model trained on the
dev businesses' finished jobs predicts billable hours from what is known at quote time
(features from the Feast store), and flags quotes likely to run more than 15% over.

Compared on the held-out businesses with the owner's own estimate and with the median
hours per job type (scaled by area). Jobs where the customer asked for extra work are left
out of training and evaluation, since their hours include work that was never quoted.
Every run is logged to MLflow; the model is registered and, if it beats both baselines,
given the ``champion`` alias.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from leakcheck import features

MLFLOW_URI = "sqlite:///mlruns.db"
OVERRUN = 1.15  # a quote "runs over" when real hours exceed the quoted hours by 15%


def frame(
    warehouse: Path = Path("data/warehouse.duckdb"),
    root: Path = features.ROOT,
    splits: dict[str, str] | None = None,
) -> pd.DataFrame:
    path = features.export(warehouse, root)
    features.apply(root)
    raw = pd.read_parquet(path)
    entities = raw[["job_id", "event_timestamp"]].copy()
    df = features.training_frame(entities, root).merge(
        raw[
            ["job_id", "business_id", "job_type", "actual_hours", "had_extra_request", "area_sqft"]
        ].rename(columns={"area_sqft": "area"}),
        on="job_id",
    )
    if splits is not None:
        df["split"] = df["business_id"].map(splits)
    return df


def _median_baseline(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    """Median hours per square foot for the job type, times the job's area."""
    per_sqft = (train["actual_hours"] / train["area"]).groupby(train["job_type"]).median()
    return np.asarray(test["job_type"].map(per_sqft) * test["area"])


def _overrun_flags(df: pd.DataFrame, predicted: np.ndarray) -> dict[str, float]:
    truth = df["actual_hours"].to_numpy() > OVERRUN * df["est_hours"].to_numpy()
    flagged = predicted > OVERRUN * df["est_hours"].to_numpy()
    tp = int((truth & flagged).sum())
    return {
        "overrun_jobs": int(truth.sum()),
        "flagged": int(flagged.sum()),
        "precision": round(tp / flagged.sum(), 3) if flagged.sum() else 0.0,
        "recall": round(tp / truth.sum(), 3) if truth.sum() else 0.0,
    }


def train_and_evaluate(df: pd.DataFrame, register: bool = True) -> dict[str, Any]:
    import lightgbm as lgb
    import mlflow

    clean = df[~df["had_extra_request"]]
    train, test = clean[clean["split"] == "dev"], clean[clean["split"] == "test"]
    x_cols = features.FEATURES
    params: dict[str, Any] = {
        "n_estimators": 400,
        "learning_rate": 0.03,
        "num_leaves": 15,
        "min_child_samples": 10,
        "subsample": 0.9,
        "subsample_freq": 1,
        "verbose": -1,
    }
    model = lgb.LGBMRegressor(**params)
    # Hours are skewed; fit the log and predict back on the hour scale.
    model.fit(
        train[x_cols],
        np.log1p(train["actual_hours"]),
        categorical_feature=["job_type_code", "condition_code"],
    )
    pred = np.expm1(model.predict(test[x_cols]))
    owner = test["est_hours"].to_numpy()
    median = _median_baseline(train, test)
    y = test["actual_hours"].to_numpy()

    def mae(p: np.ndarray) -> float:
        return round(float(np.mean(np.abs(p - y))), 2)

    def mape(p: np.ndarray) -> float:
        return round(float(np.mean(np.abs(p - y) / y)), 3)

    result: dict[str, Any] = {
        "train_jobs": len(train),
        "test_jobs": len(test),
        "mae_hours": {
            "model": mae(pred),
            "owner_estimate": mae(owner),
            "median_per_type": mae(median),
        },
        "mape": {
            "model": mape(pred),
            "owner_estimate": mape(owner),
            "median_per_type": mape(median),
        },
        "overrun_flags": _overrun_flags(test, pred),
        "feature_importance": dict(
            zip(x_cols, (int(v) for v in model.feature_importances_), strict=True)
        ),
    }
    mlflow.set_tracking_uri(MLFLOW_URI)
    mlflow.set_experiment("quote-hours")
    with mlflow.start_run(run_name="lightgbm") as run:
        mlflow.log_params(params)
        mlflow.log_metrics(
            {
                "mae_model": result["mae_hours"]["model"],
                "mae_owner": result["mae_hours"]["owner_estimate"],
                "mae_median": result["mae_hours"]["median_per_type"],
                "overrun_recall": result["overrun_flags"]["recall"],
                "overrun_precision": result["overrun_flags"]["precision"],
            }
        )
        info = mlflow.lightgbm.log_model(model, name="model", input_example=test[x_cols].head(3))
        result["mlflow_run_id"] = run.info.run_id
    if register:
        client = mlflow.MlflowClient()
        version = mlflow.register_model(info.model_uri, "quote-hours")
        better = (
            result["mae_hours"]["model"] < result["mae_hours"]["owner_estimate"]
            and result["mae_hours"]["model"] < result["mae_hours"]["median_per_type"]
        )
        if better:
            client.set_registered_model_alias("quote-hours", "champion", version.version)
        result["registered_version"] = int(version.version)
        result["champion"] = better
    return result
