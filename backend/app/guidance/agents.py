"""Single-call LLM ports for the six-stage guidance pipeline."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from pathlib import Path

from app.core.llm.llm import LLM
from app.guidance.contract_request import StructuredContractRequester
from app.guidance.contracts import (
    ProblemFactsGenerationDraft,
    ProblemSpec,
    ProblemStructureGenerationDraft,
)
from app.guidance.model_ir import (
    BlockedModelIR,
    ConstraintGenerationContext,
    IndexedLinearExecutionComponent,
    IndexedLinearConstraintsDraft,
    IndexedLinearCoreDraft,
    ModelIR,
    ModelIRGenerationDraft,
    ModelIRReview,
    OperatorGraphExecutionComponent,
    SolvableModelIR,
    SolvableModelIRDraft,
)
from app.guidance.model_ir_semantics import (
    ModelIRSemanticValidator,
    validate_model_fact_coverage,
)
from app.guidance.model_review_validation import validate_model_review
from app.guidance.indexed_linear import (
    expand_indexed_linear_model,
    validate_indexed_linear_core,
    validate_indexed_linear_model_structure,
)
from app.guidance.evidence_consistency import (
    validate_model_evidence_consistency,
    validate_problem_evidence_consistency,
)
from app.guidance.failure_contracts import (
    bounded_failure_summary,
    bounded_subproblem_objective,
    safe_artifact_component,
)


MODEL_IR_ARCHITECTURE_CONTRACT = """只输出符合目标 Schema 的短架构 JSON，不输出方程、索引模型或代码。逐一覆盖 problem_spec 的全部子问题，subproblem_id 必须精确使用输入 id。可解子问题必须声明 execution_components、数据引用、显式 assumptions、推荐模型与简单基线、验证计划和证据绑定的最终答案；架构阶段禁止输出 fact_bindings 或预猜任何 canonical JSON 路径。每个实际使用题面事实的算子节点必须在 source_fact_ids 中登记 locked_facts 的 fact_id；没有使用事实的算子登记空数组，纯 operator_graph 必须由这些节点覆盖全部 required_fact_ids。题面锁定事实不是假设，不得复制到 assumptions；没有额外假设时 assumptions 必须为 []。每条 assumption 的 alternative_scenarios 和 sensitivity_checks 必须非空。题意歧义但可用替代情景检验时，不得误报算子不足；应登记 problem_ambiguity 假设，其 source_refs 必须逐项精确使用 problem_spec.json#uncertainties/<uncertainty_id>，禁止使用数组索引或集合级简写；同时列出受影响模型元素、替代情景和敏感性检查，最终结论只能是条件解。有限索引线性/整数优化必须在 execution_components 中包含 indexed_linear_model，系统会在后续合同中构建 indexed_linear_model 与 optimization.milp，架构阶段禁止自行输出 optimization.milp。不含 operator_graph 时 operators 和 candidate_models.operator_node_ids 必须为空，由系统追加 MILP 节点。若索引模型还依赖预测、估计或数据变换的输出，execution_components 必须同时包含 operator_graph，并用已注册算子显式产出供索引参数绑定的工件；混合执行图中，只使用系统 MILP 的候选保持 operator_node_ids 为空，需要上游工件的候选只引用实际存在的上游节点，系统随后为全部候选追加 MILP 节点。operator_id 必须精确来自 available_operator_ids。禁止 free-text equations、Python、solve.py、自创算子、题名或题号路由。只建模目标子问题明确要求的范围。evidence_profile 中登记的来源视为实际存在；source_requirements 中 status=missing 的来源才是确定缺失。数据、物理关系或算子不足时输出 blocked，区分 missing_inputs 与缺失算子并给出 recovery_action 和 expected_outputs；禁止用经验常数或正则化伪造可解性。"""

_FACT_EXTRACTION_TABLE_CELL_LIMIT = 64


