"""Guidance 职责边界：decomposition_context。"""

from __future__ import annotations

from app.guidance.contracts import ProblemStructureGenerationDraft


_FACT_EXTRACTION_TABLE_CELL_LIMIT = 64

def _compact_fact_input_catalog(input_catalog: dict) -> dict:
    return {
        **input_catalog,
        "blocks": [
            _compact_large_table(item)
            for item in input_catalog.get("blocks", [])
        ],
    }

def _project_registered_data_sources(
    structure: ProblemStructureGenerationDraft,
    evidence_profile: dict,
) -> ProblemStructureGenerationDraft:
    registered_source_refs = {
        str(item.get("source_ref") or "")
        for item in evidence_profile.get("evidence", [])
        if isinstance(item, dict) and item.get("source_ref")
    }
    return structure.model_copy(update={
        "data_sources": [
            item
            for item in structure.data_sources
            if item.source_ref in registered_source_refs
        ],
    })

def _compact_fact_evidence_profile(evidence_profile: dict) -> dict:
    return {
        **evidence_profile,
        "evidence": [
            _compact_large_table(item)
            for item in evidence_profile.get("evidence", [])
        ],
    }

def _compact_large_table(item: dict) -> dict:
    rows = item.get("rows")
    if not isinstance(rows, list):
        return dict(item)
    column_count = max(
        (
            len(row)
            for row in rows
            if isinstance(row, list)
        ),
        default=0,
    )
    cell_count = sum(
        len(row)
        for row in rows
        if isinstance(row, list)
    )
    if cell_count <= _FACT_EXTRACTION_TABLE_CELL_LIMIT:
        return dict(item)
    profile = item.get("profile")
    return {
        **item,
        "rows": [],
        "profile": {
            **(profile if isinstance(profile, dict) else {}),
            "row_count": len(rows),
            "column_count": column_count,
        },
        "content_omitted": True,
    }
