"""Versioned, ordered evidence catalog for an uploaded problem source."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InputBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["paragraph", "table"]
    block_id: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    text: str = ""
    rows: list[list[str]] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_kind_content(self) -> InputBlock:
        if self.kind == "paragraph" and (not self.text or self.rows):
            raise ValueError("paragraph blocks require text and cannot contain rows")
        if self.kind == "table" and (not self.rows or self.text):
            raise ValueError("table blocks require rows and cannot contain text")
        return self


class InputCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    source_file: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    blocks: list[InputBlock] = Field(min_length=1)


def build_input_catalog(
    source_path: str | Path,
    *,
    work_dir: str | Path,
    extracted_text: str = "",
) -> InputCatalog:
    source = Path(source_path)
    root = Path(work_dir)
    source_ref = _relative_source_ref(source, root)
    if source.suffix.lower() == ".docx":
        blocks = _docx_blocks(source, source_ref)
    else:
        blocks = _text_blocks(extracted_text, source_ref)
    if not blocks:
        raise ValueError("problem source did not contain any catalogable blocks")
    catalog = InputCatalog(
        source_file=source_ref,
        source_sha256=_file_sha256(source),
        blocks=blocks,
    )
    (root / "input_catalog.json").write_text(
        catalog.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return catalog


def _docx_blocks(source: Path, source_ref: str) -> list[InputBlock]:
    from docx import Document
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(source)
    blocks: list[InputBlock] = []
    paragraph_index = 0
    table_index = 0
    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            text = Paragraph(child, document).text.strip()
            if not text:
                continue
            paragraph_index += 1
            block_id = f"paragraph-{paragraph_index}"
            payload = {"kind": "paragraph", "text": text}
            blocks.append(InputBlock(
                kind="paragraph",
                block_id=block_id,
                source_ref=f"{source_ref}#{block_id}",
                sha256=_payload_sha256(payload),
                text=text,
            ))
        elif isinstance(child, CT_Tbl):
            rows = [
                [cell.text.strip() for cell in row.cells]
                for row in Table(child, document).rows
            ]
            if not rows or not any(any(cell for cell in row) for row in rows):
                continue
            table_index += 1
            block_id = f"table-{table_index}"
            payload = {"kind": "table", "rows": rows}
            blocks.append(InputBlock(
                kind="table",
                block_id=block_id,
                source_ref=f"{source_ref}#{block_id}",
                sha256=_payload_sha256(payload),
                rows=rows,
            ))
    return blocks


def _text_blocks(text: str, source_ref: str) -> list[InputBlock]:
    blocks: list[InputBlock] = []
    for index, line in enumerate(
        (line.strip() for line in text.splitlines() if line.strip()),
        start=1,
    ):
        block_id = f"paragraph-{index}"
        blocks.append(InputBlock(
            kind="paragraph",
            block_id=block_id,
            source_ref=f"{source_ref}#{block_id}",
            sha256=_payload_sha256({"kind": "paragraph", "text": line}),
            text=line,
        ))
    return blocks


def _relative_source_ref(source: Path, root: Path) -> str:
    try:
        return source.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError("problem source must be inside the task work directory") from exc


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _payload_sha256(payload: dict) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
