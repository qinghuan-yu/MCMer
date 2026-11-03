"""figure requests responsibilities."""

from __future__ import annotations

import json
import re
from typing import Any
from app.artifacts.figure_plan import FigurePlanBuilder
from app.artifacts.registry import ArtifactRegistry


_FIGURE_KEYWORDS = re.compile('(?:图表|作图|绘图|制图|画图|插图|示意图|流程图|架构图|拓扑图|折线图|散点图|柱状图|饼图|箱线图|热力图|等高线图|雷达图|直方图|figure|chart|plot|graph|diagram|visualization|visuali[sz]e|scatter|histogram|heatmap|contour|boxplot|bar\\s*chart|pie\\s*chart|折线|柱状|散点|热力|等高线|箱线|雷达|可视化)', re.IGNORECASE)


def ensure_figure_requests_in_solve_spec(
    solve_spec: dict[str, object],
    chart_language: str,
) -> list[dict[str, object]]:
    existing = solve_spec.get("figure_requests") if isinstance(solve_spec, dict) else None
    if isinstance(existing, list) and any(isinstance(item, dict) for item in existing):
        normalized = [item for item in existing if isinstance(item, dict)]
        solve_spec["figure_requests"] = normalized
        return normalized

    requests: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    subproblems_raw = solve_spec.get("subproblems", []) if isinstance(solve_spec, dict) else []
    subproblems = [item for item in subproblems_raw if isinstance(item, dict)] if isinstance(subproblems_raw, list) else []

    def _default_required_data_for_role(role: str) -> list[str]:
        if role in {"geometry_schematic", "timeline_schematic", "trajectory_overview", "optimization_variable_diagram"}:
            return ["result_registry.json"]
        if role == "trajectory_shielding":
            return [
                "missile_trajectory.json",
                "cloud_center_trajectory.json",
                "shielding_intervals.json",
            ]
        if role == "distance_time_curve":
            return ["distance_time_series.csv"]
        if role == "optimization_convergence":
            return ["optimization_history.csv"]
        if role == "sensitivity_analysis":
            return ["sensitivity_results.csv"]
        return ["key_result_series.csv"]

    def _infer_figure_role(text: str) -> str:
        lowered = text.lower()
        if any(token in lowered for token in ["轨迹", "trajectory"]):
            return "trajectory_shielding"
        if any(token in lowered for token in ["收敛", "convergence", "优化"]):
            return "optimization_convergence"
        if any(token in lowered for token in ["距离", "distance", "时间", "time"]):
            return "distance_time_curve"
        if any(token in lowered for token in ["敏感性", "sensitivity"]):
            return "sensitivity_analysis"
        return "result_visualization"

    def _append_request(
        subproblem_id: str,
        role: str,
        required: bool,
        expected_text: str,
        figure_kind: str,
    ) -> None:
        request_id = f"fig_{subproblem_id}_{role}"
        if request_id in seen_ids:
            return
        seen_ids.add(request_id)
        requests.append(
            {
                "id": request_id,
                "subproblem_id": subproblem_id,
                "required": required,
                "role": role,
                "semantic_role": role,
                "figure_kind": figure_kind,
                "expected_content": [expected_text],
                "required_data": _default_required_data_for_role(role),
                "language": chart_language,
                "chart_language": chart_language,
                "must_include_in_paper": bool(required and figure_kind == "explanatory_figure"),
            }
        )

    geometry_keywords = re.compile(r"导弹|无人机|目标|烟幕|遮蔽|视线|几何|geometry|line\s*of\s*sight", re.IGNORECASE)
    trajectory_keywords = re.compile(r"轨迹|trajectory|path", re.IGNORECASE)
    timeline_keywords = re.compile(r"投放|起爆|有效期|时间区间|时刻|timeline|time\s*window", re.IGNORECASE)
    optimization_keywords = re.compile(r"优化变量|约束|目标函数|优化|optimization|objective|constraint", re.IGNORECASE)

    for index, subproblem in enumerate(subproblems, start=1):
        text = json.dumps(subproblem, ensure_ascii=False)
        if not _FIGURE_KEYWORDS.search(text):
            continue
        subproblem_id = str(subproblem.get("id") or f"problem_{index}").strip() or f"problem_{index}"
        objective = str(subproblem.get("objective") or "").strip() or subproblem_id

        if geometry_keywords.search(text):
            _append_request(subproblem_id, "geometry_schematic", True, f"{objective} 几何关系示意图", "explanatory_figure")
        if trajectory_keywords.search(text):
            _append_request(subproblem_id, "trajectory_overview", True, f"{objective} 轨迹总览示意图", "explanatory_figure")
        if timeline_keywords.search(text):
            _append_request(subproblem_id, "timeline_schematic", True, f"{objective} 时间轴示意图", "explanatory_figure")
        if optimization_keywords.search(text):
            _append_request(subproblem_id, "optimization_variable_diagram", False, f"{objective} 优化变量关系图", "explanatory_figure")

        role = _infer_figure_role(text)
        _append_request(subproblem_id, role, True, objective, "result_figure")

    solve_spec["figure_requests"] = requests
    return requests


