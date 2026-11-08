"""Guidance 职责边界：modeler。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.core.llm.llm import LLM
from app.guidance.contracts import ProblemSpec
from app.guidance.failure_contracts import (
    bounded_failure_summary,
    bounded_subproblem_objective,
    safe_artifact_component,
)
from app.guidance.generation_validation import _validate_model_ir_contract
from app.guidance.model_generation import _request_single_subproblem_model
from app.guidance.model_ir import (
    BlockedModelIR,
    ModelIR,
)
from app.guidance.model_review_contracts import ModelIRReview
from app.guidance.modeling_context import (
    MODEL_IR_ARCHITECTURE_CONTRACT,
    _compact_problem_spec_for_modeling,
)


class LLMModeler:
    def __init__(
        self,
        llm: LLM,
        *,
        available_operator_ids: list[str] | None = None,
        architecture_timeout: int | float | None = None,
        indexed_core_timeout: int | float | None = None,
        indexed_constraints_timeout: int | float | None = None,
    ) -> None:
        self.llm = llm
        self.available_operator_ids = sorted(set(available_operator_ids or []))
        self.architecture_timeout = architecture_timeout
        self.indexed_core_timeout = indexed_core_timeout
        self.indexed_constraints_timeout = indexed_constraints_timeout

    async def model(
        self,
        *,
        problem_spec: ProblemSpec,
        evidence_profile: dict | None = None,
        task: dict,
        work_dir: Path,
    ) -> ModelIR:
        return await _request_subproblem_models(
            llm=self.llm,
            stage_duty="你是数学建模 ModelIR 架构构建器。依据 problem_spec 构建新的模型架构。",
            problem_spec=problem_spec,
            evidence_profile=evidence_profile or {"evidence": []},
            available_operator_ids=self.available_operator_ids,
            agent_name="GuidanceModeler",
            artifact_dir=work_dir,
            architecture_timeout=self.architecture_timeout,
            indexed_core_timeout=self.indexed_core_timeout,
            indexed_constraints_timeout=self.indexed_constraints_timeout,
        )

    async def revise(
        self,
        *,
        problem_spec: ProblemSpec,
        previous_model_ir: ModelIR,
        review: ModelIRReview,
        evidence_profile: dict | None = None,
        task: dict,
        work_dir: Path,
    ) -> ModelIR:
        return await _request_subproblem_models(
            llm=self.llm,
            stage_duty=(
                "你是数学建模 ModelIR 修订架构构建器。逐条处理 "
                "model_review.subproblem_reviews[0].required_revisions，"
                "并使用 previous_model_summary 理解旧路线和显式 assumptions。无法在现有数据和"
                "算子能力下诚实修复的子问题才改为 blocked；不得通过虚构数据、参数或约束绕过审核。"
            ),
            problem_spec=problem_spec,
            evidence_profile=evidence_profile or {"evidence": []},
            available_operator_ids=self.available_operator_ids,
            agent_name="GuidanceModelReviser",
            artifact_dir=work_dir,
            previous_model_ir=previous_model_ir,
            review=review,
            architecture_timeout=self.architecture_timeout,
            indexed_core_timeout=self.indexed_core_timeout,
            indexed_constraints_timeout=self.indexed_constraints_timeout,
        )

async def _request_subproblem_models(
    *,
    llm: LLM,
    stage_duty: str,
    problem_spec: ProblemSpec,
    evidence_profile: dict,
    available_operator_ids: list[str],
    agent_name: str,
    artifact_dir: Path,
    previous_model_ir: ModelIR | None = None,
    review: ModelIRReview | None = None,
    architecture_timeout: int | float | None = None,
    indexed_core_timeout: int | float | None = None,
    indexed_constraints_timeout: int | float | None = None,
) -> ModelIR:
    system_prompt = f"{stage_duty}\n{MODEL_IR_ARCHITECTURE_CONTRACT}"
    compact_spec = _compact_problem_spec_for_modeling(problem_spec)
    compact_subproblems = compact_spec["subproblems"]
    previous_by_id = (
        {item.subproblem_id: item for item in previous_model_ir.subproblems}
        if previous_model_ir is not None
        else {}
    )
    if previous_model_ir is not None:
        missing_previous = [
            str(item["id"])
            for item in compact_subproblems
            if str(item["id"]) not in previous_by_id
        ]
        if missing_previous:
            raise ValueError(
                f"previous Model IR missing subproblems {missing_previous!r}"
            )
    generated_by_id: dict[str, ModelIR] = {}
    total = len(compact_subproblems)

    for subproblem in _dependency_order(compact_subproblems):
        target_id = str(subproblem["id"])
        required_fact_ids = list(subproblem["required_fact_ids"])
        required_uncertainty_ids = list(
            subproblem["required_uncertainty_ids"]
        )
        dependency_ids = set(subproblem["depends_on_subproblem_ids"])
        slice_spec = {
            **compact_spec,
            "subproblems": [subproblem],
            "locked_facts": [
                fact
                for fact in compact_spec["locked_facts"]
                if fact.get("fact_id") in required_fact_ids
            ],
            "uncertainties": [
                uncertainty
                for uncertainty in compact_spec["uncertainties"]
                if uncertainty.get("uncertainty_id")
                in required_uncertainty_ids
            ],
            "dependency_subproblems": [
                item
                for item in compact_subproblems
                if item["id"] in dependency_ids
            ],
            "dependency_models": {
                identifier: generated_by_id[identifier].subproblems[0].model_dump(mode="json")
                for identifier in sorted(dependency_ids)
            },
        }
        request_name = (
            agent_name
            if total == 1
            else f"{agent_name}_{safe_artifact_component(target_id)}"
        )
        try:
            blocked_dependencies = [
                identifier for identifier in sorted(dependency_ids)
                if isinstance(generated_by_id[identifier].subproblems[0], BlockedModelIR)
            ]
            if blocked_dependencies:
                raise ValueError(f"upstream subproblems are blocked: {', '.join(blocked_dependencies)}")
            generated_by_id[target_id] = await _request_single_subproblem_model(
                llm=llm,
                system_prompt=system_prompt,
                problem_spec=problem_spec,
                compact_spec=compact_spec,
                subproblem=subproblem,
                slice_spec=slice_spec,
                evidence_profile=evidence_profile,
                available_operator_ids=available_operator_ids,
                request_name=request_name,
                artifact_dir=artifact_dir,
                previous_model=previous_by_id.get(target_id),
                review=review,
                architecture_timeout=architecture_timeout,
                indexed_core_timeout=indexed_core_timeout,
                indexed_constraints_timeout=indexed_constraints_timeout,
            )
        except (RuntimeError, ValueError) as exc:
            generated_by_id[target_id] = _write_subproblem_modeling_failure(
                artifact_dir=artifact_dir,
                target_id=target_id,
                subproblem=subproblem,
                request_name=request_name,
                exc=exc,
            )

    generated = [generated_by_id[str(item["id"])] for item in compact_subproblems]
    model_ir = ModelIR(
        model_id=_merged_model_id(generated, previous_model_ir),
        subproblems=[item.subproblems[0] for item in generated],
    )
    _validate_model_ir_contract(problem_spec, model_ir, evidence_profile)
    return model_ir


def _dependency_order(subproblems: list[dict]) -> list[dict]:
    # ProblemSpec已拒绝依赖环；生成按拓扑顺序执行，最终合同仍保留题目顺序。
    by_id = {item["id"]: item for item in subproblems}
    ordered: list[dict] = []
    visited: set[str] = set()

    def visit(identifier: str) -> None:
        if identifier in visited:
            return
        for dependency in by_id[identifier]["depends_on_subproblem_ids"]:
            visit(dependency)
        visited.add(identifier)
        ordered.append(by_id[identifier])

    for item in subproblems:
        visit(item["id"])
    return ordered

def _write_subproblem_modeling_failure(
    *,
    artifact_dir: Path,
    target_id: str,
    subproblem: dict[str, Any],
    request_name: str,
    exc: Exception,
) -> ModelIR:
    component = safe_artifact_component(target_id)
    diagnostic_name = f"modeling_failure_{component}.json"
    message = str(exc)
    contract_diagnostics = [
        str(path.relative_to(artifact_dir)).replace("\\", "/")
        for path in sorted((artifact_dir / "contract_diagnostics").glob(
            f"{request_name}*.json"
        ))
    ]
    (artifact_dir / diagnostic_name).write_text(
        json.dumps({
            "failed_stage": "modeling",
            "failed_operation": "subproblem_model_ir_generation",
            "subproblem_id": target_id,
            "error_type": type(exc).__name__,
            "error": message,
            "contract_diagnostics": contract_diagnostics,
            "resume_from": "problem_spec.json",
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    reason = bounded_failure_summary(message, diagnostic_name)
    return ModelIR(
        model_id=f"blocked-{component}",
        subproblems=[BlockedModelIR(
            execution_status="blocked",
            subproblem_id=target_id,
            objective=bounded_subproblem_objective(subproblem),
            blocking_reason=reason,
            missing_inputs=["valid Model IR contract for this subproblem"],
            recovery_action=(
                "Regenerate this subproblem's Model IR from its locked facts and "
                "available evidence, then submit it for review again."
            ),
            expected_outputs=["reviewable Model IR for this subproblem"],
        )],
    )

def _merged_model_id(
    generated: list[ModelIR],
    previous_model_ir: ModelIR | None,
) -> str:
    if previous_model_ir is not None:
        return previous_model_ir.model_id
    if len(generated) == 1:
        return generated[0].model_id
    identity = "|".join(
        f"{item.subproblems[0].subproblem_id}:{item.model_id}" for item in generated
    )
    return f"merged-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16]}"
