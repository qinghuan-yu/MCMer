"""workflow runtime responsibilities."""

from __future__ import annotations

import json
from app.config.setting import settings
from app.core.llm.llm import LLM
from app.core.workflow_budget import WorkflowBudget, normalize_workflow_mode
from app.schemas.response import TaskProgress
from app.services.config_manager import config_manager
from app.tools.e2b_interpreter import E2BCodeInterpreter
from app.tools.local_interpreter import LocalCodeInterpreter


def _preview(text: str, limit: int = 320) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _message(agent: str, content: str, msg_type: str = "success", section: str = "") -> dict:
    payload = {"type": msg_type, "agent": agent, "content": content}
    if section:
        payload["section"] = section
    return payload


def _unique_structured_result_files(*file_groups: list[str]) -> list[str]:
    return list(
        dict.fromkeys(
            filename
            for group in file_groups
            for filename in group
            if filename
        )
    )


def _resolve_question(task: dict) -> str:
    question = (task.get("question") or "").strip()
    source_question = (task.get("source_question") or "").strip()
    source_text = (task.get("source_question_text") or "").strip()

    if source_question and question and source_question != question:
        return source_question
    if source_question:
        return source_question

    if source_text and question and source_text != question:
        return f"{question}\n\n# 附件原题文本\n{source_text}"
    if source_text:
        return source_text
    return question


def _progress(
    task_id: str,
    stage: str,
    progress: float,
    message: str,
    status: str = "running",
    current_subtask: str = "",
) -> dict:
    return {
        "type": "progress",
        "data": TaskProgress(
            task_id=task_id,
            status=status,
            stage=stage,
            progress=progress,
            message=message,
            current_subtask=current_subtask,
        ).model_dump(),
    }


def _json_for_prompt(payload: object, limit: int = 12000) -> str:
    try:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception:
        text = str(payload)
    if len(text) <= limit:
        return text
    return text[: limit - 16] + "\n...\n}"


def _build_models(budget: WorkflowBudget) -> dict[str, LLM]:
    return {
        "breakdown": LLM(model=config_manager.get_effective_model("COORDINATOR_MODEL"), temperature=0.2, max_tokens=8192, request_timeout=budget.guidance.decomposition),
        "modeling": LLM(model=config_manager.get_effective_model("MODELER_MODEL"), temperature=0.35, max_tokens=12288, request_timeout=budget.guidance.architecture),
        "review": LLM(model=config_manager.get_effective_model("MODELER_MODEL"), temperature=0.2, max_tokens=8192, request_timeout=budget.guidance.review),
        "coder": LLM(model=config_manager.get_effective_model("CODER_MODEL"), temperature=0.2, max_tokens=12288, request_timeout=budget.guidance.indexed_core),
        "analysis": LLM(model=config_manager.get_effective_model("WRITER_MODEL"), temperature=0.35, max_tokens=12288, request_timeout=budget.writer.request_timeout),
        "writer": LLM(model=config_manager.get_effective_model("WRITER_MODEL"), temperature=0.45, max_tokens=16384, request_timeout=budget.writer.request_timeout),
    }


def _resolve_workflow_mode(task: dict) -> str:
    return normalize_workflow_mode(task.get("workflow_mode") or settings.WORKFLOW_MODE)


def _build_code_interpreter(work_dir: str, chart_language: str = "English"):
    if settings.CODE_INTERPRETER == "e2b" and settings.E2B_API_KEY:
        return E2BCodeInterpreter(api_key=settings.E2B_API_KEY, work_dir=work_dir)
    return LocalCodeInterpreter(work_dir=work_dir, chart_language=chart_language)
