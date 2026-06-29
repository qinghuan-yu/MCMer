"""Public orchestration boundary for guidance generation."""

from __future__ import annotations

from typing import AsyncGenerator

from app.core.llm.llm import LLM
from app.core.workflow_budget import resolve_workflow_budget
from app.guidance.agents import (
    LLMModeler,
    LLMModelReviewer,
    LLMProblemDecomposer,
    LLMProgramGenerator,
)
from app.guidance.execution import LocalProgramExecutor
from app.guidance.pipeline import GuidancePipeline
from app.guidance.stages import (
    CalculationStage,
    DecompositionStage,
    GuidanceStage,
    ModelingStage,
    ModelReviewStage,
    ResultVerificationStage,
)
from app.schemas.enums import TaskStatus
from app.schemas.response import TaskResult
from app.services.config_manager import config_manager
from app.services.task_service import task_manager


def build_default_guidance_pipeline(workflow_mode: str = "standard") -> GuidancePipeline:
    budget = resolve_workflow_budget(workflow_mode)
    decomposition_llm = LLM(
        model=config_manager.get_effective_model("COORDINATOR_MODEL"),
        temperature=0.1,
        max_tokens=8192,
        request_timeout=budget.llm_timeout,
    )
    modeling_llm = LLM(
        model=config_manager.get_effective_model("MODELER_MODEL"),
        temperature=0.15,
        max_tokens=12288,
        request_timeout=budget.llm_timeout,
    )
    review_llm = LLM(
        model=config_manager.get_effective_model("MODELER_MODEL"),
        temperature=0.0,
        max_tokens=8192,
        request_timeout=budget.llm_timeout,
    )
    coder_llm = LLM(
        model=config_manager.get_effective_model("CODER_MODEL"),
        temperature=0.1,
        max_tokens=16384,
        request_timeout=budget.llm_timeout,
    )
    return GuidancePipeline(
        decomposition_stage=DecompositionStage(decomposer=LLMProblemDecomposer(decomposition_llm)),
        modeling_stage=ModelingStage(modeler=LLMModeler(modeling_llm)),
        model_review_stage=ModelReviewStage(reviewer=LLMModelReviewer(review_llm)),
        calculation_stage=CalculationStage(
            program_generator=LLMProgramGenerator(coder_llm),
            executor=LocalProgramExecutor(),
        ),
        result_verification_stage=ResultVerificationStage(),
        guidance_stage=GuidanceStage(),
    )


async def run_guidance_workflow(task_id: str, task: dict) -> AsyncGenerator[dict, None]:
    """Run guidance without entering the historical paper workflow."""
    task_manager.update_status(task_id, TaskStatus.RUNNING)
    try:
        final_status = "completed"
        async for payload in build_default_guidance_pipeline(
            str(task.get("workflow_mode") or "standard")
        ).run(task_id, task):
            if payload.get("type") == "result" and isinstance(payload.get("data"), dict):
                final_status = str(payload["data"].get("status") or final_status)
            yield payload
        task_manager.update_status(
            task_id,
            TaskStatus.BLOCKED if final_status == "blocked" else TaskStatus.COMPLETED,
        )
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
