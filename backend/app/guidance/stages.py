"""Guidance 职责边界：stages。"""

from __future__ import annotations

import re
from pathlib import Path

from app.artifacts.evidence_profile import (
    EvidenceProfile,
    build_evidence_profile,
    evidence_matches_reference,
    load_evidence_profile,
)
from app.artifacts.input_catalog import InputCatalog
from app.guidance.contracts import ProblemSpec
from app.guidance.evidence_consistency import (
    validate_model_evidence_consistency,
    validate_problem_evidence_consistency,
)
from app.guidance.model_ir import (
    BlockedModelIR,
    ModelIR,
    SolvableModelIR,
)
from app.guidance.model_ir_compiler import ModelIRCompiler
from app.guidance.model_ir_semantics import (
    ModelIRSemanticValidator,
    validate_model_fact_coverage,
)
from app.guidance.model_review_contracts import (
    ApprovedModelIR,
    ModelIRReview,
    ModelReviewSubmission,
)
from app.guidance.model_review_validation import (
    bind_model_review,
    load_review_catalog,
    unresolved_source_subproblem_ids,
    validate_model_review,
)
from app.guidance.operator_execution import OperatorGraphExecutor
from app.guidance.recovery import (
    canonical_digest,
    invalidate_stage_outputs,
    load_source_preflight,
)
from app.guidance.stage_contracts import (
    ModelReviewer,
    Modeler,
    ProblemDecomposer,
)
from app.guidance.stage_io import (
    _required_input,
    _write_model,
)


class DecompositionStage:
    def __init__(self, *, decomposer: ProblemDecomposer) -> None:
        self.decomposer = decomposer

    async def run(
        self,
        *,
        task_id: str,
        task: dict,
        input_path: Path | None,
        output_path: Path,
    ) -> Path:
        root = output_path.parent
        question = str(task.get("source_question") or task.get("question") or "").strip()
        evidence_profile = build_evidence_profile(root).model_dump(mode="json")
        input_catalog = InputCatalog.model_validate_json(
            (root / "input_catalog.json").read_text(encoding="utf-8")
        ).model_dump(mode="json")
        problem_spec = await self.decomposer.decompose(
            question=question,
            evidence_profile=evidence_profile,
            input_catalog=input_catalog,
            task=task,
        )
        problem_spec = _reconcile_source_requirements(
            problem_spec,
            question=question,
            evidence_profile=evidence_profile,
        )
        validate_problem_evidence_consistency(
            problem_spec,
            evidence_profile,
            input_catalog,
        )
        _write_model(output_path, problem_spec)
        return output_path


class SourceStage:
    """一次性解释并独立复核登记来源，保存原题意后提交许可和已裁决题意。"""

    def __init__(self, *, source_adjudicator) -> None:
        self.source_adjudicator = source_adjudicator

    async def run(self, *, task_id: str, task: dict, input_path: Path | None, output_path: Path) -> Path:
        from app.guidance.source_resolution import validate_source_preflight

        source_path = _required_input(input_path)
        root = output_path.parent
        permission_path = root / "source_preflight.json"
        if permission_path.exists():
            raise ValueError("source preflight can only be applied once before modeling")
        original_bytes = source_path.read_bytes()
        problem_spec = ProblemSpec.model_validate_json(original_bytes)
        evidence_profile = load_evidence_profile(root).model_dump(mode="json")
        input_catalog = load_review_catalog(root)
        resolved, record = await self.source_adjudicator.prepare_sources(
            problem_spec=problem_spec, evidence_profile=evidence_profile,
            input_catalog=input_catalog, work_dir=root,
        )
        validate_source_preflight(record, problem_spec=resolved, input_catalog=input_catalog,
                                  evidence_profile=evidence_profile, work_dir=root)
        if record.original_problem_spec != ProblemSpec.model_validate_json(original_bytes):
            raise ValueError("source preflight replaced the original problem snapshot")
        if source_path.read_bytes() != original_bytes:
            raise ValueError("source preflight input changed while awaiting its assessment")
        # 排他创建是应用的提交点；跨协程或进程也不能覆盖已落盘的许可。
        try:
            with permission_path.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(record.model_dump_json(indent=2))
        except FileExistsError as exc:
            raise ValueError("source preflight can only be applied once before modeling") from exc
        (root / "problem_spec_original.json").write_bytes(original_bytes)
        _write_model(output_path, resolved)
        return output_path


