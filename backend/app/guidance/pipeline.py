"""来源解释先于建模的文件合同流水线。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncGenerator, Protocol

from app.artifacts.evidence_profile import EvidenceProfile
from app.guidance.contracts import ProblemSpec
from app.guidance.failure_contracts import (
    bounded_failure_summary,
    bounded_subproblem_objective,
)
from app.guidance.model_review_contracts import (ApprovedModelIR, ModelIRReview, ModelReviewSubmission)
from app.guidance.model_review_validation import bind_model_review, load_review_catalog
from app.guidance.model_ir import (BlockedModelIR, ModelIR)
from app.guidance.recovery import invalidate_stage_outputs, load_source_preflight
from app.schemas.response import (
    TaskProgress,
    TaskResult,
    extract_capability_coverage,
    extract_verification_layers,
)


class GuidanceStage(Protocol):
    async def run(
        self,
        *,
        task_id: str,
        task: dict,
        input_path: Path | None,
        output_path: Path,
    ) -> Path: ...


class GuidancePipeline:
    """按来源、数学和执行职责调度文件合同阶段。"""

    def __init__(
        self,
        *,
        decomposition_stage: GuidanceStage,
        source_stage: GuidanceStage,
        modeling_stage: GuidanceStage,
        model_review_stage: GuidanceStage,
        calculation_stage: GuidanceStage,
        result_verification_stage: GuidanceStage,
        guidance_stage: GuidanceStage,
    ) -> None:
        self._stages = {
            "decomposition": decomposition_stage,
            "source": source_stage,
            "modeling": modeling_stage,
            "model_review": model_review_stage,
            "calculation": calculation_stage,
            "result_verification": result_verification_stage,
            "guidance": guidance_stage,
        }

    async def run(self, task_id: str, task: dict) -> AsyncGenerator[dict, None]:
        root = Path(str(task["work_dir"]))
        root.mkdir(parents=True, exist_ok=True)

        yield _progress(task_id, "decomposition", 0.10, "正在拆解题目与数据")
        problem_path = await self._run_stage(
            "decomposition", task_id, task, None, root / "problem_spec.json"
        )

        yield _progress(task_id, "source", 0.18, "正在独立核对来源解释与题意")
        problem_path = await self._run_stage(
            "source", task_id, task, problem_path, root / "problem_spec.json"
        )

        yield _progress(task_id, "modeling", 0.25, "正在生成建模方案")
        try:
            model_path = await self._run_stage(
                "modeling", task_id, task, problem_path, root / "model_ir.json"
            )
        except Exception as exc:
            approved_path = _write_modeling_failure_contracts(
                root,
                task_id=task_id,
                input_path=problem_path,
                exc=exc,
            )
            yield _progress(
                task_id,
                "guidance",
                0.94,
                "正在生成建模失败阻断说明",
            )
            await self._run_stage(
                "guidance", task_id, task, approved_path, root / "guidance.md"
            )
            for payload in _final_result(task_id, root):
                yield payload
            return

        yield _progress(task_id, "model_review", 0.40, "正在独立审核模型")
        review_submission_path = _write_review_submission(root, model_path)
        approved_path = await self._run_stage(
            "model_review",
            task_id,
            task,
            review_submission_path,
            root / "approved_model_ir.json",
        )
        approved = _load_approved_model(approved_path)
        review = ModelIRReview.model_validate_json(
            (root / approved.review_ref).read_text(encoding="utf-8")
        )

        if review.status == "revision_required":
            yield _progress(task_id, "modeling", 0.46, "正在根据审核意见修订模型")
            review_path = _preserve_revision_request(root, model_path)
            try:
                model_path = await self._run_stage(
                    "modeling", task_id, task, review_path, root / "model_ir.json"
                )
            except Exception as exc:
                approved_path = _write_modeling_failure_contracts(
                    root,
                    task_id=task_id,
                    input_path=review_path,
                    exc=exc,
                )
                yield _progress(
                    task_id,
                    "guidance",
                    0.94,
                    "正在生成建模失败阻断说明",
                )
                await self._run_stage(
                    "guidance", task_id, task, approved_path, root / "guidance.md"
                )
                for payload in _final_result(task_id, root):
                    yield payload
                return
            yield _progress(task_id, "model_review", 0.52, "正在复审修订后的模型")
            review_submission_path = _write_review_submission(
                root,
                model_path,
                revision_request_path=review_path,
            )
            approved_path = await self._run_stage(
                "model_review",
                task_id,
                task,
                review_submission_path,
                root / "approved_model_ir.json",
            )

        approved = _load_approved_model(approved_path)
        if approved.review_status in {"approved", "partial"}:
            yield _progress(task_id, "calculation", 0.62, "正在编译并执行算子图")
            manifest_path = await self._run_stage(
                "calculation", task_id, task, approved_path, root / "execution_manifest.json"
            )

            yield _progress(task_id, "result_verification", 0.80, "正在复核数值与结果")
            verification_path = await self._run_stage(
                "result_verification", task_id, task, manifest_path, root / "verification_report.json"
            )

            guidance_input = verification_path
            guidance_message = "正在生成建模指导"
        else:
            guidance_input = approved_path
            guidance_message = "正在生成审核阻断说明"

        yield _progress(task_id, "guidance", 0.94, guidance_message)
        await self._run_stage(
            "guidance", task_id, task, guidance_input, root / "guidance.md"
        )

        for payload in _final_result(task_id, root):
            yield payload

    async def _run_stage(
        self,
        stage_name: str,
        task_id: str,
        task: dict,
        input_path: Path | None,
        output_path: Path,
    ) -> Path:
        invalidate_stage_outputs(output_path.parent, stage_name)
        return await self._stages[stage_name].run(
            task_id=task_id,
            task=task,
            input_path=input_path,
            output_path=output_path,
        )


def _write_review_submission(
    root: Path,
    model_path: Path,
    *,
    revision_request_path: Path | None = None,
) -> Path:
    submission = ModelReviewSubmission(
        model_ir_ref=model_path.name,
        revision_request_ref=(
            revision_request_path.name if revision_request_path is not None else None
        ),
    )
    path = root / "model_review_submission.json"
    path.write_text(submission.model_dump_json(indent=2), encoding="utf-8")
    return path


def _preserve_revision_request(root: Path, model_path: Path) -> Path:
    problem_path = root / "problem_spec.json"
    original_problem_bytes = problem_path.read_bytes()
    problem_spec = ProblemSpec.model_validate_json(original_problem_bytes)
    review = ModelIRReview.model_validate_json(
        (root / "model_review.json").read_text(encoding="utf-8")
    )
    if (root / "revision_request.json").exists():
        raise ValueError("mathematical revision is limited to one revision pass")
    source_preflight = load_source_preflight(root, problem_spec)
    initial_model = ModelIR.model_validate_json(model_path.read_text(encoding="utf-8"))
    approved_path = root / "approved_model_ir.json"
    approved = _load_approved_model(approved_path) if approved_path.is_file() else None
    evidence_profile = _read_json(root / "evidence_profile.json")
    if approved is None or approved.review_binding is None:
        raise ValueError("mathematical revision requires its unchanged initial review binding")
    current_binding = bind_model_review(
        review=review, model_ir=initial_model, approved_model_ir=approved.approved_model_ir,
        problem_spec=problem_spec,
        evidence_profile=EvidenceProfile.model_validate(evidence_profile).model_dump(mode="json"),
        input_catalog=load_review_catalog(root), revision_request=None, source_preflight=source_preflight,
    )
    if current_binding != approved.review_binding:
        raise ValueError("initial review binding or source preflight permission is stale")
    # 数学修订只保留它审核过的已裁决题意，不能重开来源应用。
    initial_problem_path = root / "problem_spec_initial.json"
    initial_problem_path.write_bytes(original_problem_bytes)
    initial_model_path = root / "model_ir_initial.json"
    initial_model_path.write_bytes(model_path.read_bytes())
    if approved_path.is_file():
        (root / "approved_model_ir_initial.json").write_bytes(approved_path.read_bytes())

    review = review.model_copy(update={
        "model_ir_ref": initial_model_path.name,
        "problem_spec_ref": initial_problem_path.name,
    })
    path = root / "revision_request.json"
    path.write_text(review.model_dump_json(indent=2), encoding="utf-8")
    return path


def _final_result(task_id: str, root: Path):
    guidance_path = root / "guidance.md"
    audit = _read_json(root / "guidance_audit_report.json")
    verification = _read_json(root / "verification_report.json")
    final_status, final_message = _resolve_final_outcome(root, audit)
    yield _progress(task_id, "done", 1.0, final_message, status=final_status)
    yield {
        "type": "result",
        "data": TaskResult(
            task_id=task_id,
            status=final_status,
            task_type="guidance",
            primary_artifact_type="guidance",
            markdown_path=str(guidance_path),
            guidance_path=str(guidance_path),
            paper_path=str(guidance_path),
            docx_path="",
            notebook_path=str(root / "notebook.ipynb") if (root / "notebook.ipynb").is_file() else "",
            work_dir=str(root),
            audit_status=str(audit.get("status") or ""),
            audit_summary=json.dumps(audit.get("scores", {}), ensure_ascii=False),
            audit_blocks=audit.get("blocks", []),
            verification_layers=extract_verification_layers(verification),
            capability_coverage=extract_capability_coverage(
                approved_model=_read_json(root / "approved_model_ir.json"),
                compilation_result=_read_json(root / "compilation_result.json"),
                result_registry=_read_json(root / "result_registry.json"),
            ),
        ).model_dump(),
    }


def _resolve_final_outcome(root: Path, audit: dict) -> tuple[str, str]:
    if str(audit.get("status") or "").upper() == "BLOCK":
        return "blocked", "guidance audit blocked the generated plan"
    approved = _read_json(root / "approved_model_ir.json")
    review_status = str(approved.get("review_status") or "").lower()
    if review_status in {"blocked", "revision_required"}:
        return "blocked", "model approval blocked execution"
    verification = _read_json(root / "verification_report.json")
    verification_status = str(verification.get("status") or "").lower()
    if verification_status == "blocked":
        return "blocked", "result verification blocked the generated plan"
    if review_status == "partial" or verification_status == "partial":
        return "partial", "guidance plan generated with incomplete verification or blocked subproblems"
    return "completed", "guidance plan generated"


def _progress(
    task_id: str,
    stage: str,
    progress: float,
    message: str,
    *,
    status: str = "running",
) -> dict:
    return {
        "type": "progress",
        "data": TaskProgress(
            task_id=task_id,
            status=status,
            stage=stage,
            progress=progress,
            message=message,
            current_subtask=message,
        ).model_dump(),
    }


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _load_approved_model(path: Path) -> ApprovedModelIR:
    return ApprovedModelIR.model_validate(_read_json(path))


def _write_modeling_failure_contracts(
    root: Path,
    *,
    task_id: str,
    input_path: Path,
    exc: Exception,
) -> Path:
    message = str(exc)
    blocking_summary = bounded_failure_summary(message, "modeling_failure.json")
    failure = {
        "task_id": task_id,
        "failed_stage": "modeling",
        "failed_operation": "model_ir_generation",
        "input_ref": input_path.name,
        "error_type": type(exc).__name__,
        "error": message,
        "resume_from": input_path.name,
    }
    (root / "modeling_failure.json").write_text(
        json.dumps(failure, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    problem_spec = ProblemSpec.model_validate(_read_json(root / "problem_spec.json"))
    model_ir = ModelIR(
        model_id="modeling-contract-failure",
        subproblems=[
            BlockedModelIR(
                execution_status="blocked",
                subproblem_id=subproblem.id,
                objective=bounded_subproblem_objective(subproblem),
                blocking_reason=blocking_summary,
                missing_inputs=["valid Model IR contract"],
                recovery_action=(
                    "Correct the Model IR contract using only the stated problem and "
                    "available evidence, then submit it for review again."
                ),
                expected_outputs=["reviewable Model IR for the requested subproblem"],
            )
            for subproblem in problem_spec.subproblems
        ],
    )
    (root / "model_ir.json").write_text(model_ir.model_dump_json(indent=2), encoding="utf-8")
    review = ModelIRReview(
        model_id=model_ir.model_id,
        model_ir_ref="model_ir.json",
        identifiability="not_reviewed",
        dimensional_consistency="not_reviewed",
        data_sufficiency="not_reviewed",
        computational_feasibility="blocked",
        subproblem_reviews=[
            {
                "subproblem_id": subproblem.subproblem_id,
                "status": "blocked",
                "blocking_issues": [blocking_summary],
            }
            for subproblem in model_ir.subproblems
        ],
    )
    (root / "model_review.json").write_text(review.model_dump_json(indent=2), encoding="utf-8")
    approved = ApprovedModelIR(
        review_status="blocked",
        model_id=model_ir.model_id,
        model_ir_ref="model_ir.json",
        executable_subproblem_ids=[],
        approved_model_ir=model_ir,
    )
    approved_path = root / "approved_model_ir.json"
    approved_path.write_text(
        approved.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return approved_path
