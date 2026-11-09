"""建模前的完整来源决定、独立许可与一次不可变应用。"""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.artifacts.evidence_profile import EvidenceProfile
from app.artifacts.input_catalog import InputCatalog
from app.guidance.contracts import LockedFactSpec, ProblemSpec
from app.guidance.recovery import canonical_digest, workspace_file

class SourceCitation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_ref: str = Field(min_length=1, max_length=500)
    quote: str = Field(min_length=1, max_length=4000)


class SourceDecisionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    uncertainty_id: str = Field(min_length=1, max_length=120)
    subproblem_ids: list[str] = Field(min_length=1, max_length=50)
    status: Literal["specified", "ambiguous", "not_assessed"]
    replacement_fact: LockedFactSpec | None = None
    evidence: list[SourceCitation] = Field(max_length=20)
    rationale: str = Field(min_length=1, max_length=2400)

    @model_validator(mode="after")
    def require_decision_content(self):
        if (self.status == "specified") != (self.replacement_fact is not None):
            raise ValueError("only a specified source decision requires a complete replacement fact")
        if self.status != "not_assessed" and not self.evidence:
            raise ValueError("specified or ambiguous decisions require original source evidence")
        return self


class SourceInterpretationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decisions: list[SourceDecisionSpec]


class SourceAdjudicationCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    uncertainty_id: str = Field(min_length=1, max_length=120)
    status: Literal["supported", "contradicted", "not_assessed"]
    evidence: list[SourceCitation] = Field(max_length=20)
    rationale: str = Field(min_length=1, max_length=2400)

    @model_validator(mode="after")
    def require_decisive_evidence(self):
        if self.status != "not_assessed" and not self.evidence:
            raise ValueError("supported or contradicted adjudication requires original source evidence")
        return self


class SourceAdjudicationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checks: list[SourceAdjudicationCheck]


class SourcePreflightRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["2.0"] = "2.0"
    original_problem_spec: ProblemSpec
    resolved_problem_spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_files_sha256: dict[str, str]
    interpretation: SourceInterpretationResult | None
    adjudication: SourceAdjudicationResult | None
    interpretation_failure_reason: str | None = Field(default=None, min_length=1, max_length=1600)
    adjudication_failure_reason: str | None = Field(default=None, min_length=1, max_length=1600)
    status: Literal["ready", "partial", "blocked"]
    affected_subproblem_ids: list[str]
    blocked_subproblem_ids: list[str]
    binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _source_file_snapshots(input_catalog, evidence_profile, work_dir):
    sources = {}
    entries = []
    if input_catalog is not None:
        entries.append((input_catalog["source_file"], input_catalog["source_sha256"]))
    entries.extend((item["source_file"], item["sha256"]) for item in evidence_profile["evidence"]
                   if item["kind"] != "embedded_table")
    for reference, digest in entries:
        if reference in sources and sources[reference] != digest:
            raise ValueError("source preflight has conflicting original file snapshots")
        if work_dir is not None:
            _verified_file(work_dir, reference, digest)
        sources[reference] = digest
    return sources


def _validate_citations(evidence: list[SourceCitation], sources) -> None:
    for citation in evidence:
        source = sources.get(citation.source_ref)
        if source is None or citation.quote not in source[0]:
            raise ValueError(f"quote is not in the registered original source: {citation.source_ref}")


def validate_source_interpretation(
    problem_spec: ProblemSpec, interpretation: SourceInterpretationResult, *,
    input_catalog: dict | None, evidence_profile: dict, work_dir: Path | None = None,
) -> None:
    interpretation = SourceInterpretationResult.model_validate(interpretation.model_dump(mode="json"))
    if [item.uncertainty_id for item in interpretation.decisions] != [
        item.uncertainty_id for item in problem_spec.uncertainties
    ]:
        raise ValueError("source decisions must cover all uncertainties exactly once in input order")
    sources = _registered_sources(input_catalog, evidence_profile, work_dir)
    fact_ids = {item.fact_id for item in problem_spec.locked_facts}
    for target, decision in zip(problem_spec.uncertainties, interpretation.decisions):
        if decision.subproblem_ids != target.subproblem_ids:
            raise ValueError("source decision must preserve the full registered scope")
        _validate_citations(decision.evidence, sources)
        replacement = decision.replacement_fact
        if replacement is None:
            continue
        if replacement.subproblem_ids != target.subproblem_ids:
            raise ValueError("replacement fact must preserve the full registered scope")
        if replacement.semantic_key != target.semantic_key:
            raise ValueError("replacement fact must preserve semantic identity")
        if replacement.fact_id in fact_ids:
            raise ValueError("source decision cannot overwrite another locked fact")
        fact_ids.add(replacement.fact_id)
        if (len(replacement.source_refs) != len(set(replacement.source_refs))
                or set(replacement.source_refs) != {item.source_ref for item in decision.evidence}):
            raise ValueError("replacement source references must exactly match quoted evidence")


def validate_source_adjudication_result(
    interpretation: SourceInterpretationResult, adjudication: SourceAdjudicationResult, *,
    input_catalog: dict | None, evidence_profile: dict, work_dir: Path | None = None,
) -> None:
    adjudication = SourceAdjudicationResult.model_validate(adjudication.model_dump(mode="json"))
    expected = [item.uncertainty_id for item in interpretation.decisions if item.status != "not_assessed"]
    if [item.uncertainty_id for item in adjudication.checks] != expected:
        raise ValueError("adjudication checks must cover specified and ambiguous decisions exactly once in order")
    sources = _registered_sources(input_catalog, evidence_profile, work_dir)
    for check in adjudication.checks:
        _validate_citations(check.evidence, sources)


