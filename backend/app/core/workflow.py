"""workflow responsibilities."""

from __future__ import annotations

from typing import AsyncGenerator
from app.artifacts.document_validation import FinalizationValidationError
from app.guidance.workflow import run_guidance_workflow
from app.schemas.response import TaskResult
from app.services.task_service import task_manager


def _build_completed_task_result(
    task_id: str,
    task_type: str,
    work_dir: str,
    paper_path: str,
    docx_path: str,
    notebook_path: str,
    primary_artifact_type: str = "paper",
    audit_status: str = "",
    audit_summary: str = "",
    audit_blocks: list | None = None,
) -> dict:
    """Build a normalized completed TaskResult payload."""
    guidance_path = paper_path if primary_artifact_type == "guidance" else ""
    if primary_artifact_type == "guidance":
        docx_path = ""
    return {
        "type": "result",
        "data": TaskResult(
            task_id=task_id,
            status="completed",
            task_type=task_type,
            primary_artifact_type=primary_artifact_type,
            guidance_path=guidance_path,
            paper_path=paper_path,
            docx_path=docx_path,
            notebook_path=notebook_path,
            work_dir=work_dir,
            audit_status=audit_status,
            audit_summary=audit_summary,
            audit_blocks=audit_blocks,
        ).model_dump(),
    }


def _build_failed_task_result(
    task_id: str,
    task_type: str,
    work_dir: str,
    error_message: str,
    error_code: str = "",
    error_type: str = "",
    error_details: dict | None = None,
    paper_path: str = "",
    docx_path: str = "",
    notebook_path: str = "",
    primary_artifact_type: str = "paper",
    audit_status: str = "",
    audit_summary: str = "",
    audit_blocks: list | None = None,
) -> dict:
    """Build a normalized failed TaskResult payload."""
    guidance_path = paper_path if primary_artifact_type == "guidance" else ""
    if primary_artifact_type == "guidance":
        docx_path = ""
    return {
        "type": "result",
        "data": TaskResult(
            task_id=task_id,
            status="failed",
            task_type=task_type,
            primary_artifact_type=primary_artifact_type,
            guidance_path=guidance_path,
            error_message=error_message,
            error_code=error_code,
            error_type=error_type,
            error_details=error_details,
            paper_path=paper_path,
            docx_path=docx_path,
            notebook_path=notebook_path,
            work_dir=work_dir,
            audit_status=audit_status,
            audit_summary=audit_summary,
            audit_blocks=audit_blocks,
        ).model_dump(),
    }


def _classify_failure(exc: Exception) -> tuple[str, str, str, dict | None]:
    """Map internal exceptions to machine-readable failure semantics."""
    if isinstance(exc, FinalizationValidationError):
        return str(exc), exc.error_code, exc.error_type, exc.details
    return str(exc), "WORKFLOW_RUNTIME_ERROR", "runtime", None


async def run_workflow(task_id: str, question: str) -> AsyncGenerator[dict, None]:
    task = task_manager.get_task(task_id)
    if not task:
        yield {"error": "任务不存在"}
        return

    task_type = task.get("task_type", "writing")
    if task_type == "polish":
        from app.polish import workflow as polish_workflow

        async for payload in polish_workflow.run_polish_workflow(task_id, task):
            yield payload
        return

    async for payload in run_guidance_workflow(task_id, task):
        yield payload


async def run_revision_workflow(
    revision_task_id: str,
    revision_task: dict,
) -> AsyncGenerator[dict, None]:
    """历史论文修订统一走润色流水线。"""
    revision_task["task_type"] = "polish"
    if not revision_task.get("paper_content") and revision_task.get("parent_task_id"):
        revision_task["paper_content"] = task_manager.load_paper(revision_task.get("parent_task_id", "")) or ""

    from app.polish import workflow as polish_workflow

    async for payload in polish_workflow.run_polish_workflow(revision_task_id, revision_task):
        yield payload
