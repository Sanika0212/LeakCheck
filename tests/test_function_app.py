from __future__ import annotations

import sys
from pathlib import Path

import pytest


def test_function_app_registers_its_functions() -> None:
    pytest.importorskip("azure.functions")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "function_app"))
    import function_app

    names = {f.get_function_name() for f in function_app.app.get_functions()}
    assert names == {"daily_drafts", "review_job"}
