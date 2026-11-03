"""model_validation: robustness 领域实现与合同。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.guidance.model_validation.contracts import (
    ValidationCheck,
    failed,
    find_evidence,
    finite_number,
    not_assessed,
    passed_check,
)


def sensitivity(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    node = find_evidence(nodes, "sensitivity")
    if node is None:
        return not_assessed("sensitivity evidence was not recorded")
    evidence = node["sensitivity"]
    if not isinstance(evidence, Mapping):
        return failed("sensitivity evidence must be a mapping")
    change = evidence.get("max_relative_change")
    threshold = evidence.get("threshold")
    if not finite_number(change) or not finite_number(threshold):
        return failed("sensitivity evidence is incomplete or non-finite")
    if change > threshold:
        return failed("sensitivity change exceeds its declared threshold")
    return passed_check("sensitivity change remains within its declared threshold")

def extreme_inputs(nodes: list[Mapping[str, Any]]) -> ValidationCheck:
    node = find_evidence(nodes, "extreme_inputs")
    if node is None:
        return not_assessed("extreme-input evidence was not recorded")
    evidence = node["extreme_inputs"]
    if not isinstance(evidence, Mapping):
        return failed("extreme-input evidence must be a mapping")
    if evidence.get("finite") is not True:
        return failed("extreme-input execution produced non-finite output")
    if evidence.get("constraints_satisfied") is not True:
        return failed("extreme-input execution violates model constraints")
    return passed_check("extreme-input outputs remain finite and feasible")
