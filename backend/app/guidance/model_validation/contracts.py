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
)


ValidationStatus = Literal["passed", "failed", "not_assessed"]

class ValidationCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ValidationStatus
    reason: str

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
