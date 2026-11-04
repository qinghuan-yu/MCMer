"""Guidance 职责边界：model_generation。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.llm.llm import LLM
from app.guidance.contract_request import StructuredContractRequester
from app.guidance.contracts import ModelingRequirementSpec, ProblemSpec
from app.guidance.generation_validation import (
    _validate_generation_draft_slice,
    _validate_indexed_constraint_stage,
    _validate_indexed_core_stage,
    _validate_model_ir_slice,
)
from app.guidance.model_ir import ModelIR
from app.guidance.model_ir_drafts import (
    ConstraintGenerationContext,
    IndexedLinearConstraintsDraft,
    IndexedLinearCoreDraft,
    ModelIRGenerationDraft,
    SolvableModelIRDraft,
)
from app.guidance.model_review_contracts import ModelIRReview
from app.guidance.modeling_context import (
    _compact_previous_model_summary,
    _remaining_locked_facts,
    _subproblem_review_payload,
)


async def _request_model(
    *,
    llm: LLM,
    model_type,
    system_prompt: str,
    payload,
    agent_name: str,
    artifact_dir: Path,
    semantic_validator=None,
    request_timeout: int | float | None = None,
):
    requester = StructuredContractRequester(
        llm=llm,
        model_type=model_type,
        system_prompt=system_prompt,
        agent_name=agent_name,
        artifact_dir=artifact_dir,
        semantic_validator=semantic_validator,
        request_timeout=request_timeout,
    )
    return await requester.request(payload)

async def _request_single_subproblem_model(
    *,
    llm: LLM,
    system_prompt: str,
    problem_spec: ProblemSpec,
    compact_spec: dict[str, Any],
    subproblem: dict[str, Any],
    slice_spec: dict[str, Any],
    evidence_profile: dict,
    available_operator_ids: list[str],
    request_name: str,
    artifact_dir: Path,
    previous_model: Any | None,
    review: ModelIRReview | None,
    architecture_timeout: int | float | None,
    indexed_core_timeout: int | float | None,
    indexed_constraints_timeout: int | float | None,
) -> ModelIR:
    target_id = str(subproblem["id"])
    review_payload = (
        _subproblem_review_payload(review, target_id)
        if review is not None
        else None
    )
    required_fact_ids = [
        str(fact["fact_id"])
        for fact in slice_spec["locked_facts"]
    ]
    payload: dict[str, Any] = {
        **slice_spec,
        "target_subproblem_id": target_id,
        "required_fact_ids": required_fact_ids,
        "evidence_profile": evidence_profile,
        "available_operator_ids": available_operator_ids,
    }
    if previous_model is not None:
        payload["previous_model_summary"] = _compact_previous_model_summary(previous_model)
    if review_payload is not None:
        payload["model_review"] = review_payload

    draft = await _request_model(
        llm=llm,
        model_type=ModelIRGenerationDraft,
        system_prompt=(
            f"{system_prompt}\n本次只生成 target_subproblem_id={target_id} 的一个子问题；"
            "返回的 subproblems 必须恰好包含这一项，不得生成或省略其他 id。"
            "可解结果只能在实际算子节点的 source_fact_ids 登记本阶段使用的事实；"
            "禁止输出 fact_bindings，最终路径由系统组装。"
        ),
        payload=payload,
        agent_name=request_name,
        artifact_dir=artifact_dir,
        semantic_validator=lambda generation_draft: _validate_generation_draft_slice(
            generation_draft,
            expected_id=target_id,
            available_operator_ids=available_operator_ids,
            problem_spec=problem_spec,
        ),
        request_timeout=architecture_timeout,
    )
    indexed_linear_model = None
    architecture = draft.subproblems[0]
    remaining_after_architecture = (
        _remaining_locked_facts(
            slice_spec["locked_facts"],
            [
                fact_id
                for operator in architecture.operators
                for fact_id in operator.source_fact_ids
            ],
        )
        if isinstance(architecture, SolvableModelIRDraft)
        else []
    )
    if (
        isinstance(architecture, SolvableModelIRDraft)
        and "indexed_linear_model" in architecture.execution_components
    ):
        core = await _request_model(
            llm=llm,
            model_type=IndexedLinearCoreDraft,
            system_prompt=(
                "你是有限索引线性模型核心构建器。只输出索引集、参数来源、索引变量和线性目标。"
                "每个参数必须用 source_kind 判别来源：题面或附件常量使用 static、dense_values "
                "和 dense_source_ref；上游算子工件使用 runtime_artifact 和 runtime_source，"
                "静态向量的 dense_source_ref 必须使用完整已登记表格 source_ref 加精确列名；"
                "单值参数可引用单值 cell/ref 或标量 locked fact；"
                "禁止用向量首个单元格作为整列来源，也不得用文档名或模糊标签替代精确引用。"
                "其中 artifact_ref 必须精确匹配 model_architecture 中的算子输出，field_path "
                "必须定位有限数值数组。参数顺序严格按 index_sets 的笛卡尔积。"
                "每个参数的 source_fact_ids 只能登记 locked_facts 中该参数实际使用的事实；"
                "不得重复登记架构算子已覆盖的事实。"
                "目标数组表示目标项族而非展开后的标量项，"
                "重复期必须用 for_each 与 binder，一律不得逐个枚举索引值；固定 value 只用于"
                "最多四个首期、末期等边界值。不要输出约束、解释或代码。"
            ),
            payload={
                "target_subproblem": subproblem,
                "problem_context": compact_spec["task_summary"],
                "model_architecture": draft.model_dump(mode="json"),
                "locked_facts": remaining_after_architecture,
                "uncertainties": slice_spec["uncertainties"],
                "evidence_profile": evidence_profile,
                **(
                    {"model_review": review_payload}
                    if review_payload is not None
                    else {}
                ),
            },
            agent_name=f"{request_name}_indexed_core",
            artifact_dir=artifact_dir,
            semantic_validator=lambda core_draft: _validate_indexed_core_stage(
                core_draft,
                remaining_after_architecture,
                evidence_profile=evidence_profile,
            ),
            request_timeout=indexed_core_timeout,
        )
        remaining_after_core = _remaining_locked_facts(
            remaining_after_architecture,
            [
                fact_id
                for parameter in core.parameters
                for fact_id in parameter.source_fact_ids
            ],
        )
        constraint_context = ConstraintGenerationContext.from_core(core)
        requirements = [
            ModelingRequirementSpec.model_validate(item)
            for item in slice_spec["modeling_requirements"]
        ]
        constraints = await _request_model(
            llm=llm,
            model_type=IndexedLinearConstraintsDraft,
            system_prompt=(
                "你是有限索引线性模型约束构建器。只输出辅助索引集和约束数组。"
                "constraint_context 是唯一允许引用的符号目录；parameter_id、variable_id 和"
                "已有 index set id 必须逐字选自该目录，禁止另造或改写符号。"
                "constraints 数组表示约束族而非展开后的逐期约束，最多输出 50 个约束族；"
                "使用 for_each 与 binder 表达所有重复约束，禁止逐个枚举周、时段或场景；"
                "固定 value 只用于最多四个首期、末期等边界值；首期、过渡期等核心未声明的子集"
                "必须在 additional_index_sets 中声明，"
                "非零 offset 必须绑定到排除越界端点的 auxiliary index set；"
                "首期或末期边界条件必须使用独立约束族，禁止让全域 binder 携带非零 offset。"
                "每个约束的 source_fact_ids 只能登记 locked_facts 中实际使用的事实，"
                "并且全部约束合计必须覆盖本阶段收到的所有 locked_facts。"
                "modeling_requirements 的每一项必须由约束的 fulfills_requirement_ids 履行，"
                "且结构必须符合其 kind：state_conservation 用等式连接同一状态的 t 期与更早期"
                "（常数系数反号）并另设字面索引的首期边界；lagged_link 用一个变量的 t 期与另一"
                "变量的 t-lag 期联动，并为目标变量设字面索引的边界；upper_capacity 用 less_equal"
                "并引用容量参数；demand_satisfaction 用 equal 或 greater_equal 并引用需求参数；"
                "需求声明 source_fact_ids 时，所引参数必须登记同一事实。"
                "不得登记未收到的 requirement_id。"
                "不得写条件表达式、不得把参数当变量、不得输出解释或代码。"
            ),
            payload={
                "target_subproblem": subproblem,
                "problem_context": compact_spec["task_summary"],
                "model_architecture": draft.model_dump(mode="json"),
                "locked_facts": remaining_after_core,
                "uncertainties": slice_spec["uncertainties"],
                "modeling_requirements": slice_spec["modeling_requirements"],
                "constraint_context": constraint_context.model_dump(mode="json"),
                **(
                    {"model_review": review_payload}
                    if review_payload is not None
                    else {}
                ),
            },
            agent_name=f"{request_name}_indexed_constraints",
            artifact_dir=artifact_dir,
            semantic_validator=lambda constraint_draft: _validate_indexed_constraint_stage(
                core=core,
                constraints=constraint_draft,
                context=constraint_context,
                locked_facts=remaining_after_core,
                requirements=requirements,
            ),
            request_timeout=indexed_constraints_timeout,
        )
        indexed_linear_model = core.promote(
            constraints.promote_constraints(),
            constraints.additional_index_sets,
        )

    promoted = draft.promote(indexed_linear_model)
    _validate_model_ir_slice(
        promoted,
        expected_id=target_id,
        problem_spec=problem_spec,
        evidence_profile=evidence_profile,
        available_operator_ids=available_operator_ids,
    )
    return promoted