def normalize_workflow_issues(figure_plan: dict[str, object] | None) -> list[dict[str, object]]:
    if not isinstance(figure_plan, dict):
        return []
    raw = figure_plan.get("workflow_issues", [])
    if not isinstance(raw, list):
        return []
    issues: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        issues.append(
            {
                "code": str(item.get("code") or "").strip(),
                "severity": str(item.get("severity") or "").strip() or "warning",
                "stage": str(item.get("stage") or "").strip() or "figure_plan",
                "reason": str(item.get("reason") or "").strip() or "unspecified",
                "request_id": str(item.get("request_id") or "").strip(),
                "action": str(item.get("action") or "").strip(),
            }
        )
    return issues


def workflow_issue_summary(issues: list[dict[str, object]]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "count": 0,
        "by_code": {},
        "by_stage": {},
        "top_reasons": {},
    }
    by_code: dict[str, int] = {}
    by_stage: dict[str, int] = {}
    top_reasons: dict[str, int] = {}
    valid_count = 0
    for item in issues:
        if not isinstance(item, dict):
            continue
        valid_count += 1
        code = str(item.get("code") or "UNKNOWN").strip() or "UNKNOWN"
        stage = str(item.get("stage") or "unknown").strip() or "unknown"
        reason = str(item.get("reason") or "unspecified").strip() or "unspecified"
        by_code[code] = by_code.get(code, 0) + 1
        by_stage[stage] = by_stage.get(stage, 0) + 1
        top_reasons[reason] = top_reasons.get(reason, 0) + 1

    summary["count"] = valid_count
    summary["by_code"] = by_code
    summary["by_stage"] = by_stage
    summary["top_reasons"] = dict(
        sorted(top_reasons.items(), key=lambda kv: kv[1], reverse=True)[:5]
    )
    return summary


def reevaluate_solve_spec_requests(
    solve_spec: dict[str, object],
    chart_language: str,
    work_dir: str,
) -> list[dict[str, object]]:
    if not isinstance(solve_spec, dict):
        return []
    current_requests = solve_spec.get("figure_requests", [])
    if not isinstance(current_requests, list) or not current_requests:
        return []

    reevaluated = FigurePlanBuilder.reevaluate_requests(
        requests=[item for item in current_requests if isinstance(item, dict)],
        chart_language=chart_language,
        work_dir=work_dir,
    )
    if not reevaluated:
        return []

    solve_spec["figure_requests"] = reevaluated
    solve_spec["required_feasible_count"] = sum(
        1 for item in reevaluated if isinstance(item, dict) and bool(item.get("required", True))
    )
    solve_spec["blocked_count"] = sum(
        1 for item in reevaluated if isinstance(item, dict) and str(item.get("status") or "") == "blocked"
    )
    return [item for item in reevaluated if isinstance(item, dict)]


def required_figure_requests(solve_spec: dict[str, object]) -> list[dict[str, object]]:
    requests = solve_spec.get("figure_requests", []) if isinstance(solve_spec, dict) else []
    if not isinstance(requests, list):
        return []
    return [
        req
        for req in requests
        if isinstance(req, dict)
        and bool(req.get("required", True))
        and str(req.get("status") or "").strip().lower() != "blocked"
        and req.get("feasible", True) is not False
    ]


def figure_requests_for_subproblem(
    solve_spec: dict[str, object],
    subproblem_id: str,
) -> list[dict[str, object]]:
    requests = solve_spec.get("figure_requests", []) if isinstance(solve_spec, dict) else []
    if not isinstance(requests, list):
        return []
    sid = str(subproblem_id or "").strip()
    if not sid:
        return []
    scoped: list[dict[str, object]] = []
    for item in requests:
        if not isinstance(item, dict):
            continue
        if str(item.get("subproblem_id") or item.get("problem_section") or "").strip() == sid:
            scoped.append(item)
    return scoped


