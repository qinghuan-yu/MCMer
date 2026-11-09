"""Deterministic validation for initial and revision ModelIR reviews."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel

from app.artifacts.evidence_profile import EvidenceProfile
from app.artifacts.input_catalog import InputCatalog
from app.guidance.contracts import ProblemSpec, VerificationLayer
from app.guidance.model_ir import (BlockedModelIR, ModelIR, SolvableModelIR)
from app.guidance.model_review_contracts import (
    ApprovedModelIR, ModelIRReview, ModelReviewBinding, SubproblemReviewSpec,
)
from app.guidance.source_resolution import SourceResolutionRecord, resolve_problem_sources


def validate_model_review(
    review: ModelIRReview,
    model_ir: ModelIR,
    *,
    revision_request: ModelIRReview | None = None,
    problem_spec: ProblemSpec | None = None,
    source_resolution: SourceResolutionRecord | None = None,
    input_catalog: dict | None = None,
    evidence_profile: dict | None = None,
    work_dir: Path | None = None,
) -> None:
    if review.model_id != model_ir.model_id:
        raise ValueError("model review model_id does not match Model IR")
    expected_subproblems = [
        subproblem.subproblem_id for subproblem in model_ir.subproblems
    ]
    reviewed_subproblems = [
        item.subproblem_id for item in review.subproblem_reviews
    ]
    if reviewed_subproblems != expected_subproblems:
        raise ValueError(
            "subproblem review coverage mismatch: "
            f"expected={expected_subproblems!r}; reviewed={reviewed_subproblems!r}"
        )
    requested_by_id = {}
    if revision_request is not None:
        if revision_request.model_id != model_ir.model_id:
            raise ValueError("revision request model_id does not match Model IR")
        if revision_request.status != "revision_required":
            raise ValueError("revision request must have revision_required status")
        requested_subproblems = [item.subproblem_id for item in revision_request.subproblem_reviews]
        if requested_subproblems != expected_subproblems:
            raise ValueError(
                "revision request subproblem coverage mismatch: "
                f"expected={expected_subproblems!r}; requested={requested_subproblems!r}"
            )
        requested_by_id = {item.subproblem_id: item for item in revision_request.subproblem_reviews}
    for decision, subproblem in zip(review.subproblem_reviews, model_ir.subproblems):
        validate_subproblem_review(
            decision, subproblem,
            revision_request=requested_by_id.get(decision.subproblem_id),
            problem_spec=problem_spec, source_resolution=source_resolution,
            input_catalog=input_catalog, evidence_profile=evidence_profile, work_dir=work_dir,
        )
    proposals = [(decision.subproblem_id, correction)
                 for decision in review.subproblem_reviews for correction in decision.source_corrections]
    if proposals:
        # 单题可接受的纠正合并后仍须一致，不能按题目顺序覆盖共享事实。
        resolve_problem_sources(problem_spec, proposals, input_catalog=input_catalog,
                                evidence_profile=evidence_profile or {"evidence": []}, work_dir=work_dir)


def validate_subproblem_review(
    decision: SubproblemReviewSpec,
    subproblem: BlockedModelIR | SolvableModelIR,
    *,
    revision_request: SubproblemReviewSpec | None = None,
    problem_spec: ProblemSpec | None = None,
    source_resolution: SourceResolutionRecord | None = None,
    input_catalog: dict | None = None,
    evidence_profile: dict | None = None,
    work_dir: Path | None = None,
) -> None:
    if decision.subproblem_id != subproblem.subproblem_id:
        raise ValueError("subproblem review identity mismatch")
    if revision_request is not None and revision_request.subproblem_id != subproblem.subproblem_id:
        raise ValueError("revision request subproblem identity mismatch")
    if decision.assessment_status == "not_assessed":
        return
    if decision.source_corrections:
        if problem_spec is None:
            raise ValueError("source corrections require the current ProblemSpec")
        resolve_problem_sources(
            problem_spec, [(decision.subproblem_id, item) for item in decision.source_corrections],
            input_catalog=input_catalog, evidence_profile=evidence_profile or {"evidence": []}, work_dir=work_dir,
        )
    _validate_requirement_basis(decision, problem_spec)
    _validate_mathematical_checks(decision, subproblem, problem_spec)
    if isinstance(subproblem, BlockedModelIR) and decision.status != "blocked":
        raise ValueError(
            f"blocked Model IR subproblem {decision.subproblem_id!r} "
            "must receive a blocked review"
        )
    expected = (
        [assumption.id for assumption in subproblem.assumptions]
        if isinstance(subproblem, SolvableModelIR) else []
    )
    reviewed = [item.assumption_id for item in decision.assumption_checks]
    if reviewed != expected:
        raise ValueError(
            f"assumption review coverage mismatch for {decision.subproblem_id!r}: "
            f"expected={expected!r}; reviewed={reviewed!r}"
        )
    for check in decision.assumption_checks:
        if check.status != "accepted" and decision.status == "approved":
            raise ValueError(
                "approved subproblem review contains an unaccepted assumption: "
                f"{decision.subproblem_id!r}/{check.assumption_id!r}"
            )
    if revision_request is None:
        if decision.revision_checks:
            raise ValueError("initial review cannot contain revision_checks")
        return
    expected_revisions = [item.requirement_id for item in revision_request.required_revisions]
    reviewed_revisions = [item.requirement_id for item in decision.revision_checks]
    if reviewed_revisions != expected_revisions:
        raise ValueError(
            "revision review coverage mismatch for "
            f"{decision.subproblem_id!r}: expected={expected_revisions!r}; "
            f"reviewed={reviewed_revisions!r}"
        )
    document = subproblem.model_dump(mode="json")
    requirements_by_id = {item.requirement_id: item for item in revision_request.required_revisions}
    for check in decision.revision_checks:
        requirement = requirements_by_id[check.requirement_id]
        if check.status == "withdrawn":
            _validate_source_withdrawal(check, requirement, decision.subproblem_id, problem_spec, source_resolution)
        elif requirement.kind == "source" and check.status == "accepted":
            raise ValueError("source revision must be resolved and withdrawn, not accepted as a model repair")
        for path in check.model_paths:
            if not _json_pointer_exists(document, path):
                raise ValueError(
                    f"revision evidence path {path!r} does not exist in subproblem {subproblem.subproblem_id!r}"
                )
    if decision.status == "approved":
        unaccepted_revisions = [item.requirement_id for item in decision.revision_checks if item.status not in {"accepted", "withdrawn"}]
        if unaccepted_revisions:
            raise ValueError(
                "approved subproblem review contains unaccepted revision "
                f"requirements: {unaccepted_revisions!r}"
            )


def unresolved_source_subproblem_ids(
    review: ModelIRReview,
    problem_spec: ProblemSpec,
    source_resolution: SourceResolutionRecord | None = None,
) -> set[str]:
    resolved = {
        f"problem_spec.json#{'uncertainties' if item.target_kind == 'uncertainty' else 'locked_facts'}/{item.target_id}"
        for item in source_resolution.corrections
    } if source_resolution is not None else set()
    scopes = {
        **{f"problem_spec.json#locked_facts/{item.fact_id}": item.subproblem_ids for item in problem_spec.locked_facts},
        **{f"problem_spec.json#uncertainties/{item.uncertainty_id}": item.subproblem_ids for item in problem_spec.uncertainties},
    }
    pending = {
        ref for decision in review.subproblem_reviews
        for requirement in decision.required_revisions if requirement.kind == "source"
        for ref in requirement.basis_refs
    } | {
        f"problem_spec.json#{'uncertainties' if item.target_kind == 'uncertainty' else 'locked_facts'}/{item.target_id}"
        for decision in review.subproblem_reviews for item in decision.source_corrections
    }
    affected = set()
    for reference in pending - resolved:
        if reference not in scopes:
            raise ValueError(f"pending source obligation targets an unknown problem record: {reference}")
        affected.update(scopes[reference])
    while True:
        expanded = affected | {
            item.id for item in problem_spec.subproblems
            if affected.intersection(item.depends_on_subproblem_ids)
        }
        if expanded == affected:
            return affected
        affected = expanded


def _validate_requirement_basis(decision: SubproblemReviewSpec, problem_spec: ProblemSpec | None) -> None:
    if not any(item.basis_refs for item in decision.required_revisions):
        return
    if problem_spec is None:
        raise ValueError("revision basis requires the current ProblemSpec")
    scopes = {
        **{f"problem_spec.json#locked_facts/{item.fact_id}": item.subproblem_ids for item in problem_spec.locked_facts},
        **{f"problem_spec.json#uncertainties/{item.uncertainty_id}": item.subproblem_ids for item in problem_spec.uncertainties},
    }
    for requirement in decision.required_revisions:
        for ref in requirement.basis_refs:
            if decision.subproblem_id not in scopes.get(ref, []):
                raise ValueError(f"revision basis is unknown or outside the subproblem scope: {ref!r}")


def _validate_source_withdrawal(check, requirement, target_id, problem_spec, source_resolution) -> None:
    if requirement.kind != "source":
        raise ValueError("a model defect cannot be withdrawn through source correction")
    if source_resolution is None:
        raise ValueError("withdrawal requires an applied source resolution")
    if problem_spec is None or _review_digest(problem_spec) != source_resolution.resolved_problem_spec_sha256:
        raise ValueError("applied source resolution does not match the current ProblemSpec")
    corrections = {item.correction_id: item for item in source_resolution.corrections}
    unknown = set(check.source_correction_ids) - corrections.keys()
    if unknown:
        raise ValueError(f"unknown source correction evidence: {sorted(unknown)!r}")
    basis = set()
    for identifier in check.source_correction_ids:
        correction = corrections[identifier]
        if target_id not in correction.replacement_fact.subproblem_ids:
            raise ValueError("source withdrawal evidence is outside the subproblem scope")
        group = "uncertainties" if correction.target_kind == "uncertainty" else "locked_facts"
        basis.add(f"problem_spec.json#{group}/{correction.target_id}")
    if basis != set(requirement.basis_refs):
        raise ValueError("source withdrawal must resolve exactly all disputed basis records")


def mathematical_requirement_ids(problem_spec: ProblemSpec, subproblem_id: str) -> list[str]:
    subproblem = next((item for item in problem_spec.subproblems if item.id == subproblem_id), None)
    if subproblem is None:
        raise ValueError(f"mathematical review references unknown subproblem {subproblem_id!r}")
    return [*subproblem.required_fact_ids, "$objective", "$formulation"]


def _validate_mathematical_checks(decision, subproblem, problem_spec: ProblemSpec | None) -> None:
    if not decision.mathematical_checks:
        return
    if problem_spec is None:
        raise ValueError("mathematical correspondence requires a ProblemSpec")
    expected = set(mathematical_requirement_ids(problem_spec, subproblem.subproblem_id))
    unknown = {check.requirement_id for check in decision.mathematical_checks} - expected
    if unknown:
        raise ValueError(f"unknown mathematical requirements: {sorted(unknown)!r}")
    document = subproblem.model_dump(mode="json")
    for check in decision.mathematical_checks:
        for path in check.model_paths:
            if not _json_pointer_exists(document, path):
                raise ValueError(f"mathematical evidence path does not exist: {path!r}")


def _review_digest(value: BaseModel | dict) -> str:
    document = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    serialized = json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def load_review_catalog(root: Path) -> dict | None:
    path = root / "input_catalog.json"
    if not path.is_file():
        return None
    return InputCatalog.model_validate_json(path.read_text(encoding="utf-8")).model_dump(mode="json")


def bind_model_review(
    *, review: ModelIRReview, model_ir: ModelIR, approved_model_ir: ModelIR,
    problem_spec: ProblemSpec, evidence_profile: dict, input_catalog: dict | None,
    revision_request: ModelIRReview | None,
    source_resolution: SourceResolutionRecord | None = None,
) -> ModelReviewBinding:
    # 绑定由系统写入，LLM不能自报审核所用快照的哈希。
    return ModelReviewBinding(
        model_sha256=_review_digest(model_ir),
        approved_model_sha256=_review_digest(approved_model_ir),
        problem_spec_sha256=_review_digest(problem_spec),
        evidence_profile_sha256=_review_digest(evidence_profile),
        input_catalog_sha256=_review_digest(input_catalog) if input_catalog is not None else None,
        review_sha256=_review_digest(review),
        revision_request_sha256=_review_digest(revision_request) if revision_request is not None else None,
        source_resolution_sha256=_review_digest(source_resolution) if source_resolution is not None else None,
    )


def assess_mathematical_review(
    approved: ApprovedModelIR, *, root: Path, problem_spec: ProblemSpec | None,
) -> VerificationLayer:
    if approved.review_binding is None or problem_spec is None:
        return VerificationLayer(status="not_assessed", reasons=["no mathematical review bound to the current model and sources was registered"])
    try:
        from app.guidance.recovery import load_source_resolution

        source_resolution = load_source_resolution(root, problem_spec)
        review = ModelIRReview.model_validate_json((root / approved.review_ref).read_text(encoding="utf-8"))
        model_ir = ModelIR.model_validate_json((root / approved.model_ir_ref).read_text(encoding="utf-8"))
        revision_request = (
            ModelIRReview.model_validate_json((root / approved.revision_request_ref).read_text(encoding="utf-8"))
            if approved.revision_request_ref is not None else None
        )
        current_binding = bind_model_review(
            review=review, model_ir=model_ir, approved_model_ir=approved.approved_model_ir,
            problem_spec=problem_spec,
            evidence_profile=EvidenceProfile.model_validate_json(
                (root / "evidence_profile.json").read_text(encoding="utf-8")
            ).model_dump(mode="json"),
            input_catalog=load_review_catalog(root), revision_request=revision_request,
            source_resolution=source_resolution,
        )
    except (OSError, ValueError) as exc:
        return VerificationLayer(status="not_assessed", reasons=[f"mathematical review evidence is unavailable or invalid: {exc}"])
    if current_binding != approved.review_binding:
        return VerificationLayer(status="not_assessed", reasons=["mathematical review snapshot is stale: model, source, review or revision evidence changed"])
    try:
        validate_model_review(
            review, model_ir, problem_spec=problem_spec, revision_request=revision_request,
            source_resolution=source_resolution, input_catalog=load_review_catalog(root),
            evidence_profile=EvidenceProfile.model_validate_json(
                (root / "evidence_profile.json").read_text(encoding="utf-8")
            ).model_dump(mode="json"), work_dir=root,
        )
    except ValueError as exc:
        return VerificationLayer(status="failed", reasons=[str(exc)], evidence_refs=[approved.review_ref])
    reasons: list[str] = []
    refs: list[str] = []
    statuses: list[str] = []
    for index, decision in enumerate(review.subproblem_reviews):
        if decision.assessment_status == "not_assessed":
            statuses.append("not_assessed")
            reasons.append(f"{decision.subproblem_id}: mathematical review was not assessed because no valid review contract was available")
            refs.append(f"{approved.review_ref}#/subproblem_reviews/{index}")
            continue
        if decision.subproblem_id not in approved.executable_subproblem_ids:
            continue
        expected = set(mathematical_requirement_ids(problem_spec, decision.subproblem_id))
        checks = {item.requirement_id: item for item in decision.mathematical_checks}
        if decision.status != "approved" or any(item.status == "failed" for item in checks.values()):
            statuses.append("failed")
            reasons.append(f"{decision.subproblem_id}: independent mathematical review did not accept the model")
        elif expected - checks.keys() or any(item.status != "passed" for item in checks.values()):
            statuses.append("not_assessed")
            reasons.append(f"{decision.subproblem_id}: mathematical correspondence is incomplete or unassessed")
        else:
            statuses.append("passed")
            reasons.append(f"{decision.subproblem_id}: independent reviewer accepted {len(expected)} mathematical requirements under the bound source snapshots")
        refs.append(f"{approved.review_ref}#/subproblem_reviews/{index}/mathematical_checks")
    status = "failed" if "failed" in statuses else "passed" if statuses and all(item == "passed" for item in statuses) else "not_assessed"
    return VerificationLayer(
        status=status, reasons=reasons or ["no executable subproblem has mathematical review evidence"], evidence_refs=refs,
    )


def _json_pointer_exists(document: object, pointer: str) -> bool:
    current = document
    for raw_token in pointer.split("/")[1:]:
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                return False
            current = current[token]
            continue
        if isinstance(current, list):
            if not token.isdigit():
                return False
            index = int(token)
            if index >= len(current):
                return False
            current = current[index]
            continue
        return False
    return True
