"""Compatibility exports for the visualization protocol.

New code should import schema objects from ``result_schema`` and
``visualization_schema``.  This module remains as the stable legacy import path
for existing planner and tests.
"""

from __future__ import annotations

from typing import Any

from app.artifacts.protocols import RESULT_OVERVIEW_ROLE, canonical_caption_for_role, normalize_figure_role
from app.artifacts.result_schema import VerifiedResult, normalize_verified_results
from app.artifacts.visualization_schema import VisualizationContract


def result_overview_request(
    result: VerifiedResult,
    chart_language: str,
    data_files: list[str] | None = None,
) -> dict[str, Any]:
    caption = canonical_caption_for_role(RESULT_OVERVIEW_ROLE, chart_language)
    section = result.section or "problem_1"
    request_id = f"fig_{section}_{RESULT_OVERVIEW_ROLE}"
    source_data = data_files or result.data_artifacts or ["result_registry.json"]
    return {
        "id": request_id,
        "problem_section": section,
        "subproblem_id": section,
        "figure_kind": "result_figure",
        "semantic_role": RESULT_OVERVIEW_ROLE,
        "role": RESULT_OVERVIEW_ROLE,
        "canonical_caption": caption,
        "purpose": caption,
        "chart_intent": "numeric_overview",
        "view_type": "numeric_overview",
        "required": True,
        "must_include_in_paper": True,
        "chart_language": chart_language,
        "language": chart_language,
        "required_columns": [],
        "required_data": source_data,
        "depends_on": {
            "model_objects": [],
            "formulas": [],
            "data_files": source_data,
            "result_ids": [result.id],
        },
        "linked_result_ids": [result.id],
        "expected_content": [caption],
    }
