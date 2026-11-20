"""model_validation: contracts 领域实现与合同。"""

from __future__ import annotations

import math
from collections.abc import (
    Callable,
    Iterable,
    Mapping,
)
from typing import (
    Any,
    Literal,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    PrivateAttr,
)


ValidationStatus = Literal["passed", "failed", "not_assessed"]


class BaselineMeasurement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    baseline_kind: str = Field(min_length=1)
    metric: str = Field(min_length=1)
    baseline_value: FiniteFloat
    result_value: FiniteFloat


class ValidationCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ValidationStatus
    reason: str
    baseline_measurements: list[BaselineMeasurement] = Field(default_factory=list)
    _source_node_id: int | None = PrivateAttr(default=None)

    def record_comparison(
        self, source: Mapping[str, Any], kind: str, metric: str, baseline: float, result: float,
    ) -> ValidationCheck:
        # 对象标识仅在本次复核中定位原始结果，写入持久记录的是对应的 result_registry 引用。
        self._source_node_id = id(source)
        self.baseline_measurements.append(BaselineMeasurement(
            baseline_kind=kind, metric=metric, baseline_value=baseline, result_value=result,
        ))
        return self

    def comparison_source(self, sources: dict[int, str]) -> str:
        if self._source_node_id not in sources:
            raise ValueError("baseline measurement has no registered source")
        return sources[self._source_node_id]


class ModelValidationAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ValidationStatus
    checks: dict[str, ValidationCheck] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)

Validator = Callable[[list[Mapping[str, Any]]], ValidationCheck]

def walk_mappings(values: Iterable[Any]) -> Iterable[Mapping[str, Any]]:
    for value in values:
        if isinstance(value, Mapping):
            yield value
            yield from walk_mappings(value.values())
        elif isinstance(value, list):
            yield from walk_mappings(value)

def find_evidence(nodes: list[Mapping[str, Any]], *keys: str) -> Mapping[str, Any] | None:
    required = set(keys)
    return next((node for node in nodes if required.issubset(node)), None)

def passed_check(reason: str) -> ValidationCheck:
    return ValidationCheck(status="passed", reason=reason)

def failed(reason: str) -> ValidationCheck:
    return ValidationCheck(status="failed", reason=reason)

def not_assessed(reason: str) -> ValidationCheck:
    return ValidationCheck(status="not_assessed", reason=reason)

def finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