class ModelingStage:
    def __init__(self, *, modeler: Modeler) -> None:
        self.modeler = modeler

    async def run(
        self,
        *,
        task_id: str,
        task: dict,
        input_path: Path | None,
        output_path: Path,
    ) -> Path:
        source_path = _required_input(input_path)
        evidence_profile = load_evidence_profile(output_path.parent).model_dump(mode="json")
        problem_spec = ProblemSpec.model_validate_json(
            (output_path.parent / "problem_spec.json").read_text(encoding="utf-8")
        )
        source_preflight = load_source_preflight(output_path.parent, problem_spec)
        if source_path.name == "problem_spec.json":
            model_ir = await self.modeler.model(
                problem_spec=problem_spec,
                evidence_profile=evidence_profile,
                task=task,
                work_dir=output_path.parent,
            )
        else:
            review = ModelIRReview.model_validate_json(source_path.read_text(encoding="utf-8"))
            previous_model_ir = ModelIR.model_validate_json(
                (output_path.parent / review.model_ir_ref).read_text(encoding="utf-8")
            )
            model_ir = await self.modeler.revise(
                problem_spec=problem_spec,
                previous_model_ir=previous_model_ir,
                review=review,
                evidence_profile=evidence_profile,
                task=task,
                work_dir=output_path.parent,
            )
        current_preflight = load_source_preflight(output_path.parent, problem_spec)
        if canonical_digest(current_preflight) != canonical_digest(source_preflight):
            raise ValueError("source preflight permission changed while modeling")
        blocked = set(source_preflight.blocked_subproblem_ids)
        if any(item.subproblem_id in blocked and item.execution_status == "solvable" for item in model_ir.subproblems):
            raise ValueError("model generation bypassed a blocked source preflight scope")
        _validate_model_ir_coverage(problem_spec, model_ir)
        validate_model_fact_coverage(problem_spec, model_ir)
        validate_model_evidence_consistency(problem_spec, model_ir, evidence_profile)
        diagnostics = ModelIRSemanticValidator().validate(model_ir)
        if diagnostics:
            details = "; ".join(
                f"{item.code} at {item.path}: {item.message}" for item in diagnostics
            )
            raise ValueError(f"Model IR semantic validation failed: {details}")
        _write_model(output_path, model_ir)
        return output_path

class ModelReviewStage:
    def __init__(self, *, reviewer: ModelReviewer) -> None:
        self.reviewer = reviewer

    async def run(
        self,
        *,
        task_id: str,
        task: dict,
        input_path: Path | None,
        output_path: Path,
    ) -> Path:
        source_path = _required_input(input_path)
        submission = ModelReviewSubmission.model_validate_json(
            source_path.read_text(encoding="utf-8")
        )
        model_ir_path = output_path.parent / submission.model_ir_ref
        model_ir = ModelIR.model_validate_json(model_ir_path.read_text(encoding="utf-8"))
        revision_request = (
            ModelIRReview.model_validate_json(
                (output_path.parent / submission.revision_request_ref).read_text(
                    encoding="utf-8"
                )
            )
            if submission.revision_request_ref is not None
            else None
        )
        problem_spec = ProblemSpec.model_validate_json(
            (output_path.parent / "problem_spec.json").read_text(encoding="utf-8")
        )
        evidence_profile = load_evidence_profile(output_path.parent).model_dump(mode="json")
        input_catalog = load_review_catalog(output_path.parent)
        source_preflight = load_source_preflight(output_path.parent, problem_spec)
        review = await self.reviewer.review(
            problem_spec=problem_spec,
            model_ir=model_ir,
            revision_request=revision_request,
            evidence_profile=evidence_profile,
            input_catalog=input_catalog,
            task=task,
            work_dir=output_path.parent,
        )
        review = review.model_copy(
            update={
                "problem_spec_ref": "problem_spec.json",
                "model_ir_ref": model_ir_path.name,
            }
        )
        if canonical_digest(load_source_preflight(output_path.parent, problem_spec)) != canonical_digest(source_preflight):
            raise ValueError("source preflight permission changed while reviewing")
        validate_model_review(
            review, model_ir, revision_request=revision_request, problem_spec=problem_spec,
            source_preflight=source_preflight, input_catalog=input_catalog,
            evidence_profile=evidence_profile, work_dir=output_path.parent,
        )
        _write_model(output_path.parent / "model_review.json", review)
        approved_projection = _build_reviewed_model_projection(model_ir, review, problem_spec)
        executable_ids = [
            subproblem.subproblem_id
            for subproblem in approved_projection.subproblems
            if isinstance(subproblem, SolvableModelIR)
        ]
        approved = ApprovedModelIR(
            review_status=(
                "approved" if len(executable_ids) == len(approved_projection.subproblems)
                else "partial" if executable_ids else "blocked"
            ),
            model_id=model_ir.model_id,
            model_ir_ref=model_ir_path.name,
            revision_request_ref=submission.revision_request_ref,
            executable_subproblem_ids=executable_ids,
            approved_model_ir=approved_projection,
            review_binding=bind_model_review(
                review=review, model_ir=model_ir, approved_model_ir=approved_projection,
                problem_spec=problem_spec, evidence_profile=evidence_profile,
                input_catalog=input_catalog, revision_request=revision_request,
                source_preflight=source_preflight,
            ),
        )
        _write_model(output_path, approved)
        return output_path

