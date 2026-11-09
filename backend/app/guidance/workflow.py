"""Public orchestration boundary for guidance generation."""

from __future__ import annotations

from typing import AsyncGenerator

from app.core.llm.llm import LLM
from app.core.workflow_budget import resolve_workflow_budget
from app.guidance.modeler import LLMModeler
from app.guidance.reviewer import LLMModelReviewer
from app.guidance.decomposer import LLMProblemDecomposer
from app.guidance.model_ir_compiler import ModelIRCompiler
from app.guidance.operator_catalog import DEFAULT_OPERATOR_IDS, build_default_operator_registry
from app.guidance.operator_execution import OperatorGraphExecutor
from app.guidance.pipeline import GuidancePipeline
from app.guidance.stages import CalculationStage, DecompositionStage, ModelingStage, ModelReviewStage
from app.guidance.guidance_stage import GuidanceStage
from app.guidance.verification_stage import ResultVerificationStage
from app.schemas.enums import TaskStatus
from app.schemas.response import TaskResult
from app.services.config_manager import config_manager
from app.services.task_service import task_manager


def build_default_guidance_pipeline(workflow_mode: str = "standard") -> GuidancePipeline:
    budget = resolve_workflow_budget(workflow_mode)
    guidance_budget = budget.guidance
    decomposition_llm = LLM(
        model=config_manager.get_effective_model("COORDINATOR_MODEL"),
        temperature=0.1,
        max_tokens=guidance_budget.structure_tokens.initial,
        request_timeout=guidance_budget.decomposition,
    )
    modeling_llm = LLM(
        model=config_manager.get_effective_model("MODELER_MODEL"),
        temperature=0.15,
        max_tokens=guidance_budget.architecture_tokens.initial,
        request_timeout=max(
            guidance_budget.architecture,
            guidance_budget.indexed_core,
            guidance_budget.indexed_constraints,
        ),
    )
    review_llm = LLM(
        model=config_manager.get_effective_model("MODELER_MODEL"),
        temperature=0.0,
        max_tokens=guidance_budget.review_tokens.initial,
        request_timeout=guidance_budget.review,
    )
    operator_registry = build_default_operator_registry()
    return GuidancePipeline(
        decomposition_stage=DecompositionStage(decomposer=LLMProblemDecomposer(
            decomposition_llm,
            request_timeout=guidance_budget.decomposition,
            structure_token_budget=guidance_budget.structure_tokens,
            facts_token_budget=guidance_budget.facts_tokens,
        )),
        modeling_stage=ModelingStage(modeler=LLMModeler(
            modeling_llm,
            available_operator_ids=sorted(DEFAULT_OPERATOR_IDS),
            architecture_timeout=guidance_budget.architecture,
            indexed_core_timeout=guidance_budget.indexed_core,
            indexed_constraints_timeout=guidance_budget.indexed_constraints,
            architecture_token_budget=guidance_budget.architecture_tokens,
            indexed_core_token_budget=guidance_budget.indexed_core_tokens,
            indexed_constraints_token_budget=guidance_budget.indexed_constraints_tokens,
        )),
        model_review_stage=ModelReviewStage(reviewer=LLMModelReviewer(
            review_llm,
            request_timeout=guidance_budget.review,
            token_budget=guidance_budget.review_tokens,
        )),
        calculation_stage=CalculationStage(
            compiler=ModelIRCompiler(registry=operator_registry),
            executor=OperatorGraphExecutor(registry=operator_registry),
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
        terminal_status = {
            "blocked": TaskStatus.BLOCKED,
            "partial": TaskStatus.PARTIAL,
        }.get(final_status, TaskStatus.COMPLETED)
        task_manager.update_status(task_id, terminal_status)
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
