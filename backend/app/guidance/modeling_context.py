"""Guidance 职责边界：modeling_context。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.guidance.contracts import ProblemSpec
from app.guidance.model_ir import (
    OperatorGraphExecutionComponent,
    SolvableModelIR,
)
from app.guidance.model_review_contracts import ModelIRReview


MODEL_IR_ARCHITECTURE_CONTRACT = """只输出符合目标 Schema 的短架构 JSON，不输出方程、索引模型或代码。逐一覆盖 problem_spec 的全部子问题，subproblem_id 必须精确使用输入 id。可解子问题必须声明 execution_components、数据引用、显式 assumptions、推荐模型与简单基线、验证计划和证据绑定的最终答案；架构阶段禁止输出 fact_bindings 或预猜任何 canonical JSON 路径。每个实际使用题面事实的算子节点必须在 source_fact_ids 中登记 locked_facts 的 fact_id；没有使用事实的算子登记空数组，纯 operator_graph 必须由这些节点覆盖全部 required_fact_ids；禁止从 description 猜测未登记的事实 ID，也禁止把 data_sources[].id 当作 fact_id。题面锁定事实不是假设，不得复制到 assumptions；没有额外假设时 assumptions 必须为 []。每条 assumption 的 alternative_scenarios 和 sensitivity_checks 必须非空。题意歧义但可用替代情景检验时，不得误报算子不足；应登记 problem_ambiguity 假设，其 source_refs 必须逐项精确使用 problem_spec.json#uncertainties/<uncertainty_id>，禁止使用数组索引或集合级简写；同时列出受影响模型元素、替代情景和敏感性检查，最终结论只能是条件解。有限索引线性/整数优化必须在 execution_components 中包含 indexed_linear_model，系统会在后续合同中构建 indexed_linear_model 与 optimization.milp，架构阶段禁止自行输出 optimization.milp。不含 operator_graph 时 operators 和 candidate_models.operator_node_ids 必须为空，由系统追加 MILP 节点。若索引模型还依赖预测、估计或数据变换的输出，execution_components 必须同时包含 operator_graph，并用已注册算子显式产出供索引参数绑定的工件；混合执行图中，只使用系统 MILP 的候选保持 operator_node_ids 为空，需要上游工件的候选只引用实际存在的上游节点，系统随后为全部候选追加 MILP 节点。operator_id 必须精确来自 available_operator_ids。禁止 free-text equations、Python、solve.py、自创算子、题名或题号路由。只建模目标子问题明确要求的范围。evidence_profile 中登记的来源视为实际存在；source_requirements 中 status=missing 的来源才是确定缺失。数据、物理关系或算子不足时输出 blocked，区分 missing_inputs 与缺失算子并给出 recovery_action 和 expected_outputs；禁止用经验常数或正则化伪造可解性。"""

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

def _task_artifact_dir(task: dict) -> Path | None:
    work_dir = task.get("work_dir")
    return Path(work_dir) if work_dir else None

def _compact_problem_spec_for_modeling(problem_spec: ProblemSpec) -> dict[str, Any]:
    payload = problem_spec.model_dump(mode="json")
    return {
        **payload,
        "task_summary": _compact_task_summary(str(payload.get("task_summary") or "")),
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

def _compact_task_summary(text: str) -> str:
    clean = " ".join(text.split())
    return _truncate_text(clean, 12_000)

def _truncate_text(text: str, limit: int) -> str:
    clean = " ".join(text.split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 20].rstrip() + "...[truncated]"
