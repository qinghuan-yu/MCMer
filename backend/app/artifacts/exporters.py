"""DocumentFinalizer — unified export outlet for all final documents.

Every ``res.md``, ``res.docx``, ``res_vN.docx``, and failure report must
go through this module.  It handles:
- Image reference normalisation
- Image whitelist enforcement
- Markdown writing
- DOCX export
- DOCX media validation
- ``res.json`` writing
- Revision version inheritance
- Failure report export
"""

from __future__ import annotations

import json
import os
import re
import asyncio
import zipfile
from pathlib import Path
from typing import Any

from app.utils.common_utils import (
    finalize_markdown_export,
    normalize_math_markdown,
    save_json,
)
from app.utils.figure_artifacts import normalize_artifact_path
from app.utils.log_util import logger


_TOOL_PROTOCOL_RE = re.compile(
    r"(<\s*[｜|]+\s*DSML\s*[｜|]+|</\s*[｜|]+\s*DSML\s*[｜|]+|"
    r"\btool_calls\b|invoke\s+name\s*=|parameter\s+name\s*=|run_script)",
    re.IGNORECASE,
)


def _contains_tool_protocol_text(content: str) -> bool:
    if not content:
        return False
    matches = _TOOL_PROTOCOL_RE.findall(content)
    if any("DSML" in str(match).upper() for match in matches):
        return True
    lowered = content.lower()
    return (
        "tool_calls" in lowered
        and ("invoke name" in lowered or "parameter name" in lowered or "run_script" in lowered)
    )