def _with_descendants(problem_spec: ProblemSpec, owners: set[str]) -> list[str]:
    affected = set(owners)
    while True:
        expanded = affected | {item.id for item in problem_spec.subproblems
                               if affected.intersection(item.depends_on_subproblem_ids)}
        if expanded == affected:
            return [item.id for item in problem_spec.subproblems if item.id in affected]
        affected = expanded


def resolve_problem_sources(
    problem_spec: ProblemSpec,
    *,
    interpretation: SourceInterpretationResult | None,
    adjudication: SourceAdjudicationResult | None,
    input_catalog: dict | None,
    evidence_profile: dict,
    work_dir: Path | None = None,
    interpretation_failure_reason: str | None = None,
    adjudication_failure_reason: str | None = None,
) -> tuple[ProblemSpec, SourcePreflightRecord]:
    """两阶段原始结果绑定后只应用获许可的明确关系，其他范围确定性隔离。"""
    _registered_sources(input_catalog, evidence_profile, work_dir)
    source_files = _source_file_snapshots(input_catalog, evidence_profile, work_dir)
    if interpretation is None:
        if not interpretation_failure_reason or adjudication is not None or adjudication_failure_reason:
            raise ValueError("missing interpretation requires its failure without invented adjudication")
        decisions = []
    else:
        if interpretation_failure_reason:
            raise ValueError("completed interpretation cannot carry a request failure")
        validate_source_interpretation(problem_spec, interpretation, input_catalog=input_catalog,
                                       evidence_profile=evidence_profile, work_dir=work_dir)
        decisions = interpretation.decisions
        if adjudication is None:
            if not adjudication_failure_reason:
                raise ValueError("source decisions require independent adjudication permission or recorded failure")
        else:
            if adjudication_failure_reason:
                raise ValueError("completed adjudication cannot carry a request failure")
            validate_source_adjudication_result(interpretation, adjudication, input_catalog=input_catalog,
                                                evidence_profile=evidence_profile, work_dir=work_dir)
    supported = {item.uncertainty_id for item in adjudication.checks if item.status == "supported"} if adjudication else set()
    by_id = {item.uncertainty_id: item for item in decisions}
    replacements = {item.uncertainty_id: item.replacement_fact for item in decisions
                    if item.status == "specified" and item.uncertainty_id in supported}
    blocked_owners: set[str] = set()
    changed_owners: set[str] = set()
    for target in problem_spec.uncertainties:
        decision = by_id.get(target.uncertainty_id)
        if decision is None or decision.status == "not_assessed" or target.uncertainty_id not in supported:
            blocked_owners.update(target.subproblem_ids)
        if target.uncertainty_id in replacements:
            changed_owners.update(target.subproblem_ids)
    # 从完整原快照构造新值；拒绝和未评估不删除UNC，也不改变其含义。
    original = problem_spec.model_dump(mode="json")
    original["locked_facts"] += [item.model_dump(mode="json") for item in replacements.values()]
    original["uncertainties"] = [
        item.model_dump(mode="json") for item in problem_spec.uncertainties
        if item.uncertainty_id not in replacements
    ]
    for subproblem in original["subproblems"]:
        subproblem["required_fact_ids"] = [
            item["fact_id"] for item in original["locked_facts"] if subproblem["id"] in item["subproblem_ids"]
        ]
        subproblem["required_uncertainty_ids"] = [
            item["uncertainty_id"] for item in original["uncertainties"] if subproblem["id"] in item["subproblem_ids"]
        ]
    resolved = ProblemSpec.model_validate(original)
    blocked = _with_descendants(problem_spec, blocked_owners)
    status = "ready" if not blocked else "blocked" if len(blocked) == len(problem_spec.subproblems) else "partial"
    payload = {
        "schema_version": "2.0", "original_problem_spec": problem_spec.model_dump(mode="json"),
        "resolved_problem_spec_sha256": canonical_digest(resolved),
        "input_catalog_sha256": canonical_digest(input_catalog),
        "evidence_profile_sha256": canonical_digest(evidence_profile), "source_files_sha256": source_files,
        "interpretation": interpretation.model_dump(mode="json") if interpretation is not None else None,
        "adjudication": adjudication.model_dump(mode="json") if adjudication is not None else None,
        "interpretation_failure_reason": interpretation_failure_reason,
        "adjudication_failure_reason": adjudication_failure_reason,
        "status": status, "affected_subproblem_ids": _with_descendants(problem_spec, changed_owners),
        "blocked_subproblem_ids": blocked,
    }
    return resolved, SourcePreflightRecord.model_validate({**payload, "binding_sha256": canonical_digest(payload)})


def validate_source_preflight(
    record: SourcePreflightRecord,
    *,
    problem_spec: ProblemSpec,
    input_catalog: dict | None,
    evidence_profile: dict,
    work_dir: Path | None = None,
) -> None:
    record = SourcePreflightRecord.model_validate(record.model_dump(mode="json"))
    if record.binding_sha256 != canonical_digest(record.model_dump(mode="json", exclude={"binding_sha256"})):
        raise ValueError("source preflight binding differs from its recorded decisions")
    rebuilt, expected = resolve_problem_sources(
        record.original_problem_spec,
        interpretation=record.interpretation, adjudication=record.adjudication,
        input_catalog=input_catalog, evidence_profile=evidence_profile, work_dir=work_dir,
        interpretation_failure_reason=record.interpretation_failure_reason,
        adjudication_failure_reason=record.adjudication_failure_reason,
    )
    if expected != record or rebuilt != problem_spec:
        raise ValueError("source preflight record does not match its bound snapshots")


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
