"""Guidance 职责边界：decomposition_context。"""

from __future__ import annotations

from app.guidance.contracts import ProblemStructureGenerationDraft


_FACT_EXTRACTION_TABLE_CELL_LIMIT = 64


def validate_deliverable_requests(
    structure: ProblemStructureGenerationDraft, input_catalog: dict,
) -> None:
    if not structure.subproblems:
        raise ValueError("缺少题目要求的交付目标。请登记一个整体数学问题或多个有题面依据的独立问题。")
    source_texts = [structure.task_summary]
    for block in input_catalog.get("blocks", []):
        source_texts.append(block.get("text", ""))
        for row in block.get("rows", []):
            source_texts.extend(cell for cell in row if isinstance(cell, str))
    quotes = set()
    for subproblem in structure.subproblems:
        quote = subproblem.request_quote
        if not quote and len(structure.subproblems) == 1:
            continue
        if (not quote or not any(quote in source for source in source_texts)
                or any(quote in prior or prior in quote for prior in quotes)):
            raise ValueError(
                f"{subproblem.id}.request_quote 必须逐字引用题面中要求独立答案的不同原文。"
                "若条目都是为同一答案建模、求解或验算，请合并为一个子题，把这些要求放入 goals 和 outputs。"
            )
        quotes.add(quote)

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
