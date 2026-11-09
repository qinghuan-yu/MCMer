"""有原文证据的题意纠正与不可变快照。"""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.artifacts.evidence_profile import EvidenceProfile
from app.artifacts.input_catalog import InputCatalog
from app.guidance.contracts import LockedFactSpec, ProblemSpec
from app.guidance.recovery import canonical_digest, workspace_file


class SourceCitation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_ref: str = Field(min_length=1, max_length=500)
    quote: str = Field(min_length=1, max_length=4000)


class SourceCorrectionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    correction_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$", max_length=120)
    target_kind: Literal["uncertainty", "locked_fact"]
    target_id: str = Field(min_length=1, max_length=120)
    replacement_fact: LockedFactSpec
    evidence: list[SourceCitation] = Field(min_length=1, max_length=20)
    rationale: str = Field(min_length=1, max_length=1600)


class SourceResolutionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prior_problem_spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolved_problem_spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    corrections: list[SourceCorrectionSpec]
    affected_subproblem_ids: list[str]
    source_evidence_sha256: dict[str, str]


def resolve_problem_sources(
    problem_spec: ProblemSpec,
    proposals: list[tuple[str, SourceCorrectionSpec]],
    *,
    input_catalog: dict | None,
    evidence_profile: dict,
    work_dir: Path | None = None,
) -> tuple[ProblemSpec, SourceResolutionRecord]:
    # 从完整旧快照构造新值；任何拒绝都不应留下部分修改。
    original = problem_spec.model_dump(mode="json")
    targets = {
        **{("locked_fact", item.fact_id): item for item in problem_spec.locked_facts},
        **{("uncertainty", item.uncertainty_id): item for item in problem_spec.uncertainties},
    }
    replacements: dict[tuple[str, str], LockedFactSpec] = {}
    identities: dict[str, tuple[str, str]] = {}
    corrections: list[SourceCorrectionSpec] = []
    source_hashes: dict[str, str] = {}
    affected: set[str] = set()
    sources = _registered_sources(input_catalog, evidence_profile, work_dir) if proposals else {}

    for proposer, supplied in proposals:
        correction = SourceCorrectionSpec.model_validate(supplied.model_dump(mode="json"))
        key = (correction.target_kind, correction.target_id)
        target = targets.get(key)
        if target is None:
            raise ValueError(f"source correction references unknown target: {key!r}")
        replacement = correction.replacement_fact
        if proposer not in target.subproblem_ids or replacement.subproblem_ids != target.subproblem_ids:
            raise ValueError("source correction must preserve the full registered scope")
        if replacement.semantic_key != target.semantic_key:
            raise ValueError("source correction must preserve semantic identity")
        if correction.target_kind == "locked_fact" and replacement.fact_id != correction.target_id:
            raise ValueError("locked fact correction must preserve its fact ID")
        if any(item.fact_id == replacement.fact_id and ("locked_fact", item.fact_id) != key
               for item in problem_spec.locked_facts):
            raise ValueError("source correction cannot overwrite another locked fact")
        if key in replacements and replacements[key] != replacement:
            raise ValueError(f"conflicting source corrections for {key!r}")
        if correction.correction_id in identities and identities[correction.correction_id] != key:
            raise ValueError("source correction identifier cannot refer to different targets")
        references = replacement.source_refs
        if len(references) != len(set(references)) or set(references) != {item.source_ref for item in correction.evidence}:
            raise ValueError("replacement source references must exactly match quoted evidence")
        for citation in correction.evidence:
            source = sources.get(citation.source_ref)
            if source is None:
                raise ValueError(f"unknown correction evidence source: {citation.source_ref}")
            content, digest = source
            if citation.quote not in content:
                raise ValueError(f"correction quote is not in registered source: {citation.source_ref}")
            source_hashes[citation.source_ref] = digest
        replacements[key] = replacement
        affected.update(target.subproblem_ids)
        # 不同 ID 的等价提议都保留，便于各题精确引用撤回证据；只应用一次目标。
        if correction.correction_id not in identities:
            corrections.append(correction)
            identities[correction.correction_id] = key

    original["locked_facts"] = [
        replacements.get(("locked_fact", item.fact_id), item).model_dump(mode="json")
        for item in problem_spec.locked_facts
    ] + [
        replacements[("uncertainty", item.uncertainty_id)].model_dump(mode="json")
        for item in problem_spec.uncertainties if ("uncertainty", item.uncertainty_id) in replacements
    ]
    original["uncertainties"] = [
        item.model_dump(mode="json") for item in problem_spec.uncertainties
        if ("uncertainty", item.uncertainty_id) not in replacements
    ]
    for subproblem in original["subproblems"]:
        subproblem["required_fact_ids"] = [
            item["fact_id"] for item in original["locked_facts"] if subproblem["id"] in item["subproblem_ids"]
        ]
        subproblem["required_uncertainty_ids"] = [
            item["uncertainty_id"] for item in original["uncertainties"] if subproblem["id"] in item["subproblem_ids"]
        ]
    resolved = ProblemSpec.model_validate(original)
    while True:
        expanded = affected | {
            item.id for item in problem_spec.subproblems
            if affected.intersection(item.depends_on_subproblem_ids)
        }
        if expanded == affected:
            break
        affected = expanded
    return resolved, SourceResolutionRecord(
        prior_problem_spec_sha256=canonical_digest(problem_spec),
        resolved_problem_spec_sha256=canonical_digest(resolved), corrections=corrections,
        affected_subproblem_ids=[item.id for item in problem_spec.subproblems if item.id in affected],
        source_evidence_sha256=source_hashes,
    )


