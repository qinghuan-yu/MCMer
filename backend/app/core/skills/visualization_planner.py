"""Result-driven visualization planning skill.

The long-term workflow should ask this deterministic skill for figure requests
instead of asking an agent or scanning directories.  It reads verified results
and emits protocol-shaped figure requests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.artifacts.visualization_protocol import (
    normalize_verified_results,
    result_overview_request,
)


class VisualizationPlannerSkill:
    """Build fallback-safe figure requests from verified results."""

    @staticmethod
    def result_overview_requests(
        result_registry: dict[str, Any],
        chart_language: str,
        default_source_data: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        requests: list[dict[str, Any]] = []
        seen_sections: set[str] = set()
        source_data = default_source_data or ["result_registry.json"]

        for result in normalize_verified_results(result_registry):
            section = result.section or "problem_1"
            if section in seen_sections:
                continue
            seen_sections.add(section)
            requests.append(
                result_overview_request(
                    result=result,
                    chart_language=chart_language,
                    data_files=result.data_artifacts or source_data,
                )
            )
        return requests

    @staticmethod
    def discover_tabular_sources(work_dir: str) -> list[str]:
        root = Path(work_dir)
        sources: list[str] = []
        for folder in (root / "data", root / "inputs", root):
            if not folder.exists() or not folder.is_dir():
                continue
            for path in folder.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in {".csv", ".xlsx", ".xls"}:
                    continue
                try:
                    sources.append(path.relative_to(root).as_posix())
                except Exception:
                    sources.append(path.name)
        return list(dict.fromkeys(sources))
