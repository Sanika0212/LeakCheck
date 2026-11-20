"""Quote-hours model: trained on the dev businesses, measured on the held-out ones.

    python eval/run_quote.py

Features come from the Feast store over ``gold.quote_features``; the run is logged to
MLflow and the model registered (``champion`` alias if it beats both baselines).
"""

from __future__ import annotations

from common import read_jsonl, write

from leakcheck import quote_model


def main() -> None:
    splits = {b["business_id"]: b["split"] for b in read_jsonl("businesses")}
    result = quote_model.train_and_evaluate(quote_model.frame(splits=splits))
    print({k: result[k] for k in ("mae_hours", "overrun_flags", "champion")})
    print("wrote", write("quote", result))


if __name__ == "__main__":
    main()
