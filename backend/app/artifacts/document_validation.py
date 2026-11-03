"""document validation responsibilities."""

from __future__ import annotations

import re
from typing import Any
from app.utils.figure_artifacts import normalize_artifact_path


_TOOL_PROTOCOL_RE = re.compile(
    r"(<\s*[｜|]+\s*DSML\s*[｜|]+|</\s*[｜|]+\s*DSML\s*[｜|]+|"
    r"\btool_calls\b|invoke\s+name\s*=|parameter\s+name\s*=|run_script)",
    re.IGNORECASE,
)


_COVERAGE_DISCLOSURE_TERMS = (
    "partial coverage",
    "partial result",
    "blocked",
    "interrupted",
    "incomplete",
    "limitation",
    "limited",
    "provisional",
    "salvaged",
    "pending review",
    "部分覆盖",
    "部分结果",
    "被阻断",
    "阻断",
    "中断",
    "未完成",
    "未能",
    "无法",
    "受限",
    "局限",
    "保全",
    "待复核",
    "需复核",
    "临时结果",
    "降级",
)


_RESULT_OVERVIEW_FORBIDDEN_TERMS = (
    "fitting_curve",
    "residual_plot",
    "comparison_bar",
    "mechanism",
    "workflow",
    "fitted curve",
    "residual plot",
    "model shape",
    "curve shape",
    "function shape",
    "拟合曲线",
    "拟合函数",
    "函数曲线",
    "函数整体形态",
    "整体形态",
    "残差图",
    "对比柱状图",
    "机理图",
    "流程图",
    "平直线",
    "正弦波动",
    "正弦周期",
    "波峰",
    "波谷",
)


def _contains_tool_protocol_text(content: str) -> bool:
    if not content:
        return False
    matches = _TOOL_PROTOCOL_RE.findall(content)
    if any("DSML" in str(match).upper() for match in matches):
        return True
    lowered = content.lower()
    return (
        "tool_calls" in lowered
        and ("invoke name" in lowered or "parameter name" in lowered or "run_script" in lowered)
    )


class FinalizationValidationError(ValueError):
    """Machine-readable finalization validation failure."""

    def __init__(self, message: str, error_code: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.error_code = error_code
        self.error_type = "finalizer_validation"
        self.details = details or {}


def _writer_context_from_payload(result_payload: dict[str, Any] | None) -> dict[str, Any]:
    """Return the final writer context embedded in a result payload."""
    if not isinstance(result_payload, dict):
        return {}

    direct_context = result_payload.get("writer_context")
    if isinstance(direct_context, dict):
        return direct_context

    stages = result_payload.get("stages")
    if isinstance(stages, dict):
        stage_context = stages.get("writer_context")
        if isinstance(stage_context, dict):
            return stage_context

    return {}


def _validate_coverage_disclosure(markdown: str, result_payload: dict[str, Any] | None) -> None:
    """Require partial-coverage papers to disclose that status in the final text."""
    writer_context = _writer_context_from_payload(result_payload)
    diagnostics = writer_context.get("coverage_diagnostics")
    if not isinstance(diagnostics, dict):
        return
    if not diagnostics.get("must_disclose_partial"):
        return

    normalized_text = (markdown or "").lower()
    if any(term.lower() in normalized_text for term in _COVERAGE_DISCLOSURE_TERMS):
        return

    details = {
        "coverage_status": diagnostics.get("coverage_status"),
        "blocked_count": diagnostics.get("blocked_count"),
        "verified_count": diagnostics.get("verified_count"),
        "provisional_verified_count": diagnostics.get("provisional_verified_count"),
    }
    raise FinalizationValidationError(
        "Final paper must disclose partial coverage, blocked results, or provisional results.",
        error_code="FINALIZER_MISSING_COVERAGE_DISCLOSURE",
        details=details,
    )


def _validate_result_overview_semantics(markdown: str, result_payload: dict[str, Any] | None) -> None:
    """Reject text that relabels a numeric result overview as another figure type."""
    writer_context = _writer_context_from_payload(result_payload)
    available = writer_context.get("available_figures")
    if not isinstance(available, list):
        return

    overview_paths = {
        normalize_artifact_path(item.get("path"))
        for item in available
        if isinstance(item, dict)
        and str(item.get("semantic_role") or item.get("figure_role") or "").strip().lower()
        in {"result_overview", "data_overview"}
        and str(item.get("path") or "").strip()
    }
    if not overview_paths:
        return

    content = markdown or ""
    for match in re.finditer(r"!\[[^\]]*\]\(([^)]+)\)", content):
        raw_target = match.group(1).strip().strip("<>")
        target = normalize_artifact_path(raw_target.split(" ", 1)[0].strip("'\""))
        if target not in overview_paths:
            continue

        start = max(0, match.start() - 500)
        end = min(len(content), match.end() + 900)
        context = content[start:end].lower()
        matched_terms = [
            term for term in _RESULT_OVERVIEW_FORBIDDEN_TERMS
            if term.lower() in context
        ]
        if matched_terms:
            raise FinalizationValidationError(
                "Result overview figures may only be described as labeled numeric summaries.",
                error_code="FINALIZER_RESULT_OVERVIEW_RELABEL",
                details={"path": target, "matched_terms": matched_terms[:5]},
            )
