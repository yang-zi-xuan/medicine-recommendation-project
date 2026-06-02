from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.query import query  # noqa: E402


CASES = [
    {"name": "herb_exact_ren_shen", "model": 0, "herb": "人参", "formula": ""},
    {"name": "herb_contains_ci_ren_shen", "model": 0, "herb": "刺人参", "formula": ""},
    {"name": "formula_exact", "model": 1, "herb": "", "formula": "六味地黄丸"},
    {"name": "symptom_formula", "model": 2, "herb": "上火", "formula": ""},
    {"name": "chain_reasoning", "model": 3, "herb": "失眠 心悸", "formula": ""},
]


def _first_name(payload: dict[str, Any], model: int) -> str:
    data = payload.get("data") or {}
    if model == 3:
        chain = data.get("chain") or {}
        formulas = chain.get("formulas") or []
        return formulas[0].get("name", "") if formulas else ""
    items = data.get("items") or []
    return items[0].get("cn_name", "") if items else ""


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    t0 = time.perf_counter()
    payload = query(
        model=case["model"],
        herb=case.get("herb", ""),
        formula=case.get("formula", ""),
    )
    elapsed = time.perf_counter() - t0
    data = payload.get("data") or {}
    return {
        "case": case["name"],
        "model": case["model"],
        "input": case.get("herb") or case.get("formula"),
        "code": payload.get("code"),
        "message": payload.get("message"),
        "match_type": data.get("match_type"),
        "first_result": _first_name(payload, case["model"]),
        "elapsed_sec": round(elapsed, 4),
        "timing": data.get("_timing"),
        "error": data.get("detail"),
    }


def main() -> None:
    results = [run_case(case) for case in CASES]
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
