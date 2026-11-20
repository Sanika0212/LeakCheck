"""Public data the generator is calibrated on, fetched once and committed.

    leakcheck calibrate          # refreshes data/calibration.json

1. BLS Occupational Employment and Wage Statistics: mean hourly wage of painters,
   construction and maintenance (SOC 47-2141), national.
2. BLS Producer Price Index, paint and coating manufacturing (PCU325510325510), monthly:
   materials prices move with it.
3. Open-Meteo historical weather for Buffalo, NY: the share of days in each month with at
   least 2.5 mm of rain, which delays exterior work.

The file records each source URL and when it was retrieved, so every generated business can
be traced to the public numbers behind it.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

BLS = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
WAGE_SERIES = "OEUN000000000000047214103"  # national, all industries, painters, mean hourly wage
PPI_SERIES = "PCU325510325510"  # paint and coating manufacturing
METEO = (
    "https://archive-api.open-meteo.com/v1/archive?latitude=42.89&longitude=-78.88"
    "&start_date=2023-01-01&end_date=2025-12-31&daily=precipitation_sum"
    "&timezone=America%2FNew_York"
)
DEFAULT_PATH = Path("data/calibration.json")


def _bls(series: list[str]) -> dict[str, list[dict[str, str]]]:
    body = {"seriesid": series, "startyear": "2023", "endyear": "2025"}
    r = httpx.post(BLS, json=body, timeout=60)
    r.raise_for_status()
    return {s["seriesID"]: s["data"] for s in r.json()["Results"]["series"]}


def fetch() -> dict[str, Any]:
    bls = _bls([WAGE_SERIES, PPI_SERIES])
    wage = next(x for x in bls[WAGE_SERIES] if x["value"] not in ("-", ""))
    ppi = {
        f"{x['year']}-{x['period'][1:]}": float(x["value"])
        for x in bls[PPI_SERIES]
        if x["period"].startswith("M") and x["value"] not in ("-", "")
    }
    daily = httpx.get(METEO, timeout=60).json()["daily"]
    wet: Counter[int] = Counter()
    days: Counter[int] = Counter()
    for day, mm in zip(daily["time"], daily["precipitation_sum"], strict=True):
        month = int(day[5:7])
        days[month] += 1
        wet[month] += mm is not None and mm >= 2.5
    return {
        "retrieved": dt.date.today().isoformat(),
        "painter_mean_hourly_wage": {
            "value": float(wage["value"]),
            "year": wage["year"],
            "source": f"BLS OEWS series {WAGE_SERIES}",
        },
        "paint_ppi": {
            "values": dict(sorted(ppi.items())),
            "source": f"BLS PPI series {PPI_SERIES} (paint and coating manufacturing)",
        },
        "wet_day_share": {
            "values": {str(m): round(wet[m] / days[m], 3) for m in range(1, 13)},
            "source": "Open-Meteo archive, Buffalo NY, 2023 to 2025, days with >= 2.5 mm",
        },
    }


def save(path: Path = DEFAULT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fetch(), indent=2) + "\n")
    return path


def load(path: Path = DEFAULT_PATH) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(path.read_text())
    return result
