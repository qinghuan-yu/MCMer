"""Cross-contract checks preventing available evidence from becoming missing."""

from __future__ import annotations

import re
from collections import Counter
import hashlib
from pathlib import Path

from app.artifacts.evidence_profile import EvidenceProfile
from app.artifacts.input_catalog import InputCatalog
from app.guidance.contracts import ProblemSpec
from app.guidance.indexed_linear import validate_indexed_linear_core
from app.guidance.model_ir import (
    IndexedLinearExecutionComponent,
    ModelIR,
    OperatorGraphExecutionComponent,
    SolvableModelIR,
)
from app.guidance.recovery import canonical_digest, workspace_file


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


def registered_result_source_ids(
    root: Path, model_ir: ModelIR, *, referenced_ids: dict[str, set[str]],
) -> tuple[dict[str, set[str]], list[str]]:
    """结果来源须同时属于正式登记、当前子题的数据声明和真实原件。"""
    path = root / "evidence_profile.json"
    if not path.is_file():
        return {}, []
    try:
        profile = EvidenceProfile.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, [f"invalid result source registration: {exc}"]
    counts = Counter(item.evidence_id for item in profile.evidence)
    node_ids = {
        node_id for subproblem in model_ir.subproblems if isinstance(subproblem, SolvableModelIR)
        for component in subproblem.execution_components
        for node_id in ([node.node_id for node in component.operators]
                        if isinstance(component, OperatorGraphExecutionComponent) else [component.solver_node_id])
    }
    registered: dict[str, set[str]] = {}
    issues: list[str] = []
    for subproblem in model_ir.subproblems:
        if not isinstance(subproblem, SolvableModelIR):
            continue
        allowed = registered.setdefault(subproblem.subproblem_id, set())
        requested = {
            identifier for answer in subproblem.final_answers for identifier in answer.evidence_ids
        } & referenced_ids.get(subproblem.subproblem_id, set())
        declared = {item.source_ref for item in subproblem.data}
        for item in profile.evidence:
            if item.evidence_id not in requested:
                continue
            if counts[item.evidence_id] != 1 or item.evidence_id in node_ids:
                issues.append(f"ambiguous source/node evidence ID: {item.evidence_id}")
                continue
            if item.source_ref not in declared:
                continue
            try:
                if item.source_ref.split("#", 1)[0] != item.source_file:
                    raise ValueError("source_ref does not belong to source_file")
                source_hash = hashlib.sha256(workspace_file(root, item.source_file).read_bytes()).hexdigest()
                if item.kind == "embedded_table":
                    catalog = InputCatalog.model_validate_json((root / "input_catalog.json").read_text(encoding="utf-8"))
                    blocks = [block for block in catalog.blocks if block.source_ref == item.source_ref and block.kind == "table"]
                    if (catalog.source_file != item.source_file or catalog.source_sha256 != source_hash
                            or len(blocks) != 1 or blocks[0].rows != item.rows
                            or blocks[0].sha256 != item.sha256
                            or item.sha256 != canonical_digest({"kind": "table", "rows": item.rows})):
                        raise ValueError("table registration does not match the source catalog")
                elif item.sha256 != source_hash:
                    raise ValueError("registered file hash does not match the source")
            except (OSError, ValueError) as exc:
                issues.append(f"source evidence {item.evidence_id} is unavailable or inconsistent: {exc}")
                continue
            allowed.add(item.evidence_id)
    return registered, issues


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
    for record in [*problem_spec.locked_facts, *problem_spec.uncertainties]:
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
