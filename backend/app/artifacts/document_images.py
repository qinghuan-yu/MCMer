"""document images responsibilities."""

from __future__ import annotations

import re
from pathlib import Path
from app.utils.figure_artifacts import normalize_artifact_path
from app.artifacts.document_validation import FinalizationValidationError


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
        raw = normalize_artifact_path(_strip_title(m.group(2)))
        return m.group(0) if raw in allowed else ""

    return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", _replace, markdown or "")


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
    refs = _extract_markdown_image_targets(markdown)
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
