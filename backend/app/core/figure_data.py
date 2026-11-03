"""figure data responsibilities."""

from __future__ import annotations

import csv
import re
from pathlib import Path


def has_semantic_required_data_contract(role: str, required_data: list[str]) -> bool:
    normalized = [str(item).strip().lower() for item in required_data if str(item).strip()]
    if not normalized:
        return False
    if len(normalized) == 1 and normalized[0] == "result_registry.json":
        return False

    merged = " ".join(normalized)
    if role == "trajectory_shielding":
        return "trajectory" in merged and ("shield" in merged or "cloud" in merged)
    if role == "distance_time_curve":
        return "distance" in merged and "time" in merged
    return len(normalized) >= 1


def coerce_numeric_value(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value or "")
    assigned = re.search(r"[=:：]\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)", text)
    if assigned:
        try:
            return float(assigned.group(1))
        except ValueError:
            pass
    match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def deterministic_series_from_registry(
    result_registry: dict[str, object],
    required_data: list[str],
) -> tuple[list[float], list[float], list[str]]:
    verified = result_registry.get("verified_results", []) if isinstance(result_registry, dict) else []
    if not isinstance(verified, list):
        return [], [], []

    values: list[float] = []
    matched_sources: set[str] = set()
    required_set = {str(item).strip() for item in required_data if str(item).strip()}

    for entry in verified:
        if not isinstance(entry, dict):
            continue
        entry_sources = [str(item).strip() for item in (entry.get("source_data", []) or []) if str(item).strip()]
        if required_set and not (required_set.intersection(set(entry_sources))):
            continue
        value = coerce_numeric_value(entry.get("value"))
        if value is None:
            continue
        result_id = str(entry.get("id") or "").strip()
        if not result_id:
            continue
        values.append(value)
        for src in entry_sources:
            matched_sources.add(src)
        if len(values) >= 24:
            break

    x_values = [float(index + 1) for index in range(len(values))]
    return x_values, values, sorted(matched_sources)


def deterministic_series_from_data_files(
    work_dir: str,
    data_files: list[str],
) -> tuple[list[float], list[float], list[str]]:
    root = Path(work_dir)
    candidates: list[tuple[Path, str]] = []
    for raw in data_files:
        rel = str(raw or "").strip()
        if not rel:
            continue
        path = root / rel
        if path.exists() and path.is_file():
            candidates.append((path, rel.replace("\\", "/")))

    if not candidates:
        return [], [], []

    def _to_float(v: object) -> float | None:
        try:
            text = str(v).strip()
            if not text:
                return None
            return float(text)
        except Exception:
            return None

    def _pick_columns(column_map: dict[str, list[float]]) -> tuple[list[float], list[float]]:
        if not column_map:
            return [], []
        names = list(column_map.keys())

        x_candidates = [
            key for key in names if any(token in key.lower() for token in ["wavenumber", "波数", "time", "时间", "x"])
        ]
        y_candidates = [
            key for key in names if any(token in key.lower() for token in ["reflectance", "反射率", "intensity", "value", "y"])
        ]

        x_name = x_candidates[0] if x_candidates else ""
        y_name = y_candidates[0] if y_candidates else ""

        if not y_name:
            for name in names:
                if name != x_name and len(column_map.get(name, [])) >= 2:
                    y_name = name
                    break
        if not x_name:
            x_name = next((name for name in names if name != y_name and len(column_map.get(name, [])) >= 2), "")

        if y_name:
            y_values = column_map.get(y_name, [])
            if x_name:
                x_values = column_map.get(x_name, [])
                if len(x_values) == len(y_values) and len(y_values) >= 2:
                    return x_values, y_values
            if len(y_values) >= 2:
                return [float(i + 1) for i in range(len(y_values))], y_values
        return [], []

    for path, rel in candidates:
        suffix = path.suffix.lower()
        if suffix == ".csv":
            try:
                with path.open("r", encoding="utf-8", newline="") as fp:
                    reader = csv.DictReader(fp)
                    if not reader.fieldnames:
                        continue
                    column_map: dict[str, list[float]] = {str(name): [] for name in reader.fieldnames if str(name).strip()}
                    for row in reader:
                        for name in list(column_map.keys()):
                            val = _to_float((row or {}).get(name, ""))
                            if val is not None:
                                column_map[name].append(val)
                x_values, y_values = _pick_columns(column_map)
                if len(x_values) >= 2 and len(y_values) >= 2:
                    return x_values, y_values, [rel]
            except Exception:
                continue

        if suffix in {".xlsx", ".xls"}:
            try:
                from openpyxl import load_workbook  # type: ignore

                wb = load_workbook(path, data_only=True, read_only=True)
                ws = wb.active
                rows = ws.iter_rows(min_row=1, max_row=2000, values_only=True)
                header = next(rows, None)
                if not header:
                    continue
                names = [str(item or "").strip() for item in header]
                column_map: dict[str, list[float]] = {name: [] for name in names if name}
                for row in rows:
                    for idx, name in enumerate(names):
                        if not name:
                            continue
                        val = _to_float(row[idx] if row and idx < len(row) else "")
                        if val is not None:
                            column_map[name].append(val)
                x_values, y_values = _pick_columns(column_map)
                if len(x_values) >= 2 and len(y_values) >= 2:
                    return x_values, y_values, [rel]
            except Exception:
                continue

    return [], [], []