class LLMProblemDecomposer:
    def __init__(
        self,
        llm: LLM,
        *,
        request_timeout: int | float | None = None,
    ) -> None:
        self.llm = llm
        self.request_timeout = request_timeout

    async def decompose(
        self,
        *,
        question: str,
        evidence_profile: dict,
        input_catalog: dict,
        task: dict,
    ) -> ProblemSpec:
        structure_requester = StructuredContractRequester(
            llm=self.llm,
            model_type=ProblemStructureGenerationDraft,
            system_prompt="""你是数学建模拆题专家。只输出题目结构 JSON，不输出 locked_facts、uncertainties、required_fact_ids 或 required_uncertainty_ids。拆出全部子问题、目标、对象、输入、约束、输出要求、问题类型、候选方向、数据来源与来源需求。每个子问题必须显式填写 depends_on_subproblem_ids，只登记题意要求的真实前置关系，不得根据描述猜测事实标识符。data_sources 只登记 evidence_profile.evidence 中的结构化表格或上传文件，其 source_ref 必须精确使用已登记值；普通题面正文不是 data source。evidence_profile 中登记的来源不得写成缺失。不得提出最终参数。""",
            agent_name="GuidanceDecomposerStructure",
            artifact_dir=_task_artifact_dir(task),
            enrich_payload=lambda payload: {**payload, "task_summary": question},
            request_timeout=self.request_timeout,
        )
        structure = await structure_requester.request({
            "question": question,
            "evidence_profile": evidence_profile,
            "input_catalog": input_catalog,
        })
        structure = _project_registered_data_sources(
            structure,
            evidence_profile,
        )
        facts_requester = StructuredContractRequester(
            llm=self.llm,
            model_type=ProblemFactsGenerationDraft,
            system_prompt="""你是数学建模题面事实审计专家。只输出事实与歧义 JSON，不重复题目结构。题面正文和短参数表中的每个数值条件、时间范围、比例、边界、成本和离散规则都必须登记为 locked fact，不能只留在子问题描述中。大型表格或时序数据必须作为一个数据集级 locked fact 登记并引用其 source_ref，不得逐行或逐单元格展开；content_omitted=true 表示原始单元格完整保存在正式 InputCatalog/EvidenceProfile 中。每条 locked fact 或 uncertainty 必须使用稳定 ID、规范化 semantic_key/value，明确列出适用的 subproblem_ids，并逐项精确引用 input_catalog.blocks 或 evidence_profile.evidence 中已登记的 source_ref；同一 semantic_key 在重叠作用域内不得给出冲突值。data_sources 的每个 source_ref 必须由至少一个 locked fact 或 uncertainty 引用。不得把 data_source.id 当作 fact_id，不得输出 required_fact_ids 或 required_uncertainty_ids；系统会根据 subproblem_ids 确定性投影这些引用。不得提出题面没有给出的最终参数。""",
            agent_name="GuidanceDecomposerFacts",
            artifact_dir=_task_artifact_dir(task),
            semantic_validator=lambda facts: validate_problem_evidence_consistency(
                structure.promote(facts),
                evidence_profile,
                input_catalog,
            ),
            request_timeout=self.request_timeout,
        )
        facts = await facts_requester.request({
            "subproblems": [
                item.model_dump(mode="json")
                for item in structure.subproblems
            ],
            "data_sources": [
                item.model_dump(mode="json")
                for item in structure.data_sources
            ],
            "evidence_profile": _compact_fact_evidence_profile(evidence_profile),
            "input_catalog": _compact_fact_input_catalog(input_catalog),
        })
        return structure.promote(facts)


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


