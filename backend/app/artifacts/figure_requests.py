"""FigureRequest — structured chart generation requests.

Instead of letting agents freely draw charts, the workflow generates
FigureRequest objects from the result_registry. Chart agents or deterministic
renderers only process these structured requests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Literal

from app.utils.common_utils import save_json


@dataclass
class FigureRequest:
    """A structured request to generate a single figure.

    Created by the workflow from the result_registry. Processed by a chart
    agent or deterministic renderer. Output must be a FigureArtifact.
    """

    figure_id: str  # e.g. "fig_problem2_duration"
    chart_language: str  # "Simplified Chinese" or "English"
    linked_result_ids: list[str]  # must reference verified results
    source_data: list[str]  # data files used to generate the figure
    chart_type: Literal[
        "line", "bar", "scatter", "heatmap", "contour",
        "trajectory_2d", "trajectory_3d", "table_figure", "summary",
    ]
    required_labels: dict[str, str]  # {"title": "...", "x": "...", "y": "..."}
    figure_role: str = "result_summary"  # semantic role
    description: str = ""  # human-readable description
    priority: int = 1  # 1=high, 2=medium, 3=low
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, work_dir: str | Path) -> Path:
        path = Path(work_dir) / f"figure_request_{self.figure_id}.json"
        save_json(self.to_dict(), str(path))
        return path


class FigureRequestPlanner:
    """Generates FigureRequest[] from the result_registry.

    Analyzes verified results and the problem context to determine
    what figures are needed for the paper.
    """

    @staticmethod
    def plan_from_registry(
        result_registry: dict[str, Any],
        chart_language: str,
        problem_context: str = "",
    ) -> list[FigureRequest]:
        """Generate figure requests from verified results.

        This is a heuristic planner. In the future, it should be driven by
        the ProblemContract and solve_spec.
        """
        requests: list[FigureRequest] = []
        verified = result_registry.get("verified_results", []) or []

        if not verified:
            return requests

        # Group results by source/problem
        result_groups: dict[str, list[dict[str, Any]]] = {}
        for entry in verified:
            if not isinstance(entry, dict):
                continue
            # Use source_data or id prefix as grouping key
            source = entry.get("source_data", [])
            group_key = "default"
            if isinstance(source, list) and source:
                group_key = str(source[0]).split("_")[0] if source else "default"
            elif entry.get("id"):
                group_key = str(entry["id"]).split("_")[0] if "_" in str(entry["id"]) else "default"

            if group_key not in result_groups:
                result_groups[group_key] = []
            result_groups[group_key].append(entry)

        # Generate summary figure for each group
        for group_key, entries in result_groups.items():
            result_ids = [str(e.get("id", "")) for e in entries if e.get("id")]
            if not result_ids:
                continue

            # Determine chart type from data
            chart_type = "bar"  # default
            for entry in entries:
                if isinstance(entry.get("value"), (list, dict)):
                    chart_type = "line"
                    break

            # Generate labels
            if chart_language == "Simplified Chinese":
                title = f"{group_key} 求解结果汇总"
                x_label = "参数"
                y_label = "值"
            else:
                title = f"{group_key} Solver Results Summary"
                x_label = "Parameter"
                y_label = "Value"

            requests.append(FigureRequest(
                figure_id=f"fig_{group_key}_summary",
                chart_language=chart_language,
                linked_result_ids=result_ids[:5],  # limit to first 5
                source_data=["result_registry.json"],
                chart_type=chart_type,
                required_labels={"title": title, "x": x_label, "y": y_label},
                figure_role="result_summary",
                description=f"Summary figure for {len(entries)} verified results from {group_key}",
            ))

        return requests

    @staticmethod
    def save_all(requests: list[FigureRequest], work_dir: str | Path) -> list[Path]:
        """Save all figure requests to the work directory."""
        paths = []
        for req in requests:
            paths.append(req.save(work_dir))
        # Also save the index
        index = {
            "figure_requests": [r.to_dict() for r in requests],
            "total_count": len(requests),
        }
        save_json(index, str(Path(work_dir) / "figure_requests_index.json"))
        return paths
