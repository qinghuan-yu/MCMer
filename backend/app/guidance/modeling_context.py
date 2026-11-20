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


MODEL_IR_ARCHITECTURE_CONTRACT = """你在数学工作台上构建一个可审核的 ModelIR。每一步可调用数学工具，根据返回的实际观测修正草稿。
先识别题目要求交付的量、决策变量、单位、定义域、目标、约束和数据来源。只处理 target_subproblem_id。
edit_model 的集合按 id 更新。先建变量和来源，再写引用它们的目标与约束；可批量提交彼此一致的编辑。
结构性事实用 variables、objective、constraints 或图节点的 source_fact_ids 登记。例如整数性属于变量 domain，关系符属于约束 relation。
parameters 只放实际会参与数值计算的数据；标量绑定为 {"id":"参数名","source_kind":"static","binding":{"kind":"fact","fact_id":"事实ID"}}。
表格按已登记来源读取。工作台返回的数值已做单位换算，百分比不得再次缩放。没有索引的标量问题可省略 index_sets。
变量示例 {"id":"v","domain":"integer","lower_bound":0,"source_fact_ids":["变量域的事实ID"]}；线性项含 variable 和 coefficient，
例如 {"variable":{"variable_id":"v"},"coefficient":{"kind":"parameter","parameter_id":"参数名"}}。重复约束可用 for_each；非零 offset 必须绑定到排除越界端点的 auxiliary index set。
不确定字段先 inspect_model(schema=字段名)，需要已验证输入值时 inspect_model(parameter_id=参数名)。工具错误会给出字段和原因，可逐项修订。
architecture 包含 model_id 和一个 subproblems 条目。可解条目声明 execution_components、data、assumptions、candidate_models、validation、final_answers。
有限索引优化使用 indexed_linear_model，系统生成 optimization.milp；operator_graph 才声明 variables 和已注册的 operators。
架构中的 variables 是图工件；决策变量在 edit_model 的顶层 variables 登记。混合模型可将图输出作为索引参数的 runtime_source。
为基线和推荐路线注明 selection_metric 与 applicability_boundary。纯索引候选的 operator_node_ids 留空。最终答案引用实际输出和证据节点。
引用索引求解结果时，从 indexed_solver_answer 读取输出名和证据节点名。图计算结果引用其生产节点。提交反馈会逐项列出无效引用和可用名称。
只使用 available_operators 中的算子，inspect_operators 可查询合同。不要写 free-text equations、Python、solve.py 或预猜 fact_bindings 路径。
已知事实与额外假设分别记录；假设需要替代情景和敏感性检查。题意歧义引用 problem_spec.json#uncertainties/<uncertainty_id>。
若确实缺少输入、关系或可执行能力，提交 blocked 架构并登记 blocking_reason、missing_inputs、recovery_action、expected_outputs。
完成时调用 submit_model。提交只进入独立审核；工具观测和校验记录决定能否继续执行。"""

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
    decision_payload = decision.model_dump(mode="json")
    # 题意已在建模前裁决；审核合同只包含真实数学修复义务。
    payload["subproblem_reviews"] = [decision_payload]
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