class LLMModelReviewer:
    def __init__(
        self,
        llm: LLM,
        *,
        request_timeout: int | float | None = None,
    ) -> None:
        self.llm = llm
        self.request_timeout = request_timeout

    async def review(
        self,
        *,
        problem_spec: ProblemSpec,
        model_ir: ModelIR,
        revision_request: ModelIRReview | None = None,
        evidence_profile: dict | None = None,
        task: dict,
        work_dir: Path,
    ) -> ModelIRReview:
        _validate_model_ir_semantics(model_ir)
        return await _request_model(
            llm=self.llm,
            model_type=ModelIRReview,
            system_prompt="""你是独立 ModelIR 审核员。只输出 ModelIRReview JSON。严格审核强类型表达式、可辨识性、维度/单位、数据证据、算子前提、约束和计算可行性。subproblem_reviews 必须按 ModelIR 顺序逐题覆盖且不重不漏；每题独立决定 approved、revision_required 或 blocked，禁止用一题的问题阻断其他可解题。原本就是 blocked 的 ModelIR 子题必须保持 blocked，并保留具体 blocking_issues；只有 solvable 且本题审核通过的子题才能 approved。不要自行决定整体 status 或 executable_subproblem_ids，它们由系统从逐题决定确定性派生。逐项输出 assumption_checks：核对假设依据、受影响模型元素、替代情景和敏感性检查；没有假设时必须输出 []。首次要求修订时，在对应 subproblem_review.required_revisions 逐项给出稳定 requirement_id、description 和 acceptance_criteria，所有 revision_checks 必须为空。复审时在对应 subproblem_review.revision_checks 逐项使用相同 requirement_id 登记处置状态，并用 RFC 6901 model_paths 引用修订后 ModelIR 中实际存在的节点；任何要求未接受时该子题不得 approved。存在未接受假设的子题不得 approved，也不得把条件解写成题设唯一结论。不得把程序可运行等同于模型充分。若 IR 虚构输入、缺少基线或证据绑定、依赖自由文本公式或隐式代码，仅将受影响子题标为 revision_required 或 blocked。""",
            payload={
                "problem_spec": problem_spec.model_dump(mode="json"),
                "model_ir": model_ir.model_dump(mode="json"),
                "evidence_profile": evidence_profile or {"evidence": []},
                **(
                    {"revision_request": revision_request.model_dump(mode="json")}
                    if revision_request is not None
                    else {}
                ),
            },
            agent_name="GuidanceModelReviewer",
            artifact_dir=work_dir,
            semantic_validator=lambda review: validate_model_review(
                review,
                model_ir,
                revision_request=revision_request,
            ),
            request_timeout=self.request_timeout,
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
    generated: list[ModelIR] = []
    total = len(compact_subproblems)

    for subproblem in compact_subproblems:
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
        }
        request_name = (
            agent_name
            if total == 1
            else f"{agent_name}_{safe_artifact_component(target_id)}"
        )
        try:
            generated.append(await _request_single_subproblem_model(
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
            ))
        except (RuntimeError, ValueError) as exc:
            generated.append(_write_subproblem_modeling_failure(
                artifact_dir=artifact_dir,
                target_id=target_id,
                subproblem=subproblem,
                request_name=request_name,
                exc=exc,
            ))

    model_ir = ModelIR(
        model_id=_merged_model_id(generated, previous_model_ir),
        subproblems=[item.subproblems[0] for item in generated],
    )
    _validate_model_ir_contract(problem_spec, model_ir, evidence_profile)
    return model_ir


