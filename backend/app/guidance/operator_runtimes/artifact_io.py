"""operator_runtimes: artifact_io 领域实现与合同。"""

from __future__ import annotations

from pathlib import (
    Path,
    PurePosixPath,
)
from typing import Any

import numpy as np
import pandas as pd


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
    fields = parameters.get("fields")
    if fields is not None:
        if not isinstance(fields, dict) or not fields:
            raise ValueError("mapping projection fields must be a non-empty mapping")
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
    indices = parameters.get("indices")
    if indices is not None:
        if not isinstance(value, list) or not isinstance(indices, list) or not indices:
            raise ValueError("indexed scalar projection requires a non-empty list")
        selected = []
        for index in indices:
            if not isinstance(index, int) or index < 0 or index >= len(value):
                raise ValueError("scalar projection index is out of bounds")
            selected.append(value[index])
        value = selected
    reducer = parameters.get("reducer")
    if reducer == "sum":
        value = sum(value)
    elif reducer is not None:
        raise ValueError(f"unsupported scalar reducer: {reducer}")
    if not isinstance(value, (int, float, str, bool)):
        raise ValueError("selected artifact value is not scalar")
    return {"value": value}

def extract_vector(*, inputs: dict, parameters: dict, random_seed, work_dir: Path) -> dict:
    del random_seed, work_dir
    value = _path_value(inputs["artifact"], parameters["path"])
    if not isinstance(value, list):
        raise ValueError("selected artifact value is not a vector")
    indices = parameters.get("indices")
    if indices is not None:
        if not isinstance(indices, list) or not indices:
            raise ValueError("vector projection indices must be a non-empty list")
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
    frame = pd.read_excel(
        workspace_path(inputs["source"], work_dir),
        sheet_name=parameters.get("sheet", 0),
    )
    row_limit = parameters.get("row_limit")
    if row_limit is not None:
        if not isinstance(row_limit, int) or isinstance(row_limit, bool) or row_limit <= 0:
            raise ValueError("row_limit must be a positive integer")
        frame = frame.iloc[:row_limit].copy()
    columns = parameters.get("columns")
    if columns:
        frame = frame.loc[:, columns]
    frame = frame.rename(columns=parameters.get("rename", {}))
    for column in parameters.get("forward_fill", []):
        frame[column] = frame[column].ffill()
    table = {str(column): frame[column].tolist() for column in frame.columns}
    matrix_columns = parameters.get("matrix_columns")
    if matrix_columns:
        matrix_name = str(parameters.get("matrix_name", "matrix"))
        table[matrix_name] = frame.loc[:, matrix_columns].to_numpy(dtype=float).tolist()
    return {"table": table}
