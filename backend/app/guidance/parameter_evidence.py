"""按确切来源及有序切片验证静态索引参数，不猜测来源或数值位置。"""

from __future__ import annotations

import itertools
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.guidance.model_ir import IndexedLinearCoreSpec


def _payload(value: Any) -> dict:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value)


def _number(value: Any, unit: str = "") -> float:
    if isinstance(value, bool) or value is None:
        raise ValueError("source is not numeric")
    text = str(value).strip()
    percent = text.endswith("%") or "%" in unit
    result = float(text.removesuffix("%"))
    if not math.isfinite(result):
        raise ValueError("source is not finite")
    return result / 100.0 if percent else result


def _table_values(
    reference: str, tables: list[dict], *,
    index_sets: Sequence[str], coordinates: list[tuple],
) -> list[float] | None:
    matches = [
        table for table in tables
        if reference == table["source_ref"]
        or reference.startswith(table["source_ref"] + "/")
    ]
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("ambiguous registered table source")
    table = matches[0]
    rows = table.get("rows") or []
    columns = [str(value).strip() for value in rows[0]] if rows else []
    key_columns = [columns.index(name) for name in index_sets if columns.count(name) == 1]
    has_named_keys = bool(index_sets) and len(key_columns) == len(index_sets)
    suffix = reference[len(table["source_ref"]):].removeprefix("/")
    cell = re.fullmatch(r"rows/(\d+)/cells/(\d+)", suffix)
    if cell:
        # JSON 单元格指针始终是原始表格内的零基坐标，包含表头。
        row = int(cell[1])
        _check_row_key(rows, row, key_columns, has_named_keys, coordinates)
        return [_number(rows[row][int(cell[2])])]
    cell = re.fullmatch(r"r([1-9]\d*)c([1-9]\d*)", suffix)
    if cell:
        # r/c 引用采用一基数据行；表头是否存在只按整行结构确定。
        first_row_is_header = rows and all(
            isinstance(value, str) and not _is_number(value) for value in rows[0]
        )
        row = int(cell[1]) - 1 + int(bool(first_row_is_header))
        _check_row_key(rows, row, key_columns, has_named_keys, coordinates)
        return [_number(rows[row][int(cell[2]) - 1])]
    if not rows or not suffix:
        raise ValueError("table source requires an explicit column or cell")
    if columns.count(suffix) != 1:
        raise ValueError(f"unknown or ambiguous evidence column {suffix!r}")
    column = columns.index(suffix)
    if has_named_keys:
        source = {
            tuple(str(row[index]).strip() for index in key_columns): _number(row[column])
            for row in rows[1:]
        }
        if len(source) != len(rows) - 1:
            raise ValueError("duplicate evidence index coordinates")
        return [source[tuple(str(value).strip() for value in key)] for key in coordinates]
    return [_number(row[column]) for row in rows[1:]]


def _check_row_key(rows, row, key_columns, has_named_keys, coordinates):
    source = tuple(str(rows[row][index]).strip() for index in key_columns)
    expected = tuple(str(value).strip() for value in coordinates[0])
    if has_named_keys and source != expected:
        raise ValueError("source cell index labels disagree with parameter coordinates")


def _is_number(value: Any) -> bool:
    try:
        _number(value)
        return True
    except (ValueError, TypeError):
        return False


def _fact_values(reference: str, facts: list[dict]) -> list[float]:
    matches = [
        fact for fact in facts
        if reference == f"problem_spec.json#locked_facts/{fact.get('fact_id')}"
        or reference in fact.get("source_refs", [])
    ]
    numbers = [_number(fact["value"], str(fact.get("unit") or "")) for fact in matches]
    if len(set(numbers)) != 1:
        raise ValueError("static source must resolve to one exact scalar fact or table slice")
    return [numbers[0]]


def validate_static_parameter_evidence(
    core: IndexedLinearCoreSpec,
    *,
    locked_facts: Sequence[Any] | None,
    evidence_profile: Any | None,
) -> list[str]:
    """验证原始模型；runtime_source 参数在物化前仅做结构检查。"""
    if locked_facts is None and evidence_profile is None:
        return []
    facts = {_payload(fact)["fact_id"]: _payload(fact) for fact in locked_facts or []}
    profile = _payload(evidence_profile) if evidence_profile is not None else {}
    tables = [item for item in profile.get("evidence", []) if isinstance(item, Mapping)]
    domains = {item.id: item.values for item in core.index_sets}
    errors: list[str] = []
    for parameter in core.parameters:
        if parameter.runtime_source is not None:
            continue
        bound = [facts[fact_id] for fact_id in parameter.source_fact_ids if fact_id in facts]
        try:
            if parameter.dense_values:
                reference = parameter.dense_source_ref or ""
                coordinates = list(itertools.product(*(domains[index] for index in parameter.index_sets)))
                expected = _table_values(
                    reference, tables, index_sets=parameter.index_sets, coordinates=coordinates,
                )
                if expected is None:
                    expected = _fact_values(reference, bound)
                    if not parameter.index_sets:
                        expected = expected[:1]
                    else:
                        count = math.prod(len(domains[index]) for index in parameter.index_sets)
                        expected = expected * count
                actual = parameter.dense_values
            else:
                expected, actual = [], []
                for item in parameter.values:
                    values = _table_values(
                        item.source_ref, tables, index_sets=parameter.index_sets,
                        coordinates=[tuple(item.coordinates)],
                    )
                    if values is None:
                        values = _fact_values(item.source_ref, bound)
                    if len(values) != 1:
                        raise ValueError("coordinate values require an explicit scalar source")
                    expected.extend(values)
                    actual.append(item.value)
            if len(actual) != len(expected) or any(
                not math.isclose(value, source, rel_tol=1e-9, abs_tol=1e-9)
                for value, source in zip(actual, expected)
            ):
                raise ValueError(f"values {actual!r} disagree with ordered source {expected!r}")
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            errors.append(f"parameter {parameter.id!r}: {exc}")
    return errors
