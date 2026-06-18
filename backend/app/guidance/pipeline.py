"""Independent compute-first guidance pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncGenerator

from app.artifacts.guidance_audit import build_guidance_audit_report
from app.compute.runner import run_solve_spec
from app.guidance.planner import GuidancePlanner, LLMGuidancePlanner
from app.guidance.renderer import render_guidance_markdown
from app.schemas.response import TaskProgress, TaskResult


class GuidancePipeline:
    """Hide planning, local compute, rendering, and audit behind one interface."""

    def __init__(self, *, planner: GuidancePlanner | None = None) -> None:
        self.planner = planner or LLMGuidancePlanner()

    async def run(self, task_id: str, task: dict) -> AsyncGenerator[dict, None]:
        root = Path(str(task["work_dir"]))
        root.mkdir(parents=True, exist_ok=True)
        question = str(task.get("source_question") or task.get("question") or "").strip()
        workflow_mode = str(task.get("workflow_mode") or "standard")
        data_files = _list_input_files(root)

        yield _progress(task_id, "guidance_planning", 0.12, "正在生成结构化计算规格")
        solve_spec = await self.planner.plan(
            question=question,
            data_files=data_files,
            workflow_mode=workflow_mode,
        )
        _write_json(root / "solve_spec.json", solve_spec)

        yield _progress(task_id, "guidance_compute", 0.48, "正在执行本地确定性计算")
        run_solve_spec(solve_spec, root)
        result_registry = _read_json(root / "result_registry.json")
        parameter_registry = _read_json(root / "parameter_registry.json")

        yield _progress(task_id, "guidance_render", 0.76, "正在生成可追踪 Markdown 方案")
        guidance = render_guidance_markdown(
            solve_spec=solve_spec,
            result_registry=result_registry,
            parameter_registry=parameter_registry,
        )
        guidance_context = {
            "artifact_type": "guidance",
            "workflow_mode": workflow_mode,
            "required_sections": [
                "可信度摘要",
                "问题理解",
                "数据与来源",
                "参数与来源表",
                "模型选择理由",
                "分问题建模步骤",
                "必要计算结果",
                "复核与稳健性",
                "阻断项与待确认项",
                "论文转化建议",
                "可复现附件说明",
            ],
        }
        _write_json(root / "guidance_context.json", guidance_context)
        audit = build_guidance_audit_report(
            guidance_markdown=guidance,
            guidance_context=guidance_context,
            result_registry=result_registry,
            parameter_registry=parameter_registry,
        )
        _write_json(root / "guidance_audit_report.json", audit)
        guidance_path = root / "guidance.md"
        guidance_path.write_text(guidance, encoding="utf-8")
        (root / "res.md").write_text(guidance, encoding="utf-8")

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


def _list_input_files(root: Path) -> list[str]:
    data_dir = root / "data"
    if not data_dir.is_dir():
        return []
    return sorted(path.relative_to(root).as_posix() for path in data_dir.rglob("*") if path.is_file())


def _read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