def _build_reviewed_model_projection(
    model_ir: ModelIR,
    review: ModelIRReview,
    problem_spec: ProblemSpec,
) -> ModelIR:
    _validate_model_ir_coverage(problem_spec, model_ir)
    decisions = {
        item.subproblem_id: item
        for item in review.subproblem_reviews
    }
    dependencies = {
        item.id: item.depends_on_subproblem_ids for item in problem_spec.subproblems
    }
    pending_source_ids = unresolved_source_subproblem_ids(review, problem_spec)
    executable = {
        item.subproblem_id for item in model_ir.subproblems
        if isinstance(item, SolvableModelIR)
        and decisions[item.subproblem_id].status == "approved"
        and item.subproblem_id not in pending_source_ids
    }
    # 依赖闭包与题目排列无关；下游不能沿用未获批准的上游模型。
    while True:
        retained = {
            identifier for identifier in executable
            if all(dependency in executable for dependency in dependencies[identifier])
        }
        if retained == executable:
            break
        executable = retained
    projected = []
    for subproblem in model_ir.subproblems:
        decision = decisions[subproblem.subproblem_id]
        if subproblem.subproblem_id in executable:
            projected.append(subproblem)
            continue
        if isinstance(subproblem, BlockedModelIR):
            projected.append(subproblem)
            continue
        reasons = decision.blocking_issues or [
            item.description for item in decision.required_revisions
        ]
        if subproblem.subproblem_id in pending_source_ids:
            reasons = [*reasons, "Unresolved source interpretation affects this subproblem."]
        unavailable_dependencies = [
            identifier for identifier in dependencies[subproblem.subproblem_id]
            if identifier not in executable
        ]
        if unavailable_dependencies:
            reasons = [*reasons, "Unapproved model dependencies: " + ", ".join(unavailable_dependencies)]
        if not reasons:
            reasons = ["Execution is pending completion of the model review."]
        projected.append(BlockedModelIR(
            execution_status="blocked",
            subproblem_id=subproblem.subproblem_id,
            objective=subproblem.objective,
            blocking_reason="; ".join(reasons)[:600],
            missing_inputs=["approved Model IR for this subproblem"],
            recovery_action=(
                "Resolve the registered subproblem review issues, then regenerate "
                "and review this Model IR."
            ),
            expected_outputs=[
                answer.item for answer in subproblem.final_answers
            ],
        ))
    return ModelIR(model_id=model_ir.model_id, subproblems=projected)

class CalculationStage:
    """Compile approved Model IR and execute only registered operators."""

    def __init__(
        self,
        *,
        compiler: ModelIRCompiler,
        executor: OperatorGraphExecutor,
    ) -> None:
        self.compiler = compiler
        self.executor = executor

    async def run(
        self,
        *,
        task_id: str,
        task: dict,
        input_path: Path | None,
        output_path: Path,
    ) -> Path:
        source_path = _required_input(input_path)
        invalidate_stage_outputs(output_path.parent, "calculation")
        approved = ApprovedModelIR.model_validate_json(
            source_path.read_text(encoding="utf-8")
        )
        if approved.review_status not in {"approved", "partial"}:
            raise ValueError(
                "CalculationStage requires an executable approved Model IR projection"
            )
        work_dir = output_path.parent
        problem_spec_path = work_dir / "problem_spec.json"
        problem_spec = (
            ProblemSpec.model_validate_json(
                problem_spec_path.read_text(encoding="utf-8")
            )
            if problem_spec_path.is_file()
            else None
        )
        if problem_spec is None:
            raise ValueError("calculation requires the current problem and complete source preflight permission")
        source_preflight = load_source_preflight(work_dir, problem_spec)
        if (approved.review_binding is None
                or approved.review_binding.source_preflight_sha256 != canonical_digest(source_preflight)):
            raise ValueError("approved model source preflight permission binding is missing or stale")
        if any(item.subproblem_id in source_preflight.blocked_subproblem_ids
               and item.execution_status == "solvable" for item in approved.approved_model_ir.subproblems):
            raise ValueError("approved model bypasses a blocked source preflight scope")
        evidence_profile = (
            load_evidence_profile(work_dir).model_dump(mode="json")
            if (
                (work_dir / "evidence_profile.json").is_file()
                or (work_dir / "input_catalog.json").is_file()
            )
            else None
        )
        compilation = self.compiler.compile_to_file(
            approved.approved_model_ir,
            output_path=work_dir / "execution_plan.json",
            model_ir_ref=f"{source_path.name}#approved_model_ir",
            problem_spec=problem_spec,
            evidence_profile=evidence_profile,
        )
        _write_model(work_dir / "compilation_result.json", compilation)
        if compilation.execution_plan is None:
            self.executor.write_blocked(
                compilation_result=compilation,
                model_ir=approved.approved_model_ir,
                work_dir=work_dir,
            )
            return output_path
        self.executor.execute(
            execution_plan=compilation.execution_plan,
            model_ir=approved.approved_model_ir,
            work_dir=work_dir,
        )
        return output_path