def deterministic_named_columns_from_data_files(
    work_dir: str,
    data_files: list[str],
) -> tuple[dict[str, list[float]], list[str]]:
    root = Path(work_dir)
    candidates: list[tuple[Path, str]] = []
    for raw in data_files:
        rel = str(raw or "").strip()
        if not rel:
            continue
        path = root / rel
        if path.exists() and path.is_file():
            candidates.append((path, rel.replace("\\", "/")))

    def _to_float(v: object) -> float | None:
        try:
            text = str(v).strip()
            if not text:
                return None
            return float(text)
        except Exception:
            return None

    for path, rel in candidates:
        suffix = path.suffix.lower()
        if suffix == ".csv":
            try:
                with path.open("r", encoding="utf-8", newline="") as fp:
                    reader = csv.DictReader(fp)
                    if not reader.fieldnames:
                        continue
                    column_map: dict[str, list[float]] = {str(name): [] for name in reader.fieldnames if str(name).strip()}
                    for row in reader:
                        for name in list(column_map.keys()):
                            val = _to_float((row or {}).get(name, ""))
                            if val is not None:
                                column_map[name].append(val)
                if any(len(values) >= 2 for values in column_map.values()):
                    return column_map, [rel]
            except Exception:
                continue
        if suffix in {".xlsx", ".xls"}:
            try:
                from openpyxl import load_workbook  # type: ignore

                wb = load_workbook(path, data_only=True, read_only=True)
                ws = wb.active
                rows = ws.iter_rows(min_row=1, max_row=3000, values_only=True)
                header = next(rows, None)
                if not header:
                    continue
                names = [str(item or "").strip() for item in header]
                column_map: dict[str, list[float]] = {name: [] for name in names if name}
                for row in rows:
                    for idx, name in enumerate(names):
                        if not name:
                            continue
                        val = _to_float(row[idx] if row and idx < len(row) else "")
                        if val is not None:
                            column_map[name].append(val)
                if any(len(values) >= 2 for values in column_map.values()):
                    return column_map, [rel]
            except Exception:
                continue

    return {}, []


def extract_named_series(columns: dict[str, list[float]], aliases: list[str]) -> list[float]:
    lowered_aliases = [alias.lower() for alias in aliases]
    for name, values in columns.items():
        lowered = name.lower()
        if any(token in lowered for token in lowered_aliases) and len(values) >= 2:
            return values
    return []


def deterministic_overview_columns_from_registry(
    result_registry: dict[str, object],
) -> tuple[dict[str, list[float]], list[str], list[str]]:
    verified = result_registry.get("verified_results", []) if isinstance(result_registry, dict) else []
    if not isinstance(verified, list):
        return {}, [], []

    linked_ids: list[str] = []
    sources: set[str] = set()
    columns: dict[str, list[float]] = {}
    for entry in verified:
        if not isinstance(entry, dict):
            continue
        result_id = str(entry.get("id") or "").strip()
        raw_value = entry.get("value")
        value_text = str(raw_value or "")
        if "\n" in value_text or "|" in value_text or result_id.endswith("_result_table"):
            continue
        value = coerce_numeric_value(raw_value)
        if not result_id or value is None:
            continue
        raw_label = str(entry.get("name") or result_id).strip() or result_id
        label = raw_label[:48]
        if label in columns:
            label = result_id[:48]
        columns[label] = [value]
        linked_ids.append(result_id)
        for src in entry.get("source_data", []) or []:
            text = str(src or "").strip()
            if text:
                sources.add(text)
        if len(columns) >= 24:
            break

    if not columns:
        return {}, [], []
    return columns, linked_ids, sorted(sources) or ["result_registry.json"]
