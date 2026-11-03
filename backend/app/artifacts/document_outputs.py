"""document outputs responsibilities."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any
from app.utils.log_util import logger


def _safe_artifact_basename(value: str) -> str:
    """Limit custom primary document names to simple local basenames."""
    name = str(value or "res").strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]+", name):
        return "res"
    return name


def _skip_docx_export(result_payload: dict[str, Any], artifact_basename: str) -> bool:
    """Guidance deliverables are Markdown-first and intentionally skip DOCX."""
    if artifact_basename == "guidance":
        return True
    if isinstance(result_payload, dict):
        return result_payload.get("primary_artifact_type") == "guidance"
    return False


def append_guidance_audit_appendix(markdown: str, result_payload: dict[str, Any] | None) -> str:
    """Append a compact parameter/source table for machine auditability."""
    if not isinstance(result_payload, dict):
        return markdown
    if result_payload.get("primary_artifact_type") != "guidance":
        return markdown
    if "机器可审计参数附录" in markdown:
        return markdown

    guidance_context = result_payload.get("guidance_context")
    if not isinstance(guidance_context, dict):
        return markdown
    parameter_registry = guidance_context.get("parameter_registry")
    if not isinstance(parameter_registry, dict):
        return markdown
    parameters = parameter_registry.get("parameters")
    if not isinstance(parameters, list) or not parameters:
        return markdown

    rows = [
        "\n\n## 机器可审计参数附录\n",
        "\n| 参数ID | 符号 | 名称 | 取值 | 来源类型 | 来源位置 | 信任状态 | 关联结果 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for parameter in parameters:
        if not isinstance(parameter, dict):
            continue
        rows.append(
            "| "
            + " | ".join(
                [
                    _escape_table_cell(parameter.get("id")),
                    _escape_table_cell(parameter.get("symbol")),
                    _escape_table_cell(parameter.get("name")),
                    _escape_table_cell(parameter.get("value")),
                    _escape_table_cell(parameter.get("source_type")),
                    _escape_table_cell(parameter.get("source_ref") or parameter.get("source_location")),
                    _escape_table_cell(parameter.get("trust_status")),
                    _escape_table_cell(parameter.get("linked_result_ids")),
                ]
            )
            + " |"
        )
    return markdown.rstrip() + "\n" + "\n".join(rows) + "\n"


def _escape_table_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value)
    return text.replace("\n", " ").replace("|", "\\|")


def _write_compat_aliases(root: Path, artifact_basename: str, paper_path: str, docx_path: str) -> None:
    """Keep legacy res.* consumers working while newer tasks use guidance.*."""
    if artifact_basename == "res":
        return

    compat_md = root / "res.md"
    compat_docx = root / "res.docx"
    try:
        shutil.copyfile(paper_path, compat_md)
        if docx_path and Path(docx_path).exists():
            shutil.copyfile(docx_path, compat_docx)
        elif compat_docx.exists():
            compat_docx.unlink()
    except Exception as exc:
        logger.warning("Failed to write legacy res.* aliases for %s: %s", paper_path, exc)


def _remove_docx_outputs(root: Path, docx_path: str) -> None:
    """Remove stale DOCX files when an artifact is Markdown-only."""
    candidates = [root / "res.docx"]
    if docx_path:
        candidates.append(Path(docx_path))
    for candidate in candidates:
        try:
            if candidate.exists():
                candidate.unlink()
        except Exception as exc:
            logger.warning("Failed to remove stale DOCX artifact %s: %s", candidate, exc)