class FinalizationValidationError(ValueError):
    """Machine-readable finalization validation failure."""

    def __init__(self, message: str, error_code: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.error_code = error_code
        self.error_type = "finalizer_validation"
        self.details = details or {}


class DocumentFinalizer:
    """Single export outlet for all final documents."""

    @staticmethod
    async def finalize_async(
        work_dir: str,
        task_id: str,
        task_type: str,
        paper_content: str,
        allowed_images: list[str] | None,
        required_images: list[str] | None,
        result_payload: dict[str, Any],
        code_interpreter=None,
        version: int | None = None,
    ) -> tuple[str, str, str]:
        """Async version of finalize — saves notebook properly in async context."""
        root = Path(work_dir)

        # ── Save notebook ──────────────────────────────────────────────
        notebook_path = str(root / "notebook.ipynb")
        if code_interpreter is not None:
            try:
                await code_interpreter.save_notebook(notebook_path)
            except Exception as e:
                logger.warning("Notebook save failed: %s", e)

        # ── Normalise markdown ─────────────────────────────────────────
        if _contains_tool_protocol_text(paper_content):
            raise FinalizationValidationError(
                "Refusing to export tool-call protocol text as paper content.",
                error_code="FINALIZER_TOOL_PROTOCOL_LEAK",
            )
        normalized = normalize_math_markdown(paper_content)
        normalized = DocumentFinalizer._normalize_image_refs(work_dir, normalized, allowed_images)
        normalized = DocumentFinalizer._ensure_required_image_refs(normalized, allowed_images, required_images)
        DocumentFinalizer._validate_image_references(work_dir, normalized, allowed_images, required_images)
        normalized = DocumentFinalizer._enforce_whitelist(normalized, allowed_images)

        # ── Determine output paths ─────────────────────────────────────
        if version is not None and version > 0:
            paper_path = str(root / f"res_v{version}.md")
            docx_path = str(root / f"res_v{version}.docx")
        else:
            paper_path = str(root / "res.md")
            docx_path = str(root / "res.docx")

        # ── Write res.json ─────────────────────────────────────────────
        result_payload["task_id"] = task_id
        result_payload["task_type"] = task_type
        result_payload["paper"] = normalized
        save_json(result_payload, str(root / "res.json"))

        # ── Write markdown and export DOCX ─────────────────────────────
        docx_ok = finalize_markdown_export(paper_path, normalized, docx_path, allowed_images)
        if not docx_ok:
            logger.warning("DOCX export failed for %s", paper_path)
            docx_path = ""

        return paper_path, docx_path, notebook_path

    @staticmethod
    def finalize(
        work_dir: str,
        task_id: str,
        task_type: str,
        paper_content: str,
        allowed_images: list[str] | None,
        required_images: list[str] | None,
        result_payload: dict[str, Any],
        code_interpreter=None,
        version: int | None = None,
    ) -> tuple[str, str, str]:
        """Export the final paper and supporting files.

        Parameters
        ----------
        work_dir:
            Working directory for the task.
        task_id:
            Task identifier.
        task_type:
            "writing" or "polish".
        paper_content:
            Raw markdown content from the writer.
        allowed_images:
            Whitelist of image paths that may appear in the paper.
            ``None`` means "no images allowed" (failure report mode).
        result_payload:
            Data to write into ``res.json``.
        code_interpreter:
            Interpreter instance (for saving notebook).
        version:
            Revision version number.  ``None`` = first version (``res.md``).
            ``N`` = revision (``res_vN.md``).

        Returns
        -------
        (paper_path, docx_path, notebook_path)

        Notes
        -----
        This synchronous method must not be used to save notebooks from within
        a running asyncio event loop. In async contexts, call
        ``DocumentFinalizer.finalize_async()``.
        """
        root = Path(work_dir)

        # ── Save notebook ──────────────────────────────────────────────
        notebook_path = str(root / "notebook.ipynb")
        if code_interpreter is not None:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                # No running loop in this thread: safe to run coroutine synchronously.
                try:
                    asyncio.run(code_interpreter.save_notebook(notebook_path))
                except Exception as e:
                    logger.warning("Notebook save failed: %s", e)
            else:
                raise RuntimeError(
                    "DocumentFinalizer.finalize() cannot save notebook in async context; "
                    "use DocumentFinalizer.finalize_async()."
                )

        # ── Normalise markdown ─────────────────────────────────────────
        if _contains_tool_protocol_text(paper_content):
            raise FinalizationValidationError(
                "Refusing to export tool-call protocol text as paper content.",
                error_code="FINALIZER_TOOL_PROTOCOL_LEAK",
            )
        normalized = normalize_math_markdown(paper_content)
        normalized = DocumentFinalizer._normalize_image_refs(work_dir, normalized, allowed_images)
        normalized = DocumentFinalizer._ensure_required_image_refs(normalized, allowed_images, required_images)
        DocumentFinalizer._validate_image_references(work_dir, normalized, allowed_images, required_images)
        normalized = DocumentFinalizer._enforce_whitelist(normalized, allowed_images)

        # ── Determine output paths ─────────────────────────────────────
        if version is not None and version > 0:
            paper_path = str(root / f"res_v{version}.md")
            docx_path = str(root / f"res_v{version}.docx")
        else:
            paper_path = str(root / "res.md")
            docx_path = str(root / "res.docx")

        # ── Write res.json ─────────────────────────────────────────────
        result_payload["task_id"] = task_id
        result_payload["task_type"] = task_type
        result_payload["paper"] = normalized
        save_json(result_payload, str(root / "res.json"))

        # ── Write markdown and export DOCX ─────────────────────────────
        docx_ok = finalize_markdown_export(paper_path, normalized, docx_path, allowed_images)
        if not docx_ok:
            logger.warning("DOCX export failed for %s", paper_path)
            docx_path = ""

        return paper_path, docx_path, notebook_path

    @staticmethod
    def export_failure_report(
        work_dir: str,
        question: str,
        result_registry: dict[str, Any],
        gate_reason: str,
        rejection_summary: list[dict[str, Any]] | None = None,
        stage_outputs: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        """Export a structured failure report.

        Returns (md_path, docx_path).
        """
        root = Path(work_dir)
        summary = result_registry.get("summary", {}) if isinstance(result_registry, dict) else {}
        verified_count = summary.get("verified_count", 0) if isinstance(summary, dict) else 0
        blocked_count = summary.get("blocked_count", 0) if isinstance(summary, dict) else 0
        blocked_results = result_registry.get("blocked_results", []) if isinstance(result_registry, dict) else []

        lines = [
            "# 求解失败报告 / Solver Failure Report",
            "",
            "## 质量门禁未通过 / Quality Gate Failed",
            "",
            f"**原因**: {gate_reason}",
            "",
            "## 求解统计 / Solver Statistics",
            "",
            "| 指标 | 值 |",
            "| --- | --- |",
            f"| verified_count | {verified_count} |",
            f"| blocked_count | {blocked_count} |",
            "",
        ]

        if rejection_summary:
            lines.append("## 图片拒绝详情 / Figure Rejections")
            lines.append("")
            for r in rejection_summary:
                lines.append(f"- **{r.get('path', '?')}**: {', '.join(r.get('rejected_by', ['unknown']))}")
            lines.append("")

        if blocked_results:
            lines.append("## 阻断项详情 / Blocked Results")
            lines.append("")
            for i, entry in enumerate(blocked_results[:10], 1):
                if isinstance(entry, dict):
                    name = entry.get("name") or entry.get("id") or f"item_{i}"
                    status = entry.get("status", "unknown")
                    warnings = entry.get("warnings", [])
                    warn_text = "; ".join(str(w) for w in warnings[:3]) if warnings else "none"
                    lines.append(f"{i}. **{name}** — status: {status}, warnings: {warn_text}")
            lines.append("")

        lines.extend([
            "## 建议 / Recommendations",
            "",
            "1. 检查求解脚本是否正确加载数据并计算关键结果。",
            "2. 确认 key_results 的 status 字段被标记为 `verified` 而非 `blocked`。",
            "3. 如果存在 Excel 列名不匹配，请先打印 sheet/columns 再适配。",
            "4. 如果优化超时，降级为 coarse search + verified baseline。",
            "",
        ])

        report = "\n".join(lines)

        md_path = str(root / "failure_report.md")
        docx_path = str(root / "failure_report.docx")

        with open(md_path, "w", encoding="utf-8") as f:
            f.write(report)

        finalize_markdown_export(md_path, report, docx_path, allowed_images=[])

        # Write failure manifest
        manifest = {
            "gate_reason": gate_reason,
            "verified_count": verified_count,
            "blocked_count": blocked_count,
            "figure_rejections": rejection_summary or [],
            "blocked_results": [
                {"id": e.get("id"), "name": e.get("name"), "status": e.get("status")}
                for e in (blocked_results or [])
                if isinstance(e, dict)
            ][:10],
        }
        save_json(manifest, str(root / "failure_manifest.json"))

        return md_path, docx_path if Path(docx_path).exists() else ""

    # ── Internal helpers ───────────────────────────────────────────────

    @staticmethod
    def _normalize_image_refs(
        work_dir: str,
        markdown: str,
        allowed_images: list[str] | None,
    ) -> str:
        """Normalise image references to match allowed paths."""
        if not allowed_images or not markdown:
            return markdown

        allowed = {normalize_artifact_path(p) for p in allowed_images if str(p).strip()}
        by_name = {Path(p).name: p for p in allowed}
        pattern = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

        def _replace(m: re.Match[str]) -> str:
            alt = m.group(1)
            raw = normalize_artifact_path(m.group(2).strip().strip("<>"))
            if raw in allowed:
                return m.group(0)
            rel = by_name.get(Path(raw).name)
            if rel:
                return f"![{alt}]({rel})"
            return m.group(0)

        return pattern.sub(_replace, markdown)

    @staticmethod
    def _enforce_whitelist(
        markdown: str,
        allowed_images: list[str] | None,
    ) -> str:
        """Remove image references not in the whitelist."""
        if allowed_images is None:
            return markdown
        allowed = {normalize_artifact_path(p) for p in allowed_images if str(p).strip()}
        if not allowed:
            return re.sub(r"(?m)^\s*!\[[^\]]*\]\([^)]+\)\s*$\n?", "", markdown or "")

        def _strip_title(raw: str) -> str:
            target = raw.strip()
            if target.startswith("<"):
                end = target.find(">")
                if end != -1:
                    return target[1:end].strip()
            for sep in (' "', " '"):
                if sep in target:
                    return target.split(sep, 1)[0].strip()
            return target

        def _replace(m: re.Match[str]) -> str:
            raw = _strip_title(m.group(2)).replace("\\", "/").lstrip("./")
            return m.group(0) if raw in allowed else ""

        return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", _replace, markdown or "")

    @staticmethod
    def _extract_markdown_image_targets(markdown: str) -> list[str]:
        """Extract normalized image targets from Markdown image syntax."""
        if not markdown:
            return []

        def _strip_title(raw: str) -> str:
            target = raw.strip()
            if target.startswith("<"):
                end = target.find(">")
                if end != -1:
                    return target[1:end].strip()
            for sep in (' "', " '"):
                if sep in target:
                    return target.split(sep, 1)[0].strip()
            return target

        targets: list[str] = []
        for match in re.finditer(r"!\[[^\]]*\]\(([^)]+)\)", markdown):
            raw = _strip_title(match.group(1))
            normalized = normalize_artifact_path(raw)
            if normalized:
                targets.append(normalized)
        return targets

    @staticmethod
    def _ensure_required_image_refs(
        markdown: str,
        allowed_images: list[str] | None,
        required_images: list[str] | None,
    ) -> str:
        """Append missing required image refs before hard validation.

        This keeps the finalizer strict while making all export outlets robust
        against writer/revision text that accidentally omits required figures.
        """
        if not required_images:
            return markdown

        allowed = {
            normalize_artifact_path(path)
            for path in (allowed_images or [])
            if str(path).strip()
        }
        refs = set(DocumentFinalizer._extract_markdown_image_targets(markdown))
        missing = [
            normalize_artifact_path(path)
            for path in required_images
            if str(path).strip()
            and normalize_artifact_path(path) not in refs
            and (not allowed or normalize_artifact_path(path) in allowed)
        ]
        if not missing:
            return markdown

        additions = ["", "## 图表补充"]
        for path in missing:
            caption = Path(path).stem.replace("_", " ").strip() or "required figure"
            additions.append(f"\n![{caption}]({path})")
        return (markdown or "").rstrip() + "\n\n" + "\n".join(additions).strip() + "\n"

    @staticmethod
    def _validate_image_references(
        work_dir: str,
        markdown: str,
        allowed_images: list[str] | None,
        required_images: list[str] | None,
    ) -> None:
        """Hard-validate Markdown image references before export.

        Rules:
        - If ``allowed_images`` is not None, every referenced image must be in whitelist.
        - Every referenced image must exist on disk under work_dir.
        """
        refs = DocumentFinalizer._extract_markdown_image_targets(markdown)
        if not refs and not required_images:
            return

        allowed = None
        if allowed_images is not None:
            allowed = {
                normalize_artifact_path(path)
                for path in allowed_images
                if str(path).strip()
            }
            disallowed = [path for path in refs if path not in allowed]
            if disallowed:
                raise FinalizationValidationError(
                    "Markdown references images outside FigureBundle whitelist: "
                    + ", ".join(disallowed),
                    error_code="FINALIZER_UNWHITELISTED_IMAGE_REF",
                    details={"paths": disallowed},
                )

        missing = [
            path
            for path in refs
            if not (Path(work_dir) / path).exists()
        ]
        if missing:
            raise FinalizationValidationError(
                "Markdown references missing image files: " + ", ".join(missing),
                error_code="FINALIZER_MISSING_IMAGE_REF",
                details={"paths": missing},
            )

        if required_images:
            required = {
                normalize_artifact_path(path)
                for path in required_images
                if str(path).strip()
            }
            not_referenced = [path for path in required if path not in set(refs)]
            if not_referenced:
                raise FinalizationValidationError(
                    "Markdown is missing required figure references: " + ", ".join(not_referenced),
                    error_code="FINALIZER_REQUIRED_IMAGE_NOT_REFERENCED",
                    details={"paths": not_referenced},
                )