def _validate_model_ir_coverage(problem_spec: ProblemSpec, model_ir: ModelIR) -> None:
    expected = [item.id for item in problem_spec.subproblems]
    modeled = [item.subproblem_id.strip() for item in model_ir.subproblems]
    invalid_ids = not all(expected) or not all(modeled)
    duplicate_ids = len(expected) != len(set(expected)) or len(modeled) != len(set(modeled))
    if invalid_ids or duplicate_ids or set(expected) != set(modeled):
        raise ValueError(
            f"subproblem coverage mismatch: expected={expected}; modeled={modeled}"
        )

def _reconcile_source_requirements(
    problem_spec: ProblemSpec,
    *,
    question: str,
    evidence_profile: dict,
) -> ProblemSpec:
    profile = EvidenceProfile.model_validate(evidence_profile)
    references = list(
        dict.fromkeys(
            re.sub(r"\s+", "", match.group(0))
            for match in re.finditer(
                r"(?i)(?:(?:附件|附录)\s*[a-z0-9一二三四五六七八九十]+|appendix\s*[a-z0-9]+)",
                question,
            )
        )
    )
    requirements = []
    missing_references: list[str] = []
    for reference in references:
        matched_files = [
            item.source_ref
            for item in profile.evidence
            if evidence_matches_reference(item, reference)
        ]
        status = "available" if matched_files else "missing"
        if status == "missing":
            missing_references.append(reference)
        requirements.append(
            {
                "reference": reference,
                "status": status,
                "matched_files": matched_files,
                "note": (
                    "Matched registered evidence in evidence_profile.json."
                    if status == "available"
                    else "Referenced by the problem statement but absent from registered evidence."
                ),
            }
        )

    locked_facts = [
        fact
        for fact in problem_spec.locked_facts
        if not any(reference in fact.statement for reference in missing_references)
    ]
    surviving_fact_ids = {fact.fact_id for fact in locked_facts}
    surviving_source_refs = {
        source_ref
        for record in [*locked_facts, *problem_spec.uncertainties]
        for source_ref in record.source_refs
    }
    subproblems = [
        {
            **subproblem.model_dump(mode="json"),
            "required_fact_ids": [
                fact_id
                for fact_id in subproblem.required_fact_ids
                if fact_id in surviving_fact_ids
            ],
        }
        for subproblem in problem_spec.subproblems
    ]
    data_sources = [
        source.model_copy(update={
            "description": f"Registered evidence source: {source.source_ref}."
        })
        if any(reference in source.description for reference in missing_references)
        else source
        for source in problem_spec.data_sources
        if source.source_ref in surviving_source_refs
    ]
    return ProblemSpec.model_validate(
        {
            **problem_spec.model_dump(mode="json"),
            "data_sources": data_sources,
            "source_requirements": requirements,
            "subproblems": subproblems,
            "locked_facts": locked_facts,
            "uncertainties": problem_spec.uncertainties,
        }
    )

def _is_positive_review_value(value) -> bool:
    text = str(value or "").strip().lower()
    positives = {
        "passed",
        "pass",
        "yes",
        "true",
        "consistent",
        "dimensionless",
        "not_applicable",
        "not applicable",
        "n/a",
    }
    return text in positives or any(
        text.startswith(f"{positive};") or text.startswith(f"{positive}:")
        for positive in positives
    )