def validate_source_resolution(
    record: SourceResolutionRecord,
    *,
    previous_problem_spec: ProblemSpec,
    problem_spec: ProblemSpec,
    input_catalog: dict | None,
    evidence_profile: dict,
    work_dir: Path | None = None,
) -> None:
    rebuilt, expected = resolve_problem_sources(
        previous_problem_spec,
        [(item.replacement_fact.subproblem_ids[0], item) for item in record.corrections],
        input_catalog=input_catalog, evidence_profile=evidence_profile, work_dir=work_dir,
    )
    if expected != record or rebuilt != problem_spec:
        raise ValueError("source resolution record does not match its bound snapshots")


def _verified_file(root: Path, reference: str, digest: str) -> Path:
    path = workspace_file(root, reference)
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ValueError(f"correction source file is unavailable: {reference}") from exc
    if actual != digest:
        raise ValueError(f"correction source file hash differs from its snapshot: {reference}")
    return path


def _table_text(rows: list[list]) -> str:
    return "\n".join("\t".join(str(value) for value in row) for row in rows)


def _registered_sources(input_catalog: dict | None, evidence_profile: dict, root: Path | None):
    sources: dict[str, tuple[str, str]] = {}
    catalog = InputCatalog.model_validate(input_catalog) if input_catalog is not None else None
    catalog_blocks = {}
    if catalog is not None:
        actual_blocks = None
        original_text = None
        if root is not None:
            path = _verified_file(root, catalog.source_file, catalog.source_sha256)
            if path.suffix.lower() == ".docx":
                from app.artifacts.input_catalog import _docx_blocks

                actual_blocks = {item.source_ref: item for item in _docx_blocks(path, catalog.source_file)}
            elif path.suffix.lower() in {".txt", ".md"}:
                original_text = path.read_text(encoding="utf-8-sig")
        for block in catalog.blocks:
            if block.source_ref in sources or block.source_ref != f"{catalog.source_file}#{block.block_id}":
                raise ValueError("duplicate or inconsistent catalog source reference")
            content = block.text if block.kind == "paragraph" else _table_text(block.rows)
            payload = ({"kind": "paragraph", "text": block.text} if block.kind == "paragraph"
                       else {"kind": "table", "rows": block.rows})
            if canonical_digest(payload) != block.sha256:
                raise ValueError("catalog source block digest does not match its contents")
            if actual_blocks is not None and actual_blocks.get(block.source_ref) != block:
                raise ValueError("catalog source block does not match the original document")
            if original_text is not None and content not in original_text:
                raise ValueError("catalog source text does not match the original file")
            sources[block.source_ref] = content, block.sha256
            catalog_blocks[block.source_ref] = block

    profile = EvidenceProfile.model_validate(evidence_profile)
    profile_refs: set[str] = set()
    for item in profile.evidence:
        if item.source_ref in profile_refs or item.source_ref.split("#", 1)[0] != item.source_file:
            raise ValueError("duplicate or inconsistent evidence source reference")
        profile_refs.add(item.source_ref)
        if item.kind == "embedded_table":
            block = catalog_blocks.get(item.source_ref)
            if block is None or block.kind != "table" or block.sha256 != item.sha256 or block.rows != item.rows:
                raise ValueError("embedded table source snapshot does not match its catalog")
            continue
        if item.source_ref in sources:
            raise ValueError("conflicting evidence source registration")
        expected_kind = {
            ".csv": "uploaded_csv", ".xlsx": "uploaded_workbook", ".xlsm": "uploaded_workbook",
        }.get(Path(item.source_file).suffix.lower(), "uploaded_file")
        if item.kind != expected_kind:
            raise ValueError("uploaded source kind does not match its original file format")
        path = _verified_file(root, item.source_file, item.sha256) if root is not None else None
        if expected_kind == "uploaded_file":
            # 未解析文件只有字节摘要，不能把快照自报的行值注册为原文。
            continue
        rows = item.rows
        detail = item.profile
        if item.kind == "uploaded_csv":
            if path is not None:
                with path.open("r", encoding="utf-8-sig", newline="") as handle:
                    actual_rows = list(csv.reader(handle))
                if rows and rows != actual_rows:
                    raise ValueError("uploaded table snapshot differs from original source rows")
                if detail and (detail.get("columns") != (actual_rows[0] if actual_rows else [])
                               or detail.get("sample_rows") != actual_rows[1:5]):
                    raise ValueError("uploaded table profile differs from original source rows")
            if not rows:
                rows = [detail.get("columns", []), *detail.get("sample_rows", [])]
        elif item.kind == "uploaded_workbook":
            if path is not None:
                from app.artifacts.evidence_profile import _profile_workbook

                if detail != _profile_workbook(path):
                    raise ValueError("workbook profile differs from original source snapshot")
            rows = [row for sheet in detail.get("sheets", [])
                    for row in [sheet.get("columns", []), *sheet.get("sample_rows", [])]]
        sources[item.source_ref] = _table_text(rows), item.sha256
    return sources
