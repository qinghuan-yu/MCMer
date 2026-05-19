"""Diagnostics — structured gate reports for observability.

Generates machine-readable reports for figure gate, artifact gate,
DOCX export, budget usage, and agent actions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from app.utils.common_utils import save_json


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
    ) -> QualityGateReport:
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
