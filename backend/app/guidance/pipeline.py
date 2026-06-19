"""Six-stage orchestration for high-trust modeling guidance."""

from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncGenerator, Protocol

from app.guidance.contracts import ApprovedModelSpec
from app.guidance.execution import UnapprovedModelError
from app.schemas.response import TaskProgress, TaskResult


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

    _STAGE_FLOW = (
        ("decomposition", "problem_spec.json", 0.10, "正在拆解题目与数据"),
        ("modeling", "model_spec.json", 0.25, "正在生成建模方案"),
        ("model_review", "approved_model_spec.json", 0.40, "正在独立审核模型"),
        ("calculation", "execution_manifest.json", 0.62, "正在生成并执行完整求解程序"),
        ("result_verification", "verification_report.json", 0.80, "正在复核数值与结果"),
        ("guidance", "guidance.md", 0.94, "正在生成建模指导"),
    )

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
        input_path: Path | None = None

        for stage_name, filename, progress, message in self._STAGE_FLOW:
            yield _progress(task_id, stage_name, progress, message)
            output_path = root / filename
            input_path = await self._stages[stage_name].run(
                task_id=task_id,
                task=task,
                input_path=input_path,
                output_path=output_path,
            )
            if stage_name == "model_review":
                _require_approved_model(input_path)

        guidance_path = root / "guidance.md"
        audit = _read_json(root / "guidance_audit_report.json")
        yield _progress(task_id, "done", 1.0, "建模指导方案已生成", status="completed")
        yield {
            "type": "result",
            "data": TaskResult(
                task_id=task_id,
                status="completed",
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
            ).model_dump(),
        }


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


def _require_approved_model(path: Path) -> None:
    approved = ApprovedModelSpec.model_validate(_read_json(path))
    if approved.review_status != "approved":
        raise UnapprovedModelError(
            f"calculation requires approved model, got {approved.review_status}"
        )
