"""Six-stage orchestration for high-trust modeling guidance."""

from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncGenerator, Protocol

from app.guidance.contracts import ProblemSpec
from app.guidance.model_ir import ApprovedModelIR, BlockedModelIR, ModelIR, ModelIRReview
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
    """Run six file-contract stages without owning their business logic."""

    def __init__(
        self,
        *,
        decomposition_stage: GuidanceStage,
        modeling_stage: GuidanceStage,
        model_review_stage: GuidanceStage,
        calculation_stage: GuidanceStage,
        result_verification_stage: GuidanceStage,
        guidance_stage: GuidanceStage,
    ) -> None:
        self._stages = {
            "decomposition": decomposition_stage,
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
        approved_path = await self._run_stage(
            "model_review", task_id, task, model_path, root / "approved_model_ir.json"
        )
        approved = _load_approved_model(approved_path)

        if approved.review_status == "revision_required":
            yield _progress(task_id, "modeling", 0.46, "正在根据审核意见修订模型")
            review_path = root / "model_review.json"
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
            approved_path = await self._run_stage(
                "model_review", task_id, task, model_path, root / "approved_model_ir.json"
            )

        approved = _load_approved_model(approved_path)
        if approved.review_status == "approved":
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
        return await self._stages[stage_name].run(
            task_id=task_id,
            task=task,
            input_path=input_path,
            output_path=output_path,
        )


def _final_result(task_id: str, root: Path):
    guidance_path = root / "guidance.md"
    audit = _read_json(root / "guidance_audit_report.json")
    verification = _read_json(root / "verification_report.json")
    final_status = _resolve_final_status(root, audit)
    final_message = (
        "guidance audit blocked the generated plan"
        if final_status == "blocked"
        else "guidance plan generated"
    )
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


def _resolve_final_status(root: Path, audit: dict) -> str:
    if str(audit.get("status") or "").upper() == "BLOCK":
        return "blocked"
    verification = _read_json(root / "verification_report.json")
    if str(verification.get("status") or "").lower() == "blocked":
        return "blocked"
    approved = _read_json(root / "approved_model_ir.json")
    review_status = str(approved.get("review_status") or "").lower()
    if review_status and review_status != "approved":
        return "blocked"
    return "completed"


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
                subproblem_id=str(subproblem["id"]),
                objective=str(
                    subproblem.get("objective")
                    or subproblem.get("goal")
                    or problem_spec.task_summary
                ),
                blocking_reason=message,
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
        status="blocked",
        model_ir_ref="model_ir.json",
        identifiability="not_reviewed",
        dimensional_consistency="not_reviewed",
        data_sufficiency="not_reviewed",
        computational_feasibility="blocked",
        blocking_issues=[message],
        required_revisions=[
            "Regenerate a syntactically valid Model IR contract before model review."
        ],
    )
    (root / "model_review.json").write_text(review.model_dump_json(indent=2), encoding="utf-8")
    approved = ApprovedModelIR(
        review_status="blocked",
        model_id=model_ir.model_id,
        model_ir_ref="model_ir.json",
        approved_model_ir=model_ir,
    )
    approved_path = root / "approved_model_ir.json"
    approved_path.write_text(
        approved.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return approved_path
