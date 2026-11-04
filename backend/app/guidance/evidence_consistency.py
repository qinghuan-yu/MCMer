"""Cross-contract checks preventing available evidence from becoming missing."""

from __future__ import annotations

import re

from app.artifacts.evidence_profile import EvidenceProfile
from app.artifacts.input_catalog import InputCatalog
from app.guidance.contracts import ProblemSpec
from app.guidance.indexed_linear import validate_indexed_linear_core
from app.guidance.model_ir import (
    IndexedLinearExecutionComponent,
    ModelIR,
    SolvableModelIR,
)


MISSING_MARKERS = (
    "缺少",
    "未提供",
    "未上传",
    "重新上传",
    "unknown",
    "missing",
    "not provided",
    "unavailable",
)


def validate_problem_evidence_consistency(
    problem_spec: ProblemSpec,
    evidence_profile: dict,
    input_catalog: dict,
) -> None:
    labels = _available_labels(evidence_profile)
    profile = EvidenceProfile.model_validate(evidence_profile)
    catalog = InputCatalog.model_validate(input_catalog)
    registered_source_refs = {
        block.source_ref for block in catalog.blocks
    } | {
        item.source_ref for item in profile.evidence
    }
    for record in [
        *problem_spec.locked_facts,
        *problem_spec.uncertainties,
        *problem_spec.modeling_requirements,
    ]:
        for source_ref in record.source_refs:
            if source_ref not in registered_source_refs:
                raise ValueError(f"unknown registered evidence source_ref: {source_ref}")
    for requirement in problem_spec.source_requirements:
        if requirement.status == "missing" and _matching_label(requirement.reference, labels):
            _raise_available(requirement.reference)
    for uncertainty in problem_spec.uncertainties:
        label = _matching_label(uncertainty.statement, labels)
        if label and _contains_missing_marker(uncertainty.statement):
            _raise_available(label)


def validate_model_evidence_consistency(
    problem_spec: ProblemSpec,
    model_ir: ModelIR,
    evidence_profile: dict,
) -> None:
    labels = _available_labels(evidence_profile)
    for subproblem in model_ir.subproblems:
        if isinstance(subproblem, SolvableModelIR):
            for component in subproblem.execution_components:
                if not isinstance(component, IndexedLinearExecutionComponent):
                    continue
                issues = validate_indexed_linear_core(
                    component.model,
                    locked_facts=problem_spec.locked_facts,
                    evidence_profile=evidence_profile,
                )
                for issue in issues:
                    if issue.code == "indexed_parameter_evidence_mismatch":
                        raise ValueError(f"{issue.code}: {issue.message}")
            continue
        if subproblem.execution_status != "blocked":
            continue
        for missing_input in subproblem.missing_inputs:
            label = _matching_label(missing_input, labels)
            if label:
                _raise_available(label)
        for claim in (subproblem.blocking_reason, subproblem.recovery_action):
            label = _matching_label(claim, labels)
            if label and _contains_missing_marker(claim):
                _raise_available(label)


def _available_labels(payload: dict) -> list[str]:
    profile = EvidenceProfile.model_validate(payload)
    return list(dict.fromkeys(
        label
        for item in profile.evidence
        for label in item.labels
        if label.strip()
    ))


def _matching_label(text: str, labels: list[str]) -> str:
    normalized_text = _normalize(text)
    for label in labels:
        if _normalize(label) in normalized_text:
            return label
    return ""


def _contains_missing_marker(text: str) -> bool:
    lowered = text.casefold()
    return any(marker in lowered for marker in MISSING_MARKERS)


def _normalize(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value or "").casefold()


def _raise_available(label: str) -> None:
    raise ValueError(f"available evidence cannot be declared missing: {label}")
