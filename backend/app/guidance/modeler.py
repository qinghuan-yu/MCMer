"""Guidance 职责边界：modeler。"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.core.llm.llm import LLM
from app.core.workflow_budget import ContractTokenBudget
from app.guidance.contracts import ProblemSpec
from app.guidance.operators import OperatorRegistry
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
from app.guidance.model_review_validation import unresolved_source_subproblem_ids
from app.guidance.modeling_context import (
    MODEL_IR_ARCHITECTURE_CONTRACT,
    _compact_problem_spec_for_modeling,
)
from app.guidance.recovery import RecoveryCheckpoint, canonical_digest, load_source_preflight
from app.guidance.source_resolution import validate_source_preflight


class LLMModeler:
    def __init__(
        self,
        llm: LLM,
        *,
        registry: OperatorRegistry | None = None,
        architecture_timeout: int | float | None = None,
        indexed_core_timeout: int | float | None = None,
        indexed_constraints_timeout: int | float | None = None,
        architecture_token_budget: ContractTokenBudget | None = None,
        indexed_core_token_budget: ContractTokenBudget | None = None,
        indexed_constraints_token_budget: ContractTokenBudget | None = None,
    ) -> None:
        self.llm = llm
        from app.guidance.operator_catalog import build_default_operator_registry
        self.registry = registry if registry is not None else build_default_operator_registry()
        self.architecture_timeout = architecture_timeout
        self.indexed_core_timeout = indexed_core_timeout
        self.indexed_constraints_timeout = indexed_constraints_timeout
        self.architecture_token_budget = architecture_token_budget
        self.indexed_core_token_budget = indexed_core_token_budget
        self.indexed_constraints_token_budget = indexed_constraints_token_budget

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
            registry=self.registry,
            agent_name="GuidanceModeler",
            artifact_dir=work_dir,
            architecture_timeout=self.architecture_timeout,
            indexed_core_timeout=self.indexed_core_timeout,
            indexed_constraints_timeout=self.indexed_constraints_timeout,
            architecture_token_budget=self.architecture_token_budget,
            indexed_core_token_budget=self.indexed_core_token_budget,
            indexed_constraints_token_budget=self.indexed_constraints_token_budget,
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
                "其中只包含模型修复义务；来源纠正已由系统写入当前problem_spec。"
                "必须使用当前锁定事实，不得重新引入已消解的旧歧义或旧假设情景。"
                "若因上游模型变化重新构建本题，须依据最新 dependency_models 重新核对依赖关系；"
                "并使用 previous_model_summary 理解旧路线和显式 assumptions。无法在现有数据和"
                "算子能力下诚实修复的子问题才改为 blocked；不得通过虚构数据、参数或约束绕过审核。"
            ),
            problem_spec=problem_spec,
            evidence_profile=evidence_profile or {"evidence": []},
            registry=self.registry,
            agent_name="GuidanceModelReviser",
            artifact_dir=work_dir,
            previous_model_ir=previous_model_ir,
            review=review,
            architecture_timeout=self.architecture_timeout,
            indexed_core_timeout=self.indexed_core_timeout,
            indexed_constraints_timeout=self.indexed_constraints_timeout,
            architecture_token_budget=self.architecture_token_budget,
            indexed_core_token_budget=self.indexed_core_token_budget,
            indexed_constraints_token_budget=self.indexed_constraints_token_budget,
        )

async def _request_subproblem_models(
    *,
    llm: LLM,
    stage_duty: str,
    problem_spec: ProblemSpec,
    evidence_profile: dict,
    registry: OperatorRegistry,
    agent_name: str,
    artifact_dir: Path,
    previous_model_ir: ModelIR | None = None,
    review: ModelIRReview | None = None,
    architecture_timeout: int | float | None = None,
    indexed_core_timeout: int | float | None = None,
    indexed_constraints_timeout: int | float | None = None,
    architecture_token_budget: ContractTokenBudget | None = None,
    indexed_core_token_budget: ContractTokenBudget | None = None,
    indexed_constraints_token_budget: ContractTokenBudget | None = None,
) -> ModelIR:
    problem_spec = problem_spec.model_copy(deep=True)
    previous_model_ir = previous_model_ir.model_copy(deep=True) if previous_model_ir is not None else None
    review = review.model_copy(deep=True) if review is not None else None
    evidence_profile = deepcopy(evidence_profile)
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
    decisions = (
        {item.subproblem_id: item for item in review.subproblem_reviews}
        if review is not None else {}
    )
    revision_targets = {
        identifier for identifier, decision in decisions.items()
        if decision.status == "revision_required" and decision.required_revisions
    }
    contract_checkpoint = RecoveryCheckpoint(artifact_dir, "model_operator_contract", "current")
    if previous_model_ir is not None and contract_checkpoint.load({
        "model_ir": previous_model_ir.model_dump(mode="json"),
        "operator_contract_sha256": registry.contract_sha256(),
    }) is None:
        revision_targets.update(previous_by_id)
    try:
        source_preflight = load_source_preflight(artifact_dir, problem_spec)
        catalog_path = artifact_dir / "input_catalog.json"
        validate_source_preflight(
            source_preflight, problem_spec=problem_spec,
            input_catalog=json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.is_file() else None,
            evidence_profile=evidence_profile, work_dir=artifact_dir,
        )
        pending_source_ids = set(source_preflight.blocked_subproblem_ids)
        source_failure = "source interpretation did not receive independent permission"
    except (OSError, ValueError) as exc:
        source_preflight = None
        pending_source_ids = {item.id for item in problem_spec.subproblems}
        source_failure = f"source preflight unavailable: {exc}"
    if review is not None:
        pending_source_ids.update(unresolved_source_subproblem_ids(review, problem_spec))
    revision_targets.update(pending_source_ids)
    changed_models: set[str] = set()
    dependency_closures: dict[str, set[str]] = {}
    total = len(compact_subproblems)

    for subproblem in _dependency_order(compact_subproblems):
        target_id = str(subproblem["id"])
        required_fact_ids = list(subproblem["required_fact_ids"])
        required_uncertainty_ids = list(
            subproblem["required_uncertainty_ids"]
        )
        dependency_ids = set(subproblem["depends_on_subproblem_ids"])
        for identifier in tuple(dependency_ids):
            dependency_ids.update(dependency_closures[identifier])
        dependency_closures[target_id] = dependency_ids
        changed_dependencies = dependency_ids & changed_models
        if (
            previous_model_ir is not None
            and target_id not in revision_targets
            and not changed_dependencies
        ):
            generated_by_id[target_id] = ModelIR(
                model_id=previous_model_ir.model_id,
                subproblems=[previous_by_id[target_id]],
            )
            continue
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
            "source_preflight_sha256": canonical_digest(source_preflight) if source_preflight else None,
        }
        request_name = (
            agent_name
            if total == 1
            else f"{agent_name}_{safe_artifact_component(target_id)}"
        )
        try:
            if target_id in pending_source_ids:
                raise ValueError(source_failure)
            blocked_dependencies = [
                identifier for identifier in sorted(dependency_ids)
                if isinstance(generated_by_id[identifier].subproblems[0], BlockedModelIR)
            ]
            if blocked_dependencies:
                raise ValueError(f"upstream subproblems are blocked: {', '.join(blocked_dependencies)}")
            if (
                changed_dependencies
                and target_id not in revision_targets
                and decisions[target_id].status != "approved"
            ):
                raise ValueError(
                    "upstream models changed without an accepted review or explicit revision "
                    f"for this subproblem: {', '.join(sorted(changed_dependencies))}"
                )
            generated_by_id[target_id] = await _request_single_subproblem_model(
                llm=llm,
                system_prompt=system_prompt,
                problem_spec=problem_spec,
                compact_spec=compact_spec,
                subproblem=subproblem,
                slice_spec=slice_spec,
                evidence_profile=evidence_profile,
                registry=registry,
                request_name=request_name,
                artifact_dir=artifact_dir,
                previous_model=previous_by_id.get(target_id),
                review=review,
                architecture_timeout=architecture_timeout,
                indexed_core_timeout=indexed_core_timeout,
                indexed_constraints_timeout=indexed_constraints_timeout,
                architecture_token_budget=architecture_token_budget,
                indexed_core_token_budget=indexed_core_token_budget,
                indexed_constraints_token_budget=indexed_constraints_token_budget,
            )
        except (RuntimeError, ValueError) as exc:
            generated_by_id[target_id] = _write_subproblem_modeling_failure(
                artifact_dir=artifact_dir,
                target_id=target_id,
                subproblem=subproblem,
                request_name=request_name,
                exc=exc,
            )
        # 中间模型即使内容相同，其上游发生变化也须继续使后继模型失效。
        if previous_model_ir is not None and (
            changed_dependencies
            or generated_by_id[target_id].subproblems[0] != previous_by_id[target_id]
        ):
            changed_models.add(target_id)

    generated = [generated_by_id[str(item["id"])] for item in compact_subproblems]
    model_ir = ModelIR(
        model_id=_merged_model_id(generated, previous_model_ir),
        subproblems=[item.subproblems[0] for item in generated],
    )
    _validate_model_ir_contract(problem_spec, model_ir, evidence_profile)
    contract_checkpoint.save({
        "model_ir": model_ir.model_dump(mode="json"),
        "operator_contract_sha256": registry.contract_sha256(),
    }, {"validated": True})
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
