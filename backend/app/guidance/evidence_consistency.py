"""Cross-contract checks preventing available evidence from becoming missing."""

from __future__ import annotations

import re
from collections import Counter
import hashlib
from pathlib import Path

from app.artifacts.evidence_profile import EvidenceItem, EvidenceProfile
from app.artifacts.input_catalog import InputCatalog
from app.guidance.contracts import ProblemSpec
from app.guidance.indexed_linear import validate_indexed_linear_core
from app.guidance.model_ir import (
    IndexedLinearExecutionComponent,
    ModelIR,
    SolvableModelIR,
)
from app.guidance.recovery import canonical_digest, workspace_file
from app.guidance.output_references import split_output_reference


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


class InvalidAnswerEvidence(ValueError):
    def __init__(self, invalid: dict[str, list[str]], available: set[str]):
        self.invalid = invalid
        self.available = sorted(available)
        super().__init__(
            f"final_answers.evidence_ids contains unknown or ambiguous references: {invalid!r}. "
            f"Use planned execution node IDs or registered source IDs declared by this subproblem: {self.available!r}."
        )


class InvalidAnswerValue(ValueError):
    pass


def _ambiguous_evidence_ids(model_ir: ModelIR, profile: EvidenceProfile) -> set[str]:
    counts = Counter(item.evidence_id for item in profile.evidence)
    node_ids = {node for sub in model_ir.subproblems if isinstance(sub, SolvableModelIR) for node in sub.execution_node_ids}
    return {identifier for identifier, count in counts.items() if count > 1 or identifier in node_ids}


def _declared_result_sources(sub: SolvableModelIR, profile: EvidenceProfile, ambiguous: set[str]) -> list[EvidenceItem]:
    declared = {data.source_ref for data in sub.data}
    return [item for item in profile.evidence if item.source_ref in declared and item.evidence_id not in ambiguous]


def validate_planned_answer_evidence(model_ir: ModelIR, evidence_profile: dict) -> None:
    """提交时只检查声明的引用。执行后仍须查验实际节点、来源字节和登记快照。"""
    profile = EvidenceProfile.model_validate(evidence_profile)
    ambiguous = _ambiguous_evidence_ids(model_ir, profile)
    for sub in model_ir.subproblems:
        if not isinstance(sub, SolvableModelIR):
            continue
        available = set(sub.execution_node_ids) - ambiguous
        available.update(item.evidence_id for item in _declared_result_sources(sub, profile, ambiguous)
                         if item.source_ref.split("#", 1)[0] == item.source_file)
        invalid = {}
        for answer in sub.final_answers:
            unknown = set(answer.evidence_ids) - available
            if unknown:
                invalid[answer.id] = sorted(unknown)
        if invalid:
            raise InvalidAnswerEvidence(invalid, available)
        outputs = sub.output_producers
        for answer in sub.final_answers:
            try:
                split_output_reference(answer.value_ref, outputs)
            except ValueError as exc:
                raise InvalidAnswerValue(f"{answer.id}: {exc}; available outputs: {sorted(outputs)}") from exc


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
    ambiguous = _ambiguous_evidence_ids(model_ir, profile)
    registered: dict[str, set[str]] = {}
    issues: list[str] = []
    for subproblem in model_ir.subproblems:
        if not isinstance(subproblem, SolvableModelIR):
            continue
        allowed = registered.setdefault(subproblem.subproblem_id, set())
        requested = {
            identifier for answer in subproblem.final_answers for identifier in answer.evidence_ids
        } & referenced_ids.get(subproblem.subproblem_id, set())
        issues.extend(f"ambiguous source/node evidence ID: {identifier}" for identifier in sorted(ambiguous & requested))
        for item in _declared_result_sources(subproblem, profile, ambiguous):
            if item.evidence_id not in requested:
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
