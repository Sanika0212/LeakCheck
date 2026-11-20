"""Shared helpers for the evaluation scripts."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

RUNS = Path("runs")
GENERATED = Path("data/generated")


def read_jsonl(name: str) -> list[dict[str, Any]]:
    path = GENERATED / f"{name}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_businesses() -> set[str]:
    return {b["business_id"] for b in read_jsonl("businesses") if b["split"] == "test"}


def write(name: str, payload: Any) -> Path:
    path = RUNS / name / "results.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def prf(tp: int, fp: int, fn: int) -> dict[str, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {
        "precision": round(p, 3),
        "recall": round(r, 3),
        "f1": round(f, 3),
        "tp": tp,
        "fp": fp,
        "fn": fn,
    }
