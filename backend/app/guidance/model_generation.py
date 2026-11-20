"""Guidance 职责边界：model_generation。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field, RootModel, field_validator

from app.core.llm.llm import LLM
from app.core.workflow_budget import ContractTokenBudget
from app.guidance.contract_request import ContractRetryBudget, StructuredContractRequester
from app.guidance.contracts import ProblemSpec
from app.guidance.operators import OperatorRegistry
from app.guidance.fact_coverage import FactCoverage, FactScope
from app.guidance.generation_validation import (
    _validate_generation_draft_slice,
    _validate_indexed_constraint_stage,
    _validate_indexed_core_stage,
    _validate_model_ir_slice,
)
from app.guidance.model_ir import IRModel, ModelIR
from app.guidance.model_ir_drafts import (
    FormulationContext,
    IndexedLinearFormulationDraft,
    IndexedLinearCoreDraft,
    ModelIRGenerationDraft,
    SolvableModelIRDraft,
)
from app.guidance.model_review_contracts import ModelIRReview
from app.guidance.modeling_context import (
    _compact_previous_model_summary,
    _subproblem_review_payload,
)


class OperatorLookup(IRModel):
    inspect_operators: list[str] = Field(min_length=1, max_length=20)

    @field_validator("inspect_operators")
    @classmethod
    def require_distinct_ids(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError(f"duplicate operator IDs: {value}")
        return value


class ModelProposal(ModelIRGenerationDraft):
    indexed_data: IndexedLinearCoreDraft | None = None


class ModelingTurn(RootModel[OperatorLookup | ModelProposal]):
    """模型可直接提交候选，也可先读取一次注册算子的详细合同。"""


BINDING_INSTRUCTIONS = (
    '可先返回 {"inspect_operators":["算子ID"]} 批量查询一次，再直接提交候选。'
    "选择 indexed_linear_model 时，在同一对象的 indexed_data 提交索引集和参数来源；"
    "其余候选省略 indexed_data。变量、目标与约束在收到实际参数后提交。"
    "每个参数必须用 source_kind 判别来源：静态数据使用 static 和 binding，"
    "数值由程序读取，禁止输出 dense_values、dense_source_ref 或自行抄写数组。"
    "binding.kind=fact 只选择 locked_facts 中的 fact_id，标量按索引广播，系统登记该事实；"
    "table_column 使用完整已登记表格 source_ref 和精确列名 column，首行必须是表头，"
    "同名索引列存在时按索引标签对齐；table_range 选择原始表格的矩形区域，"
    "row_start/row_stop、column_start/column_stop 使用零基、右开范围，包含原始表头，"
    "order 明确选择 row_major 或 column_major；只有一个单元格时起止仍相差1。"
    "选择的单元格数量必须等于参数索引笛卡尔积大小，禁止用首个单元格代表整列。"
    "有单位后缀的单元格须在 unit 精确声明后缀，如元/个；百分数转换为比例。"
    "纯数值单元格的unit必须为null，不能用表头或题面单位代替单元格后缀。"
    "source_ref 必须逐字选择 evidence_profile 已登记表根，不能自行拼接切片路径。"
    "上游算子工件使用 runtime_artifact 和 runtime_source，"
    "其中 artifact_ref 必须精确匹配 本候选中声明的算子输出，field_path "
    "必须定位有限数值数组。参数顺序严格按 index_sets 的笛卡尔积。"
    "每个参数的 source_fact_ids 只能登记 locked_facts 中该参数实际使用的事实；"
    "可重复引用架构已使用的事实；fact_coverage 仅记录覆盖，不限制引用。"
)

FORMULATION_INSTRUCTIONS = (
    "你是有限索引线性模型数学构建器。共同输出变量声明、线性目标、辅助索引集和约束数组。"
    "变量目录由本阶段定义，必须包含表达全部资源占用、活动和计费关系所需的变量；"
    "架构中的文字变量仅作设计参考，不是完备目录。"
    "目标项必须与本阶段约束对每个变量的实际定义一致，逐项核对所有付费参与者、状态和成本，"
    "不能用只含部分参与者的数量冒充总计费量。可用能力与实际发生量必须区分："
    "实际发生量受已知需求或活动限制，不能靠虚构超需求活动或无对象活动降低闲置费用。"
    "目标数组表示目标项族而非展开后的标量项，重复期必须用for_each与binder，"
    "固定value只用于最多四个首期、末期等边界值。"
    "constraint_context 是实际执行参数的已验证快照；values/dense_values 已完成来源读取和单位转换，"
    "百分数已转换为比例（例如28%对应0.28），不得再次乘0.01或除100。"
    "source_unit 仅记录原单元格单位，不是要求再次转换；按快照数值构造数学关系。"
    "runtime_source 表示尚待上游执行的工件引用，不得捏造尚未生成的数值。"
    "parameter_id和已有index set id"
    "必须逐字选自该目录，禁止另造或改写参数；variable_id必须引用本次variables中的声明。"
    "constraints 数组表示约束族而非展开后的逐期约束，最多输出 50 个约束族；"
    "使用 for_each 与 binder 表达所有重复约束，禁止逐个枚举周、时段或场景；"
    "固定 value 只用于最多四个首期、末期等边界值；首期、过渡期等核心未声明的子集"
    "必须在 additional_index_sets 中声明，"
    "非零 offset 必须绑定到排除越界端点的 auxiliary index set；"
    "首期或末期边界条件必须使用独立约束族，禁止让全域 binder 携带非零 offset。"
    "每个约束的 source_fact_ids 只能登记 locked_facts 中实际使用的事实，"
    "可重复引用架构和参数已使用的事实；全部阶段合计必须覆盖所有事实，"
    "本阶段必须补齐 fact_coverage.uncovered_fact_ids，但不得只依据该列表建约束。"
    "不得写条件表达式、不得把参数当变量、不得输出解释或代码。"
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
    checkpoint_context: Any = None,
    token_budget: ContractTokenBudget | None = None,
    retry_budget: ContractRetryBudget | None = None,
):
    requester = StructuredContractRequester(
        llm=llm,
        model_type=model_type,
        system_prompt=system_prompt,
        agent_name=agent_name,
        artifact_dir=artifact_dir,
        semantic_validator=semantic_validator,
        request_timeout=request_timeout,
        checkpoint_context=checkpoint_context,
        token_budget=token_budget,
    )
    return await requester.request(payload, retry_budget=retry_budget)


async def _request_single_subproblem_model(
    *,
    llm: LLM,
    system_prompt: str,
    problem_spec: ProblemSpec,
    compact_spec: dict[str, Any],
    subproblem: dict[str, Any],
    slice_spec: dict[str, Any],
    evidence_profile: dict,
    registry: OperatorRegistry,
    request_name: str,
    artifact_dir: Path,
    previous_model: Any | None,
    review: ModelIRReview | None,
    architecture_timeout: int | float | None,
    indexed_core_timeout: int | float | None,
    indexed_constraints_timeout: int | float | None,
    architecture_token_budget: ContractTokenBudget | None = None,
    indexed_core_token_budget: ContractTokenBudget | None = None,
    indexed_constraints_token_budget: ContractTokenBudget | None = None,
) -> ModelIR:
    target_id = str(subproblem["id"])
    review_payload = (
        _subproblem_review_payload(review, target_id)
        if review is not None
        else None
    )
    fact_scope = FactScope.from_problem_spec(problem_spec, target_id)
    coverage = FactCoverage(fact_scope)
    operator_digest = registry.contract_sha256()
    payload: dict[str, Any] = {
        **slice_spec,
        "target_subproblem_id": target_id,
        "locked_facts": fact_scope.payload(),
        "required_fact_ids": list(fact_scope.fact_ids),
        "fact_coverage": coverage.payload(),
        "evidence_profile": evidence_profile,
        "available_operators": registry.model_catalog(),
        "operator_contract_sha256": operator_digest,
    }
    if previous_model is not None:
        payload["previous_model_summary"] = _compact_previous_model_summary(previous_model)
    if review_payload is not None:
        payload["model_review"] = review_payload
    dependencies = {
        "operator_contract_sha256": operator_digest,
        "upstream_models": slice_spec.get("dependency_models", {}),
        "previous_model": previous_model.model_dump(mode="json") if previous_model is not None else None,
        "source_preflight_sha256": slice_spec.get("source_preflight_sha256"),
    }

    retries = ContractRetryBudget()
    materialized_core = None
    candidate = None

    def accept_model(model: ModelIR) -> None:
        nonlocal candidate
        _validate_model_ir_slice(
            model, expected_id=target_id, problem_spec=problem_spec,
            evidence_profile=evidence_profile, registry=registry,
        )
        candidate = model

    def validate_proposal(proposal: ModelProposal) -> None:
        nonlocal coverage, materialized_core
        proposed_coverage = _validate_generation_draft_slice(
            proposal, expected_id=target_id, registry=registry, problem_spec=problem_spec,
        )
        sub = proposal.subproblems[0]
        indexed = isinstance(sub, SolvableModelIRDraft) and "indexed_linear_model" in sub.execution_components
        if indexed != (proposal.indexed_data is not None):
            raise ValueError("indexed_data is required exactly when indexed_linear_model is selected")
        if proposal.indexed_data is None:
            accept_model(proposal.promote())
            return
        core = _validate_indexed_core_stage(proposal.indexed_data, proposed_coverage, evidence_profile=evidence_profile)
        coverage = proposed_coverage.with_stage(
            "indexed_core", ((p.id, p.source_fact_ids) for p in core.parameters),
        )
        materialized_core = core

    def validate_turn(turn: ModelingTurn) -> None:
        if isinstance(turn.root, OperatorLookup):
            for operator_id in turn.root.inspect_operators:
                if registry.get(operator_id) is None:
                    raise ValueError(f"unknown operator: {operator_id}")
        else:
            validate_proposal(turn.root)

    # 合并原架构与参数提交，沿用两者较大的单次上限。
    budgets = [b for b in (architecture_token_budget, indexed_core_token_budget) if b is not None]
    proposal_budget = (
        ContractTokenBudget(max(b.initial for b in budgets), max(b.ceiling for b in budgets))
        if budgets else None
    )
    proposal_timeout = max((t for t in (architecture_timeout, indexed_core_timeout) if t is not None), default=None)
    prompt = (
        f"{system_prompt}\n只建模 target_subproblem_id={target_id}。算子输出须声明为变量或最终工件。\n"
        + BINDING_INSTRUCTIONS
    )
    common = dict(llm=llm, artifact_dir=artifact_dir, retry_budget=retries)
    proposal_limits = dict(request_timeout=proposal_timeout, token_budget=proposal_budget, checkpoint_context=dependencies)
    turn = await _request_model(
        **common, **proposal_limits, model_type=ModelingTurn, system_prompt=prompt,
        payload=payload, agent_name=request_name, semantic_validator=validate_turn,
    )
    draft = turn.root
    if isinstance(draft, OperatorLookup):
        contracts = registry.model_contracts(draft.inspect_operators)
        draft = await _request_model(
            **common, **proposal_limits, model_type=ModelProposal,
            system_prompt=prompt + "\n算子查询已完成，请直接提交候选。",
            payload={**payload, "operator_contracts": contracts},
            agent_name=f"{request_name}_proposal", semantic_validator=validate_proposal,
        )

    if materialized_core is not None:
        context = FormulationContext.from_core(materialized_core)

        def validate_formulation(formulation: IndexedLinearFormulationDraft) -> None:
            _validate_indexed_constraint_stage(
                core=materialized_core, constraints=formulation, context=context, coverage=coverage,
            )
            accept_model(draft.promote(formulation.promote(materialized_core)))

        await _request_model(
            **common, model_type=IndexedLinearFormulationDraft, system_prompt=FORMULATION_INSTRUCTIONS,
            payload={
                "target_subproblem": subproblem,
                "problem_context": compact_spec["task_summary"],
                "model_architecture": draft.model_dump(mode="json", exclude={"indexed_data"}),
                "locked_facts": fact_scope.payload(),
                "fact_coverage": coverage.payload(),
                "uncertainties": slice_spec["uncertainties"],
                "constraint_context": context.model_dump(mode="json"),
                **({"model_review": review_payload} if review_payload is not None else {}),
            },
            agent_name=f"{request_name}_indexed_constraints", semantic_validator=validate_formulation,
            request_timeout=indexed_constraints_timeout, token_budget=indexed_constraints_token_budget,
            checkpoint_context={**dependencies, "proposal": draft.model_dump(mode="json"),
                                "materialized_core": materialized_core.model_dump(mode="json")},
        )

    if candidate is None:
        raise RuntimeError("no validated model was submitted")
    return candidate
