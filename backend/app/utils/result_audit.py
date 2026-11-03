"""result audit responsibilities."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any
from app.utils.problem_text import _slugify_identifier


def _parse_float_cell(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _pick_csv_column(columns: list[str], keywords: list[str], exact: set[str] | None = None) -> str:
    exact = exact or set()
    lowered_exact = {item.lower() for item in exact}
    for column in columns:
        normalized = str(column).strip()
        if normalized in exact or normalized.lower() in lowered_exact:
            return column
    for column in columns:
        lowered = str(column).strip().lower()
        if any(keyword.lower() in lowered for keyword in keywords):
            return column
    return ""


def _close_relative(value: float, expected: float, tolerance: float = 0.015) -> bool:
    if expected == 0:
        return abs(value) <= tolerance
    return abs(value - expected) / max(abs(expected), 1e-12) <= tolerance


def _audit_torque_coefficient_artifacts(work_dir: str) -> list[dict[str, Any]]:
    """Detect the common error of using P/T as K when the model states T=K*P*d."""
    findings: list[dict[str, Any]] = []
    root = Path(work_dir)
    if not root.exists():
        return findings

    for csv_path in sorted(root.glob("*.csv")):
        try:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as file_obj:
                rows = list(csv.DictReader(file_obj))
        except Exception:
            continue
        if not rows:
            continue

        columns = list(rows[0].keys())
        k_col = _pick_csv_column(columns, ["扭矩系数", "k值", "k_"], {"K", "k", "K值"})
        t_col = _pick_csv_column(columns, ["预紧力矩", "torque", "t_nm"], {"T", "t"})
        p_col = _pick_csv_column(columns, ["预紧力", "pretension", "p_kn"], {"P", "p"})
        d_col = _pick_csv_column(columns, ["直径", "diameter", "d_mm"], {"d", "D"})
        if p_col == t_col:
            p_col = next(
                (
                    column for column in columns
                    if ("预紧力" in str(column) and "力矩" not in str(column)) or "p_" in str(column).lower()
                ),
                "",
            )
        if not (k_col and t_col and p_col and d_col):
            continue

        wrong_rows: list[str] = []
        checked = 0
        for index, row in enumerate(rows, start=2):
            k_value = _parse_float_cell(row.get(k_col))
            torque = _parse_float_cell(row.get(t_col))
            pretension = _parse_float_cell(row.get(p_col))
            diameter = _parse_float_cell(row.get(d_col))
            if None in {k_value, torque, pretension, diameter} or not torque or not pretension or not diameter:
                continue
            checked += 1
            correct_k = torque / (pretension * diameter)
            slope_p_over_t = pretension / torque
            if _close_relative(k_value, slope_p_over_t) and not _close_relative(k_value, correct_k):
                wrong_rows.append(
                    f"row {index}: {k_col}={k_value:g}, P/T={slope_p_over_t:g}, T/(P*d)={correct_k:g}"
                )
            if len(wrong_rows) >= 5:
                break

        if wrong_rows:
            findings.append(
                {
                    "id": _slugify_identifier(f"{csv_path.stem}_torque_coefficient_formula_mismatch", "audit"),
                    "name": f"{csv_path.name} torque coefficient formula mismatch",
                    "section": "programmatic_result_audit",
                    "value": "blocked",
                    "paper_value": "",
                    "unit": "",
                    "formula": "If T = K * P * d, then K = T / (P * d), not P / T.",
                    "formula_id": "torque_coefficient_dimensional_check",
                    "inputs": {"checked_rows": checked, "k_column": k_col, "t_column": t_col, "p_column": p_col, "d_column": d_col},
                    "unit_conversion": "N*m == kN*mm",
                    "source_data": [csv_path.name],
                    "code_cell": "programmatic csv dimensional audit",
                    "evidence": "; ".join(wrong_rows),
                    "source": "programmatic_result_audit",
                    "source_file": csv_path.name,
                    "generated_files": [],
                    "status": "blocked",
                    "verified": False,
                    "warnings": ["K appears to equal P/T while the stated model requires K=T/(P*d)."],
                }
            )

    return findings
