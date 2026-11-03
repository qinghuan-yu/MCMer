"""solver planning responsibilities."""

from __future__ import annotations

import json
import re
from app.core.workflow_budget import CoderStageBudget, WorkflowBudget, normalize_workflow_mode
from app.core.workflow_runtime import _preview


def _extract_json_object(text: str) -> dict[str, object] | None:
    raw = str(text or "").strip()
    if not raw:
        return None

    candidates = [raw]
    fence_match = re.search(r"```json\s*(\{.*?\})\s*```", raw, re.S)
    if fence_match:
        candidates.insert(0, fence_match.group(1))

    brace_start = raw.find("{")
    brace_end = raw.rfind("}")
    if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
        candidates.append(raw[brace_start:brace_end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _fallback_solve_spec(
    question: str,
    breakdown: str,
    modeling: str,
    review: str,
    problem_facts: dict[str, object],
) -> dict[str, object]:
    return {
        "subproblems": [
            {
                "id": "main",
                "objective": "基于题目拆解、建模方案和题设事实完成可复现求解",
                "input_files": [],
                "input_columns": [],
                "method": "derive_from_modeling_plan",
                "steps": [
                    "读取 data 目录与题设事实",
                    "按建模方案选择可执行算法",
                    "输出可复核关键结果到结构化结果文件",
                ],
                "expected_outputs": [
                    {
                        "id": "main_key_results",
                        "type": "key_results",
                    }
                ],
                "validation": [
                    "关键结果必须写入结构化结果文件",
                    "题设参数不得被擅自修改",
                ],
            }
        ],
        "source": "fallback",
        "question_excerpt": _preview(question, 500),
        "breakdown_excerpt": _preview(breakdown, 1200),
        "modeling_excerpt": _preview(modeling, 1200),
        "review_excerpt": _preview(review, 1000),
        "locked_parameters": problem_facts.get("locked_parameters", {}),
    }


def _solve_spec_subproblems(solve_spec: dict[str, object]) -> list[dict[str, object]]:
    raw_subproblems = solve_spec.get("subproblems", []) if isinstance(solve_spec, dict) else []
    if not isinstance(raw_subproblems, list):
        return []
    subproblems: list[dict[str, object]] = []
    for index, item in enumerate(raw_subproblems, start=1):
        if isinstance(item, dict):
            normalized = dict(item)
        else:
            normalized = {"objective": str(item)}
        normalized.setdefault("id", f"p{index}")
        subproblems.append(normalized)
    return subproblems


def _subproblem_label(index: int, subproblem: dict[str, object]) -> str:
    raw_label = str(
        subproblem.get("id")
        or subproblem.get("title")
        or subproblem.get("name")
        or f"p{index}"
    ).strip()
    label = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", raw_label).strip("_")
    return label or f"p{index}"


def _should_split_solver(workflow_mode: str, solve_spec: dict[str, object]) -> bool:
    return normalize_workflow_mode(workflow_mode) != "fast" and len(_solve_spec_subproblems(solve_spec)) > 1


def _subproblem_complexity_score(subproblem: dict[str, object]) -> int:
    try:
        text = json.dumps(subproblem, ensure_ascii=False).lower()
    except Exception:
        text = str(subproblem).lower()

    score = 1
    hint = str(subproblem.get("complexity_hint") or subproblem.get("complexity") or "").strip().lower()
    priority = str(subproblem.get("priority") or "").strip().lower()
    if hint in {"simple", "low", "低", "简单"}:
        score = 1
    elif hint in {"medium", "中", "中等", "标准"}:
        score = 3
    elif hint in {"complex", "high", "高", "复杂"}:
        score = 6
    if priority == "high" or priority == "高":
        score += 1
    input_files = subproblem.get("input_files", [])
    steps = subproblem.get("steps", [])
    expected_outputs = subproblem.get("expected_outputs", [])
    if isinstance(input_files, list):
        score += min(2, len(input_files) // 2)
    if isinstance(steps, list):
        score += min(2, len(steps) // 4)
    if isinstance(expected_outputs, list):
        score += min(1, len(expected_outputs) // 3)

    complex_markers = [
        "machine learning",
        "random forest",
        "svr",
        "xgboost",
        "lstm",
        "neural",
        "cross validation",
        "交叉验证",
        "网格搜索",
        "优化",
        "非线性",
        "mice",
        "插补",
        "异常检测",
        "预测",
        "预警",
        "变量组合",
        "敏感性",
        "monte carlo",
    ]
    medium_markers = ["回归", "拟合", "分类", "聚类", "变点", "滤波", "平滑", "参数估计", "误差分析"]
    score += sum(1 for marker in complex_markers if marker in text)
    score += min(2, sum(1 for marker in medium_markers if marker in text))
    return max(1, min(score, 8))


def _subproblem_budget_level(score: int) -> str:
    if score >= 6:
        return "complex"
    if score >= 3:
        return "medium"
    return "simple"


def _solver_budget_for_subproblem(budget: WorkflowBudget, subproblem: dict[str, object] | None = None) -> CoderStageBudget:
    score = _subproblem_complexity_score(subproblem or {})
    if budget.mode == "strict":
        tool_calls = 18 if score <= 2 else 26 if score <= 5 else min(36, budget.solver.max_total_tool_calls)
        wall_seconds = 360 if score <= 2 else 540 if score <= 5 else budget.solver.max_wall_seconds
        return CoderStageBudget(
            max_chat_turns=budget.solver.max_chat_turns,
            max_retries=budget.solver.max_retries,
            max_total_tool_calls=min(max(tool_calls, budget.solver.max_total_tool_calls // 2), budget.solver.max_total_tool_calls),
            max_wall_seconds=min(max(wall_seconds, budget.solver.max_wall_seconds // 2), budget.solver.max_wall_seconds),
        )
    tool_calls = 16 if score <= 2 else 22 if score <= 5 else min(32, max(28, budget.solver.max_total_tool_calls + 4))
    wall_seconds = 360 if score <= 2 else 540 if score <= 5 else min(900, max(720, budget.solver.max_wall_seconds))
    return CoderStageBudget(
        max_chat_turns=budget.solver.max_chat_turns,
        max_retries=budget.solver.max_retries,
        max_total_tool_calls=tool_calls,
        max_wall_seconds=wall_seconds,
    )