def _subproblem_review_payload(
    review: ModelIRReview,
    target_id: str,
) -> dict[str, Any]:
    decisions = [
        item
        for item in review.subproblem_reviews
        if item.subproblem_id == target_id
    ]
    if len(decisions) != 1:
        raise ValueError(
            f"model review must contain exactly one decision for {target_id!r}"
        )
    decision = decisions[0]
    payload = review.model_dump(mode="json")
    payload["subproblem_reviews"] = [decision.model_dump(mode="json")]
    payload["status"] = decision.status
    payload["executable_subproblem_ids"] = (
        [target_id] if decision.status == "approved" else []
    )
    return payload


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
                "不得写条件表达式、不得把参数当变量、不得输出解释或代码。"
            ),
            payload={
                "target_subproblem": subproblem,
                "problem_context": compact_spec["task_summary"],
                "model_architecture": draft.model_dump(mode="json"),
                "locked_facts": remaining_after_core,
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
            semantic_validator=lambda constraint_draft: _validate_indexed_constraint_stage(
                core=core,
                constraints=constraint_draft,
                context=constraint_context,
                locked_facts=remaining_after_core,
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


def _remaining_locked_facts(
    locked_facts: list[dict[str, Any]],
    source_fact_ids: list[str],
) -> list[dict[str, Any]]:
    known_ids = {str(fact["fact_id"]) for fact in locked_facts}
    unknown_ids = sorted(set(source_fact_ids) - known_ids)
    if unknown_ids:
        raise ValueError(f"unknown locked fact ids: {unknown_ids!r}")
    consumed_ids = set(source_fact_ids)
    return [
        fact
        for fact in locked_facts
        if str(fact["fact_id"]) not in consumed_ids
    ]


def _validate_indexed_core_stage(
    core: IndexedLinearCoreDraft,
    locked_facts: list[dict[str, Any]],
) -> None:
    _validate_indexed_core_draft(core)
    _remaining_locked_facts(
        locked_facts,
        [
            fact_id
            for parameter in core.parameters
            for fact_id in parameter.source_fact_ids
        ],
    )


def _validate_indexed_constraint_stage(
    *,
    core: IndexedLinearCoreDraft,
    constraints: IndexedLinearConstraintsDraft,
    context: ConstraintGenerationContext,
    locked_facts: list[dict[str, Any]],
) -> None:
    _validate_indexed_parts(core, constraints, context)
    remaining = _remaining_locked_facts(
        locked_facts,
        [
            fact_id
            for constraint in constraints.constraints
            for fact_id in constraint.source_fact_ids
        ],
    )
    if remaining:
        raise ValueError(
            "fact coverage mismatch in indexed constraints: "
            f"uncovered={[fact['fact_id'] for fact in remaining]!r}"
        )


def _validate_generation_draft_slice(
    draft: ModelIRGenerationDraft,
    *,
    expected_id: str,
    available_operator_ids: list[str],
    problem_spec: ProblemSpec,
) -> None:
    identifiers = [item.subproblem_id for item in draft.subproblems]
    if identifiers != [expected_id]:
        raise ValueError(
            f"subproblem slice mismatch: expected={[expected_id]!r}; modeled={identifiers!r}"
        )
    subproblem = draft.subproblems[0]
    if not isinstance(subproblem, SolvableModelIRDraft):
        return
    expected_facts = [
        fact.model_dump(mode="json")
        for fact in problem_spec.locked_facts
        if expected_id in fact.subproblem_ids
    ]
    remaining_facts = _remaining_locked_facts(
        expected_facts,
        [
            fact_id
            for operator in subproblem.operators
            for fact_id in operator.source_fact_ids
        ],
    )
    if (
        "indexed_linear_model" not in subproblem.execution_components
        and remaining_facts
    ):
        raise ValueError(
            f"fact coverage mismatch for {expected_id!r}: "
            f"uncovered={[fact['fact_id'] for fact in remaining_facts]!r}"
        )
    components = set(subproblem.execution_components)
    if "indexed_linear_model" in components:
        if "optimization.milp" not in available_operator_ids:
            raise ValueError("optimization.milp is not available")
    if any(
        item.operator_id == "optimization.milp"
        for item in subproblem.operators
    ):
        raise ValueError(
            "optimization.milp is system-managed for indexed_linear_model"
        )
    unsupported = sorted({
        item.operator_id for item in subproblem.operators
        if item.operator_id not in set(available_operator_ids)
    })
    if unsupported:
        raise ValueError(f"operator_id is not available: {unsupported!r}")


def _validate_indexed_parts(
    core: IndexedLinearCoreDraft,
    constraints: IndexedLinearConstraintsDraft,
    context: ConstraintGenerationContext,
) -> None:
    _validate_indexed_constraints_compactness(core, constraints)
    indexed_model = core.promote(
        constraints.promote_constraints(),
        constraints.additional_index_sets,
    )
    issues = validate_indexed_linear_model_structure(indexed_model)
    if issues:
        details = "; ".join(f"{item.code}: {item.message}" for item in issues)
        parameter_ids = [item.id for item in context.parameters]
        variable_ids = [item.id for item in context.variables]
        index_set_ids = [item.id for item in context.index_sets]
        raise ValueError(
            f"indexed linear expansion failed: {details}; "
            f"allowed parameter ids={parameter_ids!r}; "
            f"allowed variable ids={variable_ids!r}; "
            f"allowed index set ids={index_set_ids!r}"
        )

def _validate_indexed_core_draft(core: IndexedLinearCoreDraft) -> None:
    _validate_indexed_core_compactness(core)
    issues = validate_indexed_linear_core(core.promote_core())
    if issues:
        details = "; ".join(f"{item.code}: {item.message}" for item in issues)
        raise ValueError(f"indexed linear core validation failed: {details}")


def _validate_indexed_core_compactness(core: IndexedLinearCoreDraft) -> None:
    _validate_compact_literal_references(core, [core.objective.terms])


def _validate_indexed_constraints_compactness(
    core: IndexedLinearCoreDraft,
    constraints: IndexedLinearConstraintsDraft,
) -> None:
    _validate_compact_literal_references(
        core,
        [
            constraint.terms
            for constraint in constraints.constraints
            if not constraint.for_each
        ],
    )


def _validate_compact_literal_references(
    core: IndexedLinearCoreDraft,
    term_groups: list[list[Any]],
) -> None:
    index_sets = {item.id: item for item in core.index_sets}
    variables = {item.id: item for item in core.variables}
    literal_values: dict[tuple[str, str], set[Any]] = {}

    for terms in term_groups:
        for term in terms:
            if term.for_each:
                continue
            variable = variables.get(term.variable.variable_id)
            if variable is None:
                continue
            for index_set_id, selector in zip(
                variable.index_sets,
                term.variable.selectors,
            ):
                index_set = index_sets.get(index_set_id)
                if (
                    index_set is not None
                    and len(index_set.values) > 8
                    and selector.value is not None
                ):
                    literal_values.setdefault(
                        (variable.id, index_set_id),
                        set(),
                    ).add(selector.value)

    expanded = {
        key: values
        for key, values in literal_values.items()
        if len(values) > 4
    }
    if expanded:
        details = ", ".join(
            f"{variable_id}.{index_set_id}={len(values)} literal values"
            for (variable_id, index_set_id), values in sorted(expanded.items())
        )
        raise ValueError(
            "large indexed families must use for_each and binder; " + details
        )


def _validate_model_ir_slice(
    model_ir: ModelIR,
    *,
    expected_id: str,
    problem_spec: ProblemSpec,
    evidence_profile: dict,
    available_operator_ids: list[str],
) -> None:
    identifiers = [item.subproblem_id for item in model_ir.subproblems]
    if identifiers != [expected_id]:
        raise ValueError(
            f"subproblem slice mismatch: expected={[expected_id]!r}; modeled={identifiers!r}"
        )
    validate_model_evidence_consistency(problem_spec, model_ir, evidence_profile)
    validate_model_fact_coverage(problem_spec, model_ir)
    if available_operator_ids:
        supported = set(available_operator_ids)
        unsupported = sorted({
            operator_id
            for subproblem in model_ir.subproblems
            if isinstance(subproblem, SolvableModelIR)
            for component in subproblem.execution_components
            for operator_id in (
                [operator.operator_id for operator in component.operators]
                if isinstance(component, OperatorGraphExecutionComponent)
                else ["optimization.milp"]
                if isinstance(component, IndexedLinearExecutionComponent)
                else []
            )
            if operator_id not in supported
        })
        if unsupported:
            raise ValueError(f"operator_id is not available: {unsupported!r}")
    _validate_model_ir_semantics(model_ir)


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


def _task_artifact_dir(task: dict) -> Path | None:
    work_dir = task.get("work_dir")
    return Path(work_dir) if work_dir else None


def _compact_fact_input_catalog(input_catalog: dict) -> dict:
    return {
        **input_catalog,
        "blocks": [
            _compact_large_table(item)
            for item in input_catalog.get("blocks", [])
        ],
    }


def _project_registered_data_sources(
    structure: ProblemStructureGenerationDraft,
    evidence_profile: dict,
) -> ProblemStructureGenerationDraft:
    registered_source_refs = {
        str(item.get("source_ref") or "")
        for item in evidence_profile.get("evidence", [])
        if isinstance(item, dict) and item.get("source_ref")
    }
    return structure.model_copy(update={
        "data_sources": [
            item
            for item in structure.data_sources
            if item.source_ref in registered_source_refs
        ],
    })


def _compact_fact_evidence_profile(evidence_profile: dict) -> dict:
    return {
        **evidence_profile,
        "evidence": [
            _compact_large_table(item)
            for item in evidence_profile.get("evidence", [])
        ],
    }


def _compact_large_table(item: dict) -> dict:
    rows = item.get("rows")
    if not isinstance(rows, list):
        return dict(item)
    column_count = max(
        (
            len(row)
            for row in rows
            if isinstance(row, list)
        ),
        default=0,
    )
    cell_count = sum(
        len(row)
        for row in rows
        if isinstance(row, list)
    )
    if cell_count <= _FACT_EXTRACTION_TABLE_CELL_LIMIT:
        return dict(item)
    profile = item.get("profile")
    return {
        **item,
        "rows": [],
        "profile": {
            **(profile if isinstance(profile, dict) else {}),
            "row_count": len(rows),
            "column_count": column_count,
        },
        "content_omitted": True,
    }


def _compact_problem_spec_for_modeling(problem_spec: ProblemSpec) -> dict[str, Any]:
    payload = problem_spec.model_dump(mode="json")
    locked_facts = payload.get("locked_facts", [])
    uncertainties = payload.get("uncertainties", [])
    return {
        "schema_version": payload.get("schema_version", "1.0"),
        "task_summary": _compact_task_summary(str(payload.get("task_summary") or "")),
        "subproblems": [
            _compact_subproblem(item)
            for item in payload.get("subproblems", [])
            if isinstance(item, dict)
        ],
        "data_sources": [
            _compact_mapping(
                item,
                ("id", "description", "source_ref"),
            )
            for item in payload.get("data_sources", [])
            if isinstance(item, dict)
        ],
        "source_requirements": payload.get("source_requirements", []),
        "locked_facts": _compact_list(locked_facts, limit=len(locked_facts)),
        "uncertainties": _compact_list(uncertainties, limit=len(uncertainties)),
    }


def _compact_previous_model_summary(previous: Any) -> dict[str, Any]:
    payload = previous.model_dump(mode="json")
    components = (
        previous.execution_components
        if isinstance(previous, SolvableModelIR)
        else []
    )
    execution_components = [
        "operator_graph"
        if isinstance(component, OperatorGraphExecutionComponent)
        else "indexed_linear_model"
        for component in components
    ]
    graph_components = [
        component
        for component in components
        if isinstance(component, OperatorGraphExecutionComponent)
    ]
    return {
        "execution_status": payload.get("execution_status"),
        "execution_components": execution_components,
        "subproblem_id": payload.get("subproblem_id"),
        "objective": payload.get("objective"),
        "variable_ids": [
            variable.id
            for component in graph_components
            for variable in component.variables
        ],
        "data_source_refs": [
            item.get("source_ref")
            for item in payload.get("data", [])
            if isinstance(item, dict) and item.get("source_ref")
        ],
        "operator_ids": [
            operator.operator_id
            for component in graph_components
            for operator in component.operators
        ],
        "assumptions": payload.get("assumptions", []),
        "selected_model_id": payload.get("selected_model_id"),
        "baseline_model_id": payload.get("baseline_model_id"),
        "blocking_reason": payload.get("blocking_reason"),
    }


def _validate_model_ir_contract(
    problem_spec: ProblemSpec,
    model_ir: ModelIR,
    evidence_profile: dict,
) -> None:
    expected = [item.id for item in problem_spec.subproblems]
    modeled = [item.subproblem_id for item in model_ir.subproblems]
    if modeled != expected:
        raise ValueError(
            f"subproblem coverage mismatch: expected={expected!r}; modeled={modeled!r}"
        )
    validate_model_evidence_consistency(problem_spec, model_ir, evidence_profile)
    validate_model_fact_coverage(problem_spec, model_ir)
    _validate_model_ir_semantics(model_ir)


def _validate_model_ir_semantics(model_ir: ModelIR) -> None:
    diagnostics = ModelIRSemanticValidator().validate(model_ir)
    if diagnostics:
        details = "; ".join(
            f"{item.code} at {item.path}: {item.message}" for item in diagnostics
        )
        raise ValueError(f"Model IR semantic validation failed: {details}")


def _compact_subproblem(item: dict[str, Any]) -> dict[str, Any]:
    compact = _compact_mapping(
        item,
        (
            "id",
            "subproblem_id",
            "objective",
            "description",
            "goals",
            "inputs",
            "outputs",
            "constraints",
            "assumptions",
            "candidate_directions",
            "problem_type",
        ),
    )
    for key in (
        "required_fact_ids",
        "required_uncertainty_ids",
        "depends_on_subproblem_ids",
    ):
        if key in item:
            compact[key] = list(item[key])
    return compact


def _compact_mapping(item: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key in keys:
        if key not in item:
            continue
        value = item[key]
        if isinstance(value, str):
            output[key] = _truncate_text(value, 240)
        elif isinstance(value, list):
            output[key] = _compact_list(value, limit=8)
        elif isinstance(value, dict):
            output[key] = {
                str(child_key): _truncate_text(str(child_value), 160)
                for child_key, child_value in list(value.items())[:8]
            }
        else:
            output[key] = value
    return output


def _compact_list(value: Any, *, limit: int) -> list[Any]:
    if not isinstance(value, list):
        return []
    output: list[Any] = []
    for item in value[:limit]:
        if isinstance(item, str):
            output.append(_truncate_text(item, 180))
        elif isinstance(item, dict):
            output.append(_compact_mapping(item, tuple(str(key) for key in item.keys())))
        else:
            output.append(item)
    return output


def _compact_task_summary(text: str) -> str:
    clean = " ".join(text.split())
    return _truncate_text(clean, 12_000)


def _truncate_text(text: str, limit: int) -> str:
    clean = " ".join(text.split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 20].rstrip() + "...[truncated]"
