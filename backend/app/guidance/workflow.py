"""Public orchestration boundary for guidance generation."""

from __future__ import annotations

from typing import AsyncGenerator

from app.guidance.pipeline import GuidancePipeline
from app.schemas.enums import TaskStatus
from app.schemas.response import TaskResult
from app.services.task_service import task_manager


async def run_guidance_workflow(task_id: str, task: dict) -> AsyncGenerator[dict, None]:
    """Run guidance without entering the historical paper workflow."""
    task_manager.update_status(task_id, TaskStatus.RUNNING)
    try:
        async for payload in GuidancePipeline().run(task_id, task):
            yield payload
        task_manager.update_status(task_id, TaskStatus.COMPLETED)
    except Exception as exc:
        task_manager.update_status(task_id, TaskStatus.FAILED)
        yield {
            "type": "result",
            "data": TaskResult(
                task_id=task_id,
                status="failed",
                task_type="guidance",
                primary_artifact_type="guidance",
                work_dir=str(task.get("work_dir") or ""),
                error_message=str(exc),
                error_code="GUIDANCE_PIPELINE_FAILED",
                error_type="guidance_pipeline",
            ).model_dump(),
        }
