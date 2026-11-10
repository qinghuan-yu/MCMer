"""Guidance 职责边界：model_generation。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.llm.llm import LLM
from app.core.workflow_budget import ContractTokenBudget
from app.guidance.contract_request import StructuredContractRequester
from app.guidance.contracts import ProblemSpec
from app.guidance.operators import OperatorRegistry
from app.guidance.fact_coverage import FactCoverage, FactScope
from app.guidance.generation_validation import (
    _validate_generation_draft_slice,
    _validate_indexed_constraint_stage,
    _validate_indexed_core_stage,
    _validate_model_ir_slice,
)
from app.guidance.model_ir import ModelIR
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
    payload: dict[str, Any] = {
        **slice_spec,
        "target_subproblem_id": target_id,
        "locked_facts": fact_scope.payload(),
        "required_fact_ids": list(fact_scope.fact_ids),
        "fact_coverage": coverage.payload(),
        "evidence_profile": evidence_profile,
        "available_operators": registry.model_contracts(),
        "operator_contract_sha256": registry.contract_sha256(),
    }
    if previous_model is not None:
        payload["previous_model_summary"] = _compact_previous_model_summary(previous_model)
    if review_payload is not None:
        payload["model_review"] = review_payload
    dependencies = {
        "operator_contract_sha256": registry.contract_sha256(),
        "upstream_models": slice_spec.get("dependency_models", {}),
        "previous_model": previous_model.model_dump(mode="json") if previous_model is not None else None,
        "source_preflight_sha256": slice_spec.get("source_preflight_sha256"),
    }

    draft = await _request_model(
        llm=llm,
        model_type=ModelIRGenerationDraft,
        system_prompt=(
            f"{system_prompt}\n本次只生成 target_subproblem_id={target_id} 的一个子问题；"
            "返回的 subproblems 必须恰好包含这一项，不得生成或省略其他 id。"
            "可解结果只能在实际算子节点的 source_fact_ids 登记本阶段使用的事实；"
            "事实可被后续参数和约束重复引用，覆盖登记不会消耗事实。"
            "禁止输出 fact_bindings，最终路径由系统组装。"
            "算子 outputs 按端口顺序填写已声明的工件别名，不能直接抄端口名；"
            "中间工件须在架构 variables 中声明，参数阶段不能补救未声明的输出。"
        ),
        payload=payload,
        agent_name=request_name,
        artifact_dir=artifact_dir,
        semantic_validator=lambda generation_draft: _validate_generation_draft_slice(
            generation_draft,
            expected_id=target_id,
            registry=registry,
            problem_spec=problem_spec,
        ),
        request_timeout=architecture_timeout,
        token_budget=architecture_token_budget,
        checkpoint_context=dependencies,
    )
    indexed_linear_model = None
    architecture = draft.subproblems[0]
    if isinstance(architecture, SolvableModelIRDraft):
        coverage = coverage.with_stage(
            "architecture",
            ((operator.node_id, operator.source_fact_ids) for operator in architecture.operators),
        )
    if (
        isinstance(architecture, SolvableModelIRDraft)
        and "indexed_linear_model" in architecture.execution_components
    ):
        core = await _request_model(
            llm=llm,
            model_type=IndexedLinearCoreDraft,
            system_prompt=(
                "你是有限索引线性模型数据构建器。只输出索引集和参数来源。"
                "此阶段禁止输出变量、目标函数或约束；三者将在下一阶段共同构造，保持数学定义一致。"
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
                "其中 artifact_ref 必须精确匹配 model_architecture 中的算子输出，field_path "
                "必须定位有限数值数组。参数顺序严格按 index_sets 的笛卡尔积。"
                "每个参数的 source_fact_ids 只能登记 locked_facts 中该参数实际使用的事实；"
                "可重复引用架构已使用的事实；fact_coverage 仅记录覆盖，不限制引用。"
                "不要输出解释或代码。"
            ),
            payload={
                "target_subproblem": subproblem,
                "problem_context": compact_spec["task_summary"],
                "model_architecture": draft.model_dump(mode="json"),
                "locked_facts": fact_scope.payload(),
                "fact_coverage": coverage.payload(),
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
                coverage,
                evidence_profile=evidence_profile,
            ),
            request_timeout=indexed_core_timeout,
            token_budget=indexed_core_token_budget,
            checkpoint_context=dependencies,
        )
        materialized_core = core.promote_core(
            locked_facts=fact_scope.payload(), evidence_profile=evidence_profile,
        )
        coverage = coverage.with_stage(
            "indexed_core",
            ((parameter.id, parameter.source_fact_ids) for parameter in materialized_core.parameters),
        )
        constraint_context = FormulationContext.from_core(materialized_core)

        def validate_formulation(formulation: IndexedLinearFormulationDraft) -> None:
            _validate_indexed_constraint_stage(
                core=materialized_core,
                constraints=formulation,
                context=constraint_context,
                coverage=coverage,
            )
            # 完整语义检查仍由原owner执行，在请求内反馈才能触发合同重试。
            _validate_model_ir_slice(
                draft.promote(formulation.promote(materialized_core)),
                expected_id=target_id,
                problem_spec=problem_spec,
                evidence_profile=evidence_profile,
                registry=registry,
            )

        constraints = await _request_model(
            llm=llm,
            model_type=IndexedLinearFormulationDraft,
            system_prompt=(
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
            ),
            payload={
                "target_subproblem": subproblem,
                "problem_context": compact_spec["task_summary"],
                "model_architecture": draft.model_dump(mode="json"),
                "locked_facts": fact_scope.payload(),
                "fact_coverage": coverage.payload(),
                "uncertainties": slice_spec["uncertainties"],
                "constraint_context": constraint_context.model_dump(mode="json"),
                **(
                    {"model_review": review_payload}
                    if review_payload is not None
                    else {}
                ),
            },
            agent_name=f"{request_name}_indexed_constraints",
            artifact_dir=artifact_dir,
            semantic_validator=validate_formulation,
            request_timeout=indexed_constraints_timeout,
            token_budget=indexed_constraints_token_budget,
            checkpoint_context={
                **dependencies,
                "architecture": draft.model_dump(mode="json"),
                "materialized_core": materialized_core.model_dump(mode="json"),
            },
        )
        indexed_linear_model = constraints.promote(materialized_core)

    promoted = draft.promote(indexed_linear_model)
    _validate_model_ir_slice(
        promoted,
        expected_id=target_id,
        problem_spec=problem_spec,
        evidence_profile=evidence_profile,
        registry=registry,
    )
    return promoted
