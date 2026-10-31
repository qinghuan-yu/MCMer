"""Unified evidence profile for problem-source tables and uploaded datasets."""

from __future__ import annotations

import csv
import hashlib
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.artifacts.input_catalog import InputCatalog


SOURCE_LABEL_PATTERN = re.compile(
    r"(?i)(?:附件|附录)\s*[a-z0-9一二三四五六七八九十]+|appendix\s*[a-z0-9]+"
)


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    kind: Literal["embedded_table", "uploaded_workbook", "uploaded_csv", "uploaded_file"]
    source_ref: str = Field(min_length=1)
    source_file: str = Field(min_length=1)
    labels: list[str] = Field(default_factory=list)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    rows: list[list[Any]] = Field(default_factory=list)
    profile: dict[str, Any] = Field(default_factory=dict)


class EvidenceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    evidence: list[EvidenceItem] = Field(default_factory=list)


def build_evidence_profile(root: str | Path) -> EvidenceProfile:
    work_dir = Path(root)
    evidence = [*_embedded_table_evidence(work_dir), *_uploaded_file_evidence(work_dir)]
    profile = EvidenceProfile(evidence=evidence)
    (work_dir / "evidence_profile.json").write_text(
        profile.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return profile


def load_evidence_profile(root: str | Path) -> EvidenceProfile:
    path = Path(root) / "evidence_profile.json"
    if path.is_file():
        return EvidenceProfile.model_validate_json(path.read_text(encoding="utf-8"))
    return build_evidence_profile(root)


def extract_source_labels(text: str) -> list[str]:
    return list(dict.fromkeys(
        re.sub(r"\s+", "", match.group(0))
        for match in SOURCE_LABEL_PATTERN.finditer(text or "")
    ))


def evidence_matches_reference(item: EvidenceItem, reference: str) -> bool:
    normalized = _normalize_reference(reference)
    if any(_normalize_reference(label) == normalized for label in item.labels):
        return True
    return normalized in _normalize_reference(item.source_ref)


def _embedded_table_evidence(root: Path) -> list[EvidenceItem]:
    path = root / "input_catalog.json"
    if not path.is_file():
        return []
    catalog = InputCatalog.model_validate_json(path.read_text(encoding="utf-8"))
    items: list[EvidenceItem] = []
    active_labels: list[str] = []
    table_index = 0
    for block in catalog.blocks:
        if block.kind == "paragraph":
            labels = extract_source_labels(block.text)
            if labels:
                active_labels = labels
            continue
        table_index += 1
        items.append(EvidenceItem(
            evidence_id=f"question.table.{table_index}",
            kind="embedded_table",
            source_ref=block.source_ref,
            source_file=catalog.source_file,
            labels=active_labels,
            sha256=block.sha256,
            size_bytes=len(
                str(block.rows).encode("utf-8")
            ),
            rows=block.rows,
            profile={
                "row_count": len(block.rows),
                "column_count": max((len(row) for row in block.rows), default=0),
            },
        ))
    return items


def _uploaded_file_evidence(root: Path) -> list[EvidenceItem]:
    data_dir = root / "data"
    if not data_dir.is_dir():
        return []
    items: list[EvidenceItem] = []
    for index, path in enumerate(
        sorted(candidate for candidate in data_dir.rglob("*") if candidate.is_file()),
        start=1,
    ):
        relative = path.relative_to(root).as_posix()
        suffix = path.suffix.lower()
        kind: Literal["uploaded_workbook", "uploaded_csv", "uploaded_file"]
        detail: dict[str, Any]
        if suffix in {".xlsx", ".xlsm"}:
            kind = "uploaded_workbook"
            detail = _profile_workbook(path)
        elif suffix == ".csv":
            kind = "uploaded_csv"
            detail = _profile_csv(path)
        else:
            kind = "uploaded_file"
            detail = {"suffix": suffix}
        items.append(EvidenceItem(
            evidence_id=f"upload.file.{index}",
            kind=kind,
            source_ref=relative,
            source_file=relative,
            labels=extract_source_labels(path.stem),
            sha256=_file_sha256(path),
            size_bytes=path.stat().st_size,
            profile=detail,
        ))
    return items


def _profile_workbook(path: Path) -> dict:
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=False, data_only=True)
    try:
        sheets: list[dict] = []
        for sheet in workbook.worksheets:
            effective_max_column = max(
                (
                    cell.column
                    for row in sheet.iter_rows()
                    for cell in row
                    if cell.value is not None
                ),
                default=0,
            )
            sampled_rows = [
                (row_number, list(values))
                for row_number, values in enumerate(
                    sheet.iter_rows(
                        min_row=1,
                        max_row=min(sheet.max_row or 0, 6),
                        max_col=effective_max_column,
                        values_only=True,
                    ),
                    start=1,
                )
                if any(value is not None for value in values)
            ]
            header_row = sampled_rows[0][0] if sampled_rows else 0
            header_values = sampled_rows[0][1] if sampled_rows else []
            columns = [str(value) for value in header_values]
            merged_ranges = [
                merged
                for merged in sheet.merged_cells.ranges
                if merged.min_col <= effective_max_column
            ]
            vertical_group_ranges = [
                merged
                for merged in merged_ranges
                if merged.min_col == merged.max_col
                and merged.max_row > merged.min_row
                and merged.min_row > header_row
                and sheet.cell(merged.min_row, merged.min_col).value is not None
            ]
            forward_fill_columns = list(dict.fromkeys(
                columns[merged.min_col - 1]
                for merged in vertical_group_ranges
                if merged.min_col <= len(columns)
            ))
            sample_rows: list[list] = []
            for row_number, raw_values in sampled_rows[1:5]:
                values = list(raw_values)
                for merged in vertical_group_ranges:
                    column_index = merged.min_col - 1
                    if merged.min_row <= row_number <= merged.max_row and values[column_index] is None:
                        values[column_index] = sheet.cell(merged.min_row, merged.min_col).value
                sample_rows.append([_json_safe_cell(value) for value in values])
            sheets.append({
                "name": sheet.title,
                "max_row": sheet.max_row,
                "max_column": sheet.max_column,
                "effective_max_column": effective_max_column,
                "ignored_trailing_empty_columns": max(
                    (sheet.max_column or 0) - effective_max_column,
                    0,
                ),
                "columns": columns,
                "merged_ranges": [str(merged) for merged in merged_ranges],
                "forward_fill_columns": forward_fill_columns,
                "sample_rows": sample_rows,
            })
        return {"sheets": sheets}
    finally:
        workbook.close()


def _profile_csv(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = []
        for index, row in enumerate(csv.reader(handle)):
            if index >= 6:
                break
            rows.append(row)
    return {
        "columns": rows[0] if rows else [],
        "sample_rows": rows[1:5],
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_reference(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value or "").casefold()


def _json_safe_cell(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
