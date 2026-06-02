"""Diagnostics — structured gate reports for observability.

Generates machine-readable reports for figure gate, artifact gate,
DOCX export, budget usage, and agent actions.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from app.utils.common_utils import save_json


def classify_visualization_issue(reason: str) -> str:
    """Map visualization failure reasons into stable diagnostic buckets."""

    value = str(reason or "").strip()
    if not value:
        return "other"
    normalized = value.lower()

    if normalized in {
        "missing_visualization_contract",
        "missing_visualization_source_data",
        "missing_supported_visualization_view",
    }:
        return "visualization_contract"
    if normalized == "missing_source_data" or normalized.startswith((
        "required_columns_missing:",
        "insufficient_numeric_columns:",
    )):
        return "source_data"
    if normalized.startswith(("unsupported_renderer:", "unsupported_chart_language:")):
        return "renderer"
    if (
        normalized.startswith("semantic_data_mismatch:")
        or "finalizer_result_overview_relabel" in normalized
        or "caption" in normalized
        or "relabel" in normalized
    ):
        return "semantic"
    if normalized in {
        "placeholder_linked_result_ids",
        "missing_verified_result_binding",
        "pending_result_binding",
        "linked_result_not_verified",
    }:
        return "result_binding"
    if (
        normalized.startswith("finalizer_missing_image_ref")
        or normalized.startswith("finalizer_unwhitelisted_image_ref")
        or normalized.startswith("finalizer_required_image_not_referenced")
        or "docx" in normalized
    ):
        return "document_export"
    return "other"


def _issue_reason(item: dict[str, Any]) -> str:
    for key in (
        "protocol_blocked_reason",
        "blocked_reason",
        "reason",
        "error_code",
        "code",
    ):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    rejected_by = item.get("rejected_by")
    if isinstance(rejected_by, list) and rejected_by:
        return str(rejected_by[0] or "").strip()
    return "unspecified"


def summarize_visualization_issues(items: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Return categorized diagnostics for blocked figure workflow items."""

    reasons: list[str] = []
    for item in items or []:
        if isinstance(item, dict):
            reasons.append(_issue_reason(item))
    reason_counts = Counter(reasons)
    category_counts = Counter(
        classify_visualization_issue(reason)
        for reason in reasons
    )
    top_reasons = [
        {
            "reason": reason,
            "count": count,
            "category": classify_visualization_issue(reason),
        }
        for reason, count in reason_counts.most_common(8)
    ]
    return {
        "count": len(reasons),
        "by_category": dict(sorted(category_counts.items())),
        "top_reasons": top_reasons,
    }


@dataclass
class GateReport:
    """Structured report for a gate check (figure, artifact, etc.)."""

    passed: bool
    gate_name: str = "generic"  # e.g. "figure_artifact", "quality", "docx_export"
    reason: str = ""
    items: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, work_dir: str | Path) -> Path:
        filename = f"{self.gate_name}_gate_report.json"
        path = Path(work_dir) / filename
        save_json(self.to_dict(), str(path))
        return path


@dataclass
class FigureGateReport(GateReport):
    """Detailed report for the figure artifact gate."""

    gate_name: str = "figure_artifact"

    @classmethod
    def from_rejections(
        cls,
        paper_ready: list[str],
        rejections: list[dict[str, Any]],
    ) -> FigureGateReport:
        return cls(
            gate_name="figure_artifact",
            passed=len(rejections) == 0,
            reason=f"{len(paper_ready)} figures passed, {len(rejections)} rejected",
            items=rejections,
            metadata={
                "paper_ready_count": len(paper_ready),
                "rejected_count": len(rejections),
                "paper_ready_paths": paper_ready,
            },
        )


@dataclass
class QualityGateReport(GateReport):
    """Report for the pre-writing quality gate."""

    gate_name: str = "quality"

    @classmethod
    def from_check(
        cls,
        verified_count: int,
        blocked_count: int,
        expected_figures: bool,
        paper_ready_count: int,
        passed: bool,
        reason: str,
        required_request_count: int = 0,
        satisfied_request_count: int = 0,
        missing_requests: list[dict[str, Any]] | None = None,
        workflow_issues: list[dict[str, Any]] | None = None,
        blocked_requests: list[dict[str, Any]] | None = None,
        diagnostic_summary: dict[str, Any] | None = None,
    ) -> QualityGateReport:
        issue_items = [
            *(workflow_issues or []),
            *(blocked_requests or []),
            *(missing_requests or []),
        ]
        return cls(
            gate_name="quality",
            passed=passed,
            reason=reason,
            metadata={
                "verified_count": verified_count,
                "blocked_count": blocked_count,
                "expected_figures": expected_figures,
                "paper_ready_count": paper_ready_count,
                "required_request_count": required_request_count,
                "satisfied_request_count": satisfied_request_count,
                "missing_requests": missing_requests or [],
                "workflow_issues": workflow_issues or [],
                "blocked_requests": blocked_requests or [],
                "diagnostic_summary": diagnostic_summary or summarize_visualization_issues(issue_items),
            },
        )


@dataclass
class BudgetReport:
    """Report on tool-call budget usage per agent."""

    entries: list[dict[str, Any]] = field(default_factory=list)

    def add(
        self,
        agent_name: str,
        subtask: str,
        total_calls: int,
        max_calls: int,
        guard_blocked: int = 0,
        reason: str = "",
    ) -> None:
        self.entries.append({
            "agent": agent_name,
            "subtask": subtask,
            "total_calls": total_calls,
            "max_calls": max_calls,
            "usage_pct": round(total_calls / max(max_calls, 1) * 100, 1),
            "guard_blocked": guard_blocked,
            "reason": reason,
        })

    def save(self, work_dir: str | Path) -> Path:
        path = Path(work_dir) / "budget_report.json"
        save_json({"entries": self.entries}, str(path))
        return path


@dataclass
class WorkflowTrace:
    """Lightweight trace of workflow stages for debugging."""

    stages: list[dict[str, Any]] = field(default_factory=list)

    def add_stage(
        self,
        name: str,
        status: str,
        message: str = "",
        duration_s: float = 0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.stages.append({
            "name": name,
            "status": status,
            "message": message,
            "duration_s": round(duration_s, 2),
            **(metadata or {}),
        })

    def save(self, work_dir: str | Path) -> Path:
        path = Path(work_dir) / "workflow_trace.json"
        save_json({"stages": self.stages}, str(path))
        return path
