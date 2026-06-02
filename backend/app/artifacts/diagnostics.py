"""Diagnostics — structured gate reports for observability.

Generates machine-readable reports for figure gate, artifact gate,
DOCX export, budget usage, and agent actions.
"""

from __future__ import annotations

import json
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from app.utils.common_utils import save_json


_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


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


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _norm_path(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip()


def _read_text_if_exists(path: str | Path) -> str:
    if not path:
        return ""
    target = Path(path)
    if not target.exists():
        return ""
    return target.read_text(encoding="utf-8", errors="ignore")


def _docx_media_entries(path: str | Path) -> list[str]:
    if not path:
        return []
    target = Path(path)
    if not target.exists():
        return []
    try:
        with zipfile.ZipFile(target) as archive:
            return [
                name
                for name in archive.namelist()
                if name.startswith("word/media/")
            ]
    except zipfile.BadZipFile:
        return []


def _markdown_image_refs(markdown: str) -> list[str]:
    refs: list[str] = []
    for match in _MARKDOWN_IMAGE_RE.findall(markdown or ""):
        ref = _norm_path(match.split("#", 1)[0].split("?", 1)[0])
        if ref:
            refs.append(ref)
    return refs


def _result_counts(result_payload: dict[str, Any], result_registry: dict[str, Any] | None) -> dict[str, int]:
    registry = result_registry if isinstance(result_registry, dict) else {}
    summary = registry.get("summary") if isinstance(registry.get("summary"), dict) else {}
    payload_coverage = result_payload.get("result_coverage")
    if isinstance(payload_coverage, dict):
        summary = {**summary, **payload_coverage}
    verified = summary.get("verified_count")
    blocked = summary.get("blocked_count")
    if verified is None:
        verified = len(_as_list(registry.get("verified_results")))
    if blocked is None:
        blocked = len(_as_list(registry.get("blocked_results")))
    return {
        "verified_count": int(verified or 0),
        "blocked_count": int(blocked or 0),
    }


def build_verified_output_bundle(
    *,
    status: str,
    work_dir: str | Path,
    task_id: str = "",
    task_type: str = "",
    paper_path: str = "",
    docx_path: str = "",
    notebook_path: str = "",
    result_payload: dict[str, Any] | None = None,
    result_registry: dict[str, Any] | None = None,
    allowed_images: list[str] | None = None,
    required_images: list[str] | None = None,
    gate_reason: str = "",
) -> dict[str, Any]:
    """Build the final machine-readable bundle used for real-task triage."""

    payload = result_payload if isinstance(result_payload, dict) else {}
    stages = payload.get("stages") if isinstance(payload.get("stages"), dict) else {}
    if isinstance(result_registry, dict):
        registry = result_registry
    else:
        registry = stages.get("result_registry") if isinstance(stages.get("result_registry"), dict) else {}
    counts = _result_counts(payload, registry)

    figure_bundle = stages.get("figure_bundle") if isinstance(stages.get("figure_bundle"), dict) else {}
    answer_plan = stages.get("answer_plan") if isinstance(stages.get("answer_plan"), dict) else {}
    if not answer_plan:
        answer_plan = stages.get("answer_plan_diagnostics") if isinstance(stages.get("answer_plan_diagnostics"), dict) else {}
    figure_diagnostics = (
        stages.get("figure_plan_diagnostics")
        if isinstance(stages.get("figure_plan_diagnostics"), dict)
        else {}
    )
    diagnostic_summary = (
        figure_diagnostics.get("diagnostic_summary")
        if isinstance(figure_diagnostics.get("diagnostic_summary"), dict)
        else summarize_visualization_issues([
            *(_as_list(figure_bundle.get("blocked_requests"))),
            *(_as_list(figure_bundle.get("missing_requests"))),
        ])
    )

    markdown = _read_text_if_exists(paper_path)
    markdown_refs = _markdown_image_refs(markdown)
    normalized_refs = {_norm_path(ref) for ref in markdown_refs}
    required_norm = [_norm_path(item) for item in (required_images or []) if _norm_path(item)]
    allowed_norm = [_norm_path(item) for item in (allowed_images or []) if _norm_path(item)]
    missing_required = [
        item
        for item in required_norm
        if item not in normalized_refs
        and Path(item).name not in {Path(ref).name for ref in normalized_refs}
    ]
    docx_media = _docx_media_entries(docx_path)
    output_status = str(status or "").strip().lower() or "unknown"

    document_passed = bool(Path(paper_path).exists()) and (
        not docx_path or bool(Path(docx_path).exists())
    ) and not missing_required
    quality_passed = output_status == "passed" and counts["verified_count"] > 0
    answer_status = str(answer_plan.get("coverage_status") or "not_required")
    answer_passed = answer_status in {"ready", "not_required"}
    ready_for_actual_test = bool(quality_passed and document_passed and answer_passed)

    return {
        "status": output_status,
        "ready_for_actual_test": ready_for_actual_test,
        "task": {
            "task_id": task_id or str(payload.get("task_id") or ""),
            "task_type": task_type or str(payload.get("task_type") or ""),
        },
        "outputs": {
            "work_dir": str(work_dir),
            "paper_path": paper_path,
            "paper_exists": bool(paper_path and Path(paper_path).exists()),
            "docx_path": docx_path,
            "docx_exists": bool(docx_path and Path(docx_path).exists()),
            "notebook_path": notebook_path,
            "notebook_exists": bool(notebook_path and Path(notebook_path).exists()),
        },
        "result_gate": counts,
        "answer_gate": {
            "coverage_status": answer_status,
            "slot_count": int(answer_plan.get("slot_count", 0) or 0),
            "covered_count": int(answer_plan.get("covered_count", 0) or 0),
            "blocked_count": int(answer_plan.get("blocked_count", 0) or 0),
            "missing_count": int(answer_plan.get("missing_count", 0) or 0),
            "passed": answer_passed,
            "slots": _as_list(answer_plan.get("slots")),
        },
        "visualization_gate": {
            "expected_figures": bool(figure_diagnostics.get("effective_expected_figures", False)),
            "available_figure_count": len(_as_list(figure_bundle.get("available_figures"))),
            "required_image_count": len(required_norm),
            "allowed_image_count": len(allowed_norm),
            "blocked_request_count": int(figure_diagnostics.get("blocked_request_count", 0) or 0),
            "missing_request_count": len(_as_list(figure_bundle.get("missing_requests"))),
            "diagnostic_summary": diagnostic_summary,
        },
        "document_gate": {
            "passed": document_passed,
            "markdown_image_refs": markdown_refs,
            "missing_required_images": missing_required,
            "docx_media_count": len(docx_media),
            "docx_media_entries": docx_media,
        },
        "failure": {
            "gate_reason": gate_reason,
        },
    }


def save_verified_output_bundle(
    work_dir: str | Path,
    bundle: dict[str, Any],
) -> Path:
    path = Path(work_dir) / "verified_output_bundle.json"
    save_json(bundle, str(path))
    return path


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
        answer_completeness: dict[str, Any] | None = None,
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
                "answer_completeness": answer_completeness or {},
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
