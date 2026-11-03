"""figure gate responsibilities."""

from __future__ import annotations




def repair_eligible_missing_reason(reason: str) -> bool:
    lowered = str(reason or "").strip().lower()
    if not lowered:
        return True
    blocked_prefixes = (
        "unsupported_deterministic_role",
        "required_data_missing",
        "insufficient_data_series",
        "insufficient_semantic_required_data_contract",
        "missing_fitting_columns",
        "missing_residual_columns",
        "missing_comparison_columns",
        "required_columns_missing",
        "missing_source_data",
        "missing_verified_result",
        "linked_result_not_verified",
        "placeholder_linked_result_ids",
        "pending_result_binding",
        "unsupported_renderer",
    )
    return not any(lowered.startswith(prefix) for prefix in blocked_prefixes)


def repair_eligible_request(target_req: dict[str, object] | None, missing_reason: str) -> bool:
    if not repair_eligible_missing_reason(missing_reason):
        return False
    if not isinstance(target_req, dict):
        return True
    if str(target_req.get("status") or "").strip().lower() == "blocked":
        return False
    if target_req.get("feasible", True) is False:
        return False
    if str(target_req.get("blocked_reason") or "").strip():
        return False
    if str(target_req.get("result_binding_status") or "").strip().lower() == "pending_result_binding":
        return False
    return True


def missing_reason_summary(missing_requests: list[dict[str, object]]) -> dict[str, int]:
    summary = {
        "unsupported_renderer": 0,
        "missing_verified_result": 0,
        "missing_source_data": 0,
        "repair_budget_exceeded": 0,
        "unbound_semantic_artifact": 0,
        "other": 0,
    }
    for item in missing_requests:
        reason = str(item.get("reason") or "").strip().lower()
        if reason.startswith("unsupported_deterministic_role") or reason.startswith("unsupported_renderer"):
            summary["unsupported_renderer"] += 1
        elif (
            "linked_result_not_verified" in reason
            or "placeholder_linked_result_ids" in reason
            or "missing_verified_result" in reason
        ):
            summary["missing_verified_result"] += 1
        elif (
            reason.startswith("required_data_missing")
            or "missing_source_data" in reason
            or "insufficient_data_series" in reason
            or "missing_fitting_columns" in reason
            or "missing_residual_columns" in reason
            or "missing_comparison_columns" in reason
            or "required_columns_missing" in reason
        ):
            summary["missing_source_data"] += 1
        elif "budget" in reason or "tool calls exceeded" in reason:
            summary["repair_budget_exceeded"] += 1
        elif "no semantic_verified artifact" in reason:
            summary["unbound_semantic_artifact"] += 1
        else:
            summary["other"] += 1
    return summary


def quality_gate_before_writing(
    result_registry: dict[str, object],
    expected_figures: bool,
    required_requests: list[dict[str, object]],
    satisfied_requests: list[dict[str, object]],
    missing_requests: list[dict[str, object]],
    workflow_mode: str,
    requires_result_figures: bool = False,
    requires_explanatory_figures: bool = False,
    result_figure_count: int = 0,
    explanatory_figure_count: int = 0,
    requires_figures_by_problem: bool = False,
    blocked_requests: list[dict[str, object]] | None = None,
) -> tuple[bool, str]:
    """Return (passed, reason). If passed is False, reason explains why."""
    summary = result_registry.get("summary", {}) if isinstance(result_registry, dict) else {}
    verified_count = summary.get("verified_count", 0) if isinstance(summary, dict) else 0
    blocked_count = summary.get("blocked_count", 0) if isinstance(summary, dict) else 0
    if verified_count == 0 and isinstance(result_registry, dict):
        verified = result_registry.get("verified_results", [])
        if isinstance(verified, list):
            verified_count = sum(
                1
                for item in verified
                if isinstance(item, dict)
                and str(item.get("id") or "").strip()
                and str(item.get("status") or "verified").strip().lower() != "blocked"
                and item.get("verified", True) is not False
            )

    if verified_count == 0:
        return False, (
            f"verified_count=0（blocked_count={blocked_count}）。"
            "无法输出正式论文，因为没有任何经过验证的数值结果。"
            "请检查求解阶段是否正确产出 key_results 并标记为 verified。"
        )

    if expected_figures and required_requests and missing_requests:
        missing_ids = [str(item.get("figure_request_id") or "").strip() for item in missing_requests]
        missing_ids = [item for item in missing_ids if item]
        reason_summary = missing_reason_summary(missing_requests)
        return False, (
            "任务预期有图表（expected_figures=true），但未满足全部 required figure_requests。"
            f"missing={missing_ids}。"
            f"missing_by_reason={reason_summary}。"
            "请检查图表阶段是否为每个 required request 产出 semantic_verified=true 且绑定 figure_request_id 的图。"
        )

    if expected_figures and not required_requests:
        blocked = blocked_requests if isinstance(blocked_requests, list) else []
        if requires_figures_by_problem and blocked:
            blocked_by_reason: dict[str, int] = {}
            for item in blocked:
                if not isinstance(item, dict):
                    continue
                reason = str(item.get("blocked_reason") or "unspecified").strip() or "unspecified"
                blocked_by_reason[reason] = blocked_by_reason.get(reason, 0) + 1
            return False, (
                "任务按题意需要图表（requires_figures_by_problem=true），但当前没有可行 required figure。"
                f"blocked_required_by_problem_count={len(blocked)}。"
                f"blocked_by_reason={blocked_by_reason}。"
                "请补齐 renderer 或数据绑定后再将相关请求升级为 required。"
            )
        return False, (
            "任务预期有图表（expected_figures=true），但 figure_plan 未提供 figure_requests。"
            "请通过 VisualizationPlannerSkill 从 result_registry 生成 required figure_requests。"
        )

    if expected_figures and required_requests and not satisfied_requests:
        return False, (
            "任务预期有图表（expected_figures=true），但没有任何 required figure_request 被满足。"
            "请确保产物包含 figure_request_id 且 semantic_verified=true。"
        )

    if requires_explanatory_figures and explanatory_figure_count <= 0:
        return False, (
            "任务要求 explanatory figures，但未生成任何说明图。"
            "请生成 geometry/timeline/trajectory/optimization 语义图并满足 figure_request。"
        )

    if requires_result_figures and result_figure_count <= 0:
        return False, (
            "任务要求 result figures，但未生成任何结果图。"
            "请生成绑定 verified_results 的结果图。"
        )

    return True, ""