def figure_data_protocol_contract(requests: list[dict[str, object]]) -> str:
    if not requests:
        return ""

    lines: list[str] = []
    lines.append("## Figure data protocol (deterministic renderers)")
    lines.append("当 figure_request 包含以下语义角色时，Solver 必须先产出结构化数据文件，再产图：")

    def _req_data_files(req: dict[str, object]) -> list[str]:
        required_data = req.get("required_data", [])
        from_required = [str(item).strip() for item in required_data if str(item).strip()] if isinstance(required_data, list) else []
        depends_on = req.get("depends_on", {}) if isinstance(req.get("depends_on"), dict) else {}
        dep_files = depends_on.get("data_files", []) if isinstance(depends_on.get("data_files"), list) else []
        from_depends = [str(item).strip() for item in dep_files if str(item).strip()]
        merged = [*from_required, *from_depends]
        merged = [item for item in merged if item and item != "result_registry.json"]
        return list(dict.fromkeys(merged))

    for req in requests:
        if not isinstance(req, dict):
            continue
        role = str(req.get("semantic_role") or req.get("role") or "").strip().lower()
        if role not in {"fitting_curve", "residual_plot", "comparison_bar"}:
            continue
        req_id = str(req.get("id") or "").strip() or "unknown_request"
        linked_ids = req.get("linked_result_ids", [])
        linked = [str(item).strip() for item in linked_ids if str(item).strip()] if isinstance(linked_ids, list) else []
        data_files = _req_data_files(req)
        data_file_hint = data_files[0] if data_files else "data/<subproblem>_data.csv"

        if role == "fitting_curve":
            lines.append(
                f"- {req_id} fitting_curve: 生成 CSV `{data_file_hint}`，至少包含列 x, observed, fitted；"
                f"并把对应结果写入 key_results（建议 id: {linked[0] if linked else 'q?_fitting_result'}）。"
            )
        elif role == "residual_plot":
            lines.append(
                f"- {req_id} residual_plot: 生成 CSV `{data_file_hint}`，至少包含列 x, residual；"
                f"并把对应结果写入 key_results（建议 id: {linked[0] if linked else 'q?_residual_result'}）。"
            )
        else:
            lines.append(
                f"- {req_id} comparison_bar: 生成 CSV `{data_file_hint}`，优先列 method, metric, value（可兼容 category, value）；"
                f"并把对应结果写入 key_results（建议 id: {linked[0] if linked else 'q?_comparison_result'}）。"
            )

    if len(lines) <= 2:
        return ""
    lines.append("未按列协议产出数据文件时，对应图将被降级为 blocked，不进入 required。")
    return "\n".join(lines) + "\n"


def apply_figure_plan_to_solve_spec(
    solve_spec: dict[str, object],
    figure_plan: dict[str, object],
) -> None:
    """Synchronize only minimal compatibility flags from figure_plan.

        ``figure_plan.json`` owns request counts, blocked diagnostics, and the
        effective expected-figures decision.  Keeping those fields out of
        ``solve_spec.json`` prevents the solver contract from becoming another
        global visualization state container.
        """
    if not isinstance(solve_spec, dict) or not isinstance(figure_plan, dict):
        return
    requires_figures_by_problem = bool(
        figure_plan.get("requires_figures_by_problem", figure_plan.get("requires_figures"))
    )
    requires_result_by_problem = bool(
        figure_plan.get("requires_result_figures_by_problem", figure_plan.get("requires_result_figures"))
    )
    requires_explanatory_by_problem = bool(
        figure_plan.get("requires_explanatory_figures_by_problem", figure_plan.get("requires_explanatory_figures"))
    )

    solve_spec["requires_figures"] = requires_figures_by_problem
    solve_spec["requires_result_figures"] = requires_result_by_problem
    solve_spec["requires_explanatory_figures"] = requires_explanatory_by_problem
    solve_spec["requires_figures_by_problem"] = requires_figures_by_problem
    solve_spec["requires_result_figures_by_problem"] = requires_result_by_problem
    solve_spec["requires_explanatory_figures_by_problem"] = requires_explanatory_by_problem
    for plan_owned_key in ("required_feasible_count", "blocked_count", "expected_figures"):
        solve_spec.pop(plan_owned_key, None)


def subproblem_figure_mode(
    subproblem: dict[str, object],
    task_expected_figures: bool,
) -> str:
    if not task_expected_figures:
        return "none"
    text = json.dumps(subproblem, ensure_ascii=False).lower()
    if _FIGURE_KEYWORDS.search(text):
        return "paper_required"
    return "none"


def build_bundle_snapshot(art_registry: ArtifactRegistry) -> dict[str, object]:
    figure_bundle = art_registry.build_figure_bundle()
    writer_images = [
        str(item.get("path") or "").strip()
        for item in figure_bundle.available_figures
        if str(item.get("path") or "").strip()
    ]
    return {
        "figure_bundle": figure_bundle,
        "writer_images": writer_images,
        "figure_bundle_dict": figure_bundle.to_dict(),
    }
