"""Artifact evidence helpers shared by workflow and tests.

These functions keep paper-ready figure validation and image evidence scanning
out of ``workflow.py`` so the orchestration layer can keep shrinking.
"""

from __future__ import annotations

from pathlib import Path

from app.utils.common_utils import load_json
from app.utils.figure_artifacts import (
    FIGURE_MANIFEST,
    figure_artifact_path,
    is_image_path,
    is_verified_paper_figure,
    load_figure_manifest,
    normalize_artifact_path,
)


def generated_file_path(item: object) -> str:
    return figure_artifact_path(item)


def language_verified_generated_images(
    work_dir: str,
    structured_result_files: list[str],
    document_language: str,
    result_registry: dict[str, object] | None = None,
) -> list[str]:
    """Return image paths that pass the full FigureArtifact gate."""
    allowed: list[str] = []
    seen: set[str] = set()

    verified_ids: set[str] | None = None
    if result_registry is not None:
        verified_ids = {
            str(entry["id"])
            for entry in result_registry.get("verified_results", []) or []
            if isinstance(entry, dict) and entry.get("id")
        }

    def maybe_add(item: object) -> None:
        path = generated_file_path(item)
        if not path or path in seen:
            return
        if not is_verified_paper_figure(item, document_language, work_dir, verified_ids):
            return
        allowed.append(path)
        seen.add(path)

    for manifest_item in load_figure_manifest(work_dir):
        maybe_add(manifest_item)

    root = Path(work_dir)
    for filename in structured_result_files:
        payload = load_json(str(root / filename))
        if not isinstance(payload, dict):
            continue
        generated_files = payload.get("generated_files", [])
        if not isinstance(generated_files, list):
            continue
        for item in generated_files:
            maybe_add(item)
    return allowed


def generated_image_evidence_paths(
    work_dir: str,
    structured_result_files: list[str],
    created_images: list[str] | None = None,
) -> list[str]:
    evidence: list[str] = []
    seen: set[str] = set()

    def add(path: str) -> None:
        normalized = normalize_artifact_path(path)
        if not normalized or normalized in seen or not is_image_path(normalized):
            return
        evidence.append(normalized)
        seen.add(normalized)

    for path in created_images or []:
        add(path)

    manifest = load_figure_manifest(work_dir)
    if manifest:
        for item in manifest:
            add(generated_file_path(item))

    root = Path(work_dir)
    for filename in structured_result_files:
        payload = load_json(str(root / filename))
        if not isinstance(payload, dict):
            continue
        generated_files = payload.get("generated_files", [])
        if isinstance(generated_files, list):
            for item in generated_files:
                add(generated_file_path(item))
        artifact_evidence = payload.get("artifact_evidence", [])
        if isinstance(artifact_evidence, list):
            for item in artifact_evidence:
                add(generated_file_path(item))

    debug_dir = root / "debug_artifacts"
    if debug_dir.exists():
        for path in sorted(debug_dir.rglob("*")):
            if path.is_file() and is_image_path(path.name):
                add(path.relative_to(root).as_posix())

    return evidence


def has_generated_image_evidence(
    work_dir: str,
    structured_result_files: list[str],
    created_images: list[str] | None = None,
) -> bool:
    return bool(generated_image_evidence_paths(work_dir, structured_result_files, created_images))
