"""operator_runtimes: artifact_io 领域实现与合同。"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time
import hashlib
import json
import math
from pathlib import (
    Path,
    PurePosixPath,
)
from typing import Any

import numpy as np
import pandas as pd
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter, range_boundaries


def valid_scalar_output(*, outputs: dict) -> list[str]:
    if set(outputs) != {"value"}:
        return ["expected exactly one output named value"]
    if not isinstance(outputs["value"], (int, float, str, bool)):
        return ["output value must be scalar"]
    return []

def valid_vector_output(*, outputs: dict) -> list[str]:
    if set(outputs) != {"value"}:
        return ["expected exactly one output named value"]
    if not isinstance(outputs["value"], list):
        return ["output value must be a list"]
    return []

def valid_matrix_output(*, outputs: dict) -> list[str]:
    if set(outputs) != {"value"}:
        return ["expected exactly one output named value"]
    value = outputs["value"]
    if not isinstance(value, list) or not value or not all(isinstance(row, list) for row in value):
        return ["output value must be a non-empty matrix"]
    column_count = len(value[0])
    if column_count == 0 or any(len(row) != column_count for row in value):
        return ["output matrix must be non-empty and rectangular"]
    try:
        matrix = np.asarray(value, dtype=float)
    except (TypeError, ValueError):
        return ["output matrix must be numeric"]
    if not np.all(np.isfinite(matrix)):
        return ["output matrix must contain finite values"]
    return []

def _path_value(artifact: object, path: str) -> object:
    value = artifact
    for part in path.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
            continue
        if isinstance(value, list) and part.isdecimal():
            index = int(part)
            if index < len(value):
                value = value[index]
                continue
        if not part:
            raise ValueError(f"artifact path does not exist: {path}")
        raise ValueError(f"artifact path does not exist: {path}")
    return value

def extract_mapping(*, inputs: dict, parameters: dict, random_seed, work_dir: Path) -> dict:
    del random_seed, work_dir
    fields = parameters["fields"]
    if fields is not None:
        value = {
            str(name): _path_value(inputs["artifact"], str(path))
            for name, path in fields.items()
        }
    else:
        value = _path_value(inputs["artifact"], parameters["path"])
    if not isinstance(value, dict):
        raise ValueError("selected artifact value is not a mapping")
    return {"value": value}

def extract_matrix(*, inputs: dict, parameters: dict, random_seed, work_dir: Path) -> dict:
    del random_seed, work_dir
    value = _path_value(inputs["artifact"], parameters["path"])
    if not isinstance(value, list):
        raise ValueError("selected artifact value is not a matrix")
    return {"value": value}

def extract_scalar(*, inputs: dict, parameters: dict, random_seed, work_dir: Path) -> dict:
    del random_seed, work_dir
    value = _path_value(inputs["artifact"], parameters["path"])
    indices = parameters["indices"]
    if indices is not None:
        if not isinstance(value, list):
            raise ValueError("indexed scalar projection requires a non-empty list")
        selected = []
        for index in indices:
            if not isinstance(index, int) or index < 0 or index >= len(value):
                raise ValueError("scalar projection index is out of bounds")
            selected.append(value[index])
        value = selected
    reducer = parameters["reducer"]
    if reducer == "sum":
        value = sum(value)
    if not isinstance(value, (int, float, str, bool)):
        raise ValueError("selected artifact value is not scalar")
    return {"value": value}

def extract_vector(*, inputs: dict, parameters: dict, random_seed, work_dir: Path) -> dict:
    del random_seed, work_dir
    value = _path_value(inputs["artifact"], parameters["path"])
    if not isinstance(value, list):
        raise ValueError("selected artifact value is not a vector")
    indices = parameters["indices"]
    if indices is not None:
        selected: list[Any] = []
        for index in indices:
            if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index >= len(value):
                raise ValueError("vector projection index is out of bounds")
            selected.append(value[index])
        value = selected
    return {"value": value}

def scalar_constant(*, inputs: dict, parameters: dict, random_seed, work_dir: Path) -> dict:
    del inputs, random_seed, work_dir
    value = parameters["value"]
    if not isinstance(value, (int, float, str, bool)):
        raise ValueError("constant value must be scalar")
    return {"value": value}

def workspace_path(reference: object, work_dir: Path) -> Path:
    if not isinstance(reference, str):
        raise ValueError("data source must be a workspace-relative path")
    normalized = reference.split("#", 1)[0].replace("\\", "/")
    parsed = PurePosixPath(normalized)
    if parsed.is_absolute() or ".." in parsed.parts or (parsed.parts and ":" in parsed.parts[0]):
        raise ValueError("data source escapes the workspace")
    resolved = (work_dir / Path(*parsed.parts)).resolve()
    if work_dir.resolve() not in (resolved, *resolved.parents):
        raise ValueError("data source escapes the workspace")
    return resolved

def read_excel_table(*, inputs: dict, parameters: dict, random_seed, work_dir: Path) -> dict:
    del random_seed
    path = workspace_path(inputs["source"], work_dir)
    book = load_workbook(path, read_only=True, data_only=False)
    try:
        sheet = parameters["sheet"]
        if isinstance(sheet, int):
            if not 0 <= sheet < len(book.worksheets):
                raise ValueError("sheet index is out of bounds")
            page = book.worksheets[sheet]
        else:
            if sheet not in book.sheetnames:
                raise ValueError(f"unknown sheet: {sheet}")
            page = book[sheet]
        header = parameters["header_row"] + 1
        start_column, start_row, end_column, end_row = (
            range_boundaries(parameters["cell_range"])
            if parameters["cell_range"] else (1, 1, page.max_column, page.max_row)
        )
        if start_column > end_column or start_row > end_row:
            raise ValueError("cell_range must be an increasing rectangle")
        if end_column > page.max_column or end_row > page.max_row:
            raise ValueError("cell_range exceeds worksheet bounds")
        if not start_row <= header <= end_row:
            raise ValueError("header_row must lie inside the selected worksheet region")
        rows = list(page.iter_rows(min_row=header, max_row=end_row,
                                  min_col=start_column, max_col=end_column, values_only=True))
    finally:
        book.close()
    if not rows:
        raise ValueError("header_row has no cells")
    names = [str(value) if value is not None and str(value).strip() else None for value in rows[0]]
    columns = parameters["columns"] or names
    if any(name is None for name in columns):
        raise ValueError("blank header; select a named-column region and an explicit header_row")
    if len(set(columns)) != len(columns):
        raise ValueError("duplicate selected columns or source headers")
    for name in columns:
        if name not in names:
            raise ValueError(f"unknown column: {name}")
        if names.count(name) != 1:
            raise ValueError(f"duplicate source header: {name}")
    positions = [names.index(name) for name in columns]
    data = rows[1:]
    if parameters["trim_trailing_empty_rows"]:
        while data and all(value is None for value in data[-1]):
            data.pop()
    row_limit = parameters["row_limit"]
    if row_limit is not None:
        data = data[:row_limit]
    frame = pd.DataFrame([[row[index] for index in positions] for row in data], columns=columns, dtype=object)
    if set(parameters["rename"]) - set(frame.columns):
        raise ValueError("rename references an unknown selected column")
    renamed = [parameters["rename"].get(name, name) for name in frame.columns]
    if len(set(renamed)) != len(renamed):
        raise ValueError("duplicate columns after rename")
    frame = frame.rename(columns=parameters["rename"])
    for column in parameters["forward_fill"]:
        frame[column] = frame[column].ffill()
    table = {str(column): [_json_cell(value) for value in frame[column]] for column in frame.columns}
    matrix_columns = parameters["matrix_columns"]
    if matrix_columns:
        matrix_name = str(parameters["matrix_name"])
        if matrix_name in table:
            raise ValueError("matrix output name collision with a table column")
        matrix = frame.loc[:, matrix_columns].to_numpy(dtype=float)
        if not np.all(np.isfinite(matrix)):
            raise ValueError("matrix projection requires finite numeric values; inspect missing cells first")
        table[matrix_name] = matrix.tolist()
    return {"table": table}


def _json_cell(value):
    """单元格只做无损日期/标量表示转换，不将 NA 等文本猜作缺失。"""
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, np.generic):
        value = value.item()
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("nonfinite cell value")
    return value


def _value_type(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "missing"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number" if math.isfinite(value) else "nonfinite"
    if isinstance(value, (datetime, date, time)):
        return "date"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, str):
        try:
            datetime.fromisoformat(value)
            return "date"
        except ValueError:
            return "text"
    raise ValueError(f"unsupported cell type: {type(value).__name__}")


def inspect_file(*, inputs: dict, parameters: dict, random_seed, work_dir: Path) -> dict:
    del random_seed
    path = workspace_path(inputs["source"], work_dir)
    if not path.is_file():
        raise ValueError("source is not a regular file")
    with path.open("rb") as handle:
        sha256 = hashlib.file_digest(handle, "sha256").hexdigest()
    inspection = {
        "source_ref": path.relative_to(work_dir.resolve()).as_posix(),
        "sha256": sha256, "size_bytes": path.stat().st_size,
        "extension": path.suffix.lower(), "content_kind": "uninspected", "sheets": [],
        "content_inspected": False,
    }
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        book = load_workbook(path, read_only=True, data_only=False)
        try:
            inspection["content_inspected"] = True
            inspection["content_kind"] = "excel_workbook"
            inspection["sheet_count"] = len(book.worksheets)
            inspection["sheets_truncated"] = len(book.worksheets) > parameters["preview_sheets"]
            for page in book.worksheets[:parameters["preview_sheets"]]:
                preview = []
                max_row = min(page.max_row, parameters["preview_rows"])
                max_column = min(page.max_column, parameters["preview_columns"])
                for row_index, row in enumerate(page.iter_rows(max_row=max_row, max_col=max_column), start=1):
                    for column_index, cell in enumerate(row, start=1):
                        value = _json_cell(cell.value)
                        entry = {"coordinate": f"{get_column_letter(column_index)}{row_index}",
                                 "value": value, "type": "formula" if cell.data_type == "f" else _value_type(value)}
                        if isinstance(value, str) and len(value) > parameters["cell_text_limit"]:
                            entry["value"] = value[:parameters["cell_text_limit"]]
                            entry["text_truncated"] = True
                        preview.append(entry)
                inspection["sheets"].append({
                    "name": page.title, "rows": page.max_row, "columns": page.max_column,
                    "preview": preview,
                    "truncated": page.max_row > max_row or page.max_column > max_column,
                })
        finally:
            book.close()
    return {"inspection": inspection}


def profile_table(*, inputs: dict, parameters: dict, random_seed, work_dir: Path) -> dict:
    del random_seed, work_dir
    table = inputs["table"]
    if not isinstance(table, dict) or not table or any(not isinstance(values, list) for values in table.values()):
        raise ValueError("table must contain named list columns")
    lengths = {len(values) for values in table.values()}
    if len(lengths) != 1:
        raise ValueError("table columns must have equal lengths")
    row_count = lengths.pop()
    columns = {}
    for name, values in table.items():
        types = Counter(_value_type(value) for value in values)
        numeric = [value for value in values if _value_type(value) == "number"]
        text = [value for value in values if _value_type(value) == "text"]
        columns[name] = {
            "types": dict(types), "missing_count": types["missing"],
            "mixed_types": len(set(types) - {"missing"}) > 1,
            "unique_count": len({json.dumps(value, sort_keys=True, default=str) for value in values}),
            "numeric_range": [min(numeric), max(numeric)] if numeric else None,
            "text_length_range": [min(map(len, text)), max(map(len, text))] if text else None,
        }
    unique_rows = {json.dumps(row, sort_keys=True, default=str) for row in zip(*table.values())}
    profile = {"row_count": row_count, "columns": columns,
               "duplicate_row_count": row_count - len(unique_rows), "order": None}
    order_by = parameters["order_by"]
    if order_by is not None:
        if order_by not in table:
            raise ValueError(f"unknown order_by column: {order_by}")
        values = [value for value in table[order_by] if _value_type(value) != "missing"]
        kinds = {_value_type(value) for value in values}
        if len(kinds) != 1 or not kinds <= {"number", "date", "text"}:
            direction = "not_comparable"
        else:
            comparable = [datetime.fromisoformat(value) if isinstance(value, str) else value
                          for value in values] if kinds == {"date"} else values
            try:
                ascending = all(left <= right for left, right in zip(comparable, comparable[1:]))
                descending = all(left >= right for left, right in zip(comparable, comparable[1:]))
                direction = "constant" if ascending and descending else "ascending" if ascending else "descending" if descending else "unordered"
            except TypeError:
                direction = "not_comparable"
        profile["order"] = {"column": order_by, "direction": direction,
                            "missing_count": row_count - len(values)}
    return {"profile": profile}
