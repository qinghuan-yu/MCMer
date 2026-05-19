"""ArtifactRegistry — unified artifact management for MCMer.

Loads all artifacts from a work directory, validates them against the
ProblemContract, and provides the single source of truth for paper-ready
figures, verified results, and document artifacts.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.artifacts.contracts import ProblemContract
from app.utils.figure_artifacts import (
    FIGURE_MANIFEST,
    validate_figure,
    load_figure_manifest,
    normalize_artifact_path,
    is_image_path,
)
from app.utils.common_utils import build_result_registry, load_json

logger = logging.getLogger(__name__)


# ── Gate rejection reasons ─────────────────────────────────────────────

@dataclass
class FigureRejection:
    """Structured reason why a figure was rejected from the paper."""
    path: str
    rejected_by: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "rejected_by": self.rejected_by}


# ── ArtifactRegistry ───────────────────────────────────────────────────

@dataclass
class ArtifactRegistry:
    """Central registry of all artifacts for a task.

    After loading, call ``validate_for_paper()`` to get the list of
    paper-ready figure paths and any rejection reports.
    """

    work_dir: str
    contract: ProblemContract | None = None

    # Populated by load()
    figure_manifest: list[dict[str, Any]] = field(default_factory=list)
    structured_result_files: list[str] = field(default_factory=list)
    result_registry: dict[str, Any] = field(default_factory=dict)

    # Populated by validate_for_paper()
    paper_ready_images: list[str] = field(default_factory=list)
    figure_rejections: list[FigureRejection] = field(default_factory=list)
    verified_result_ids: set[str] = field(default_factory=set)

    # ── Loading ────────────────────────────────────────────────────────

    @classmethod
    def load(
        cls,
        work_dir: str,
        structured_result_files: list[str] | None = None,
        contract: ProblemContract | None = None,
    ) -> ArtifactRegistry:
        """Load all artifacts from the work directory."""
        root = Path(work_dir)

        # Load figure manifest
        manifest = load_figure_manifest(work_dir)

        # Discover structured result files if not provided
        if structured_result_files is None:
            structured_result_files = []
            for f in root.glob("*_structured_results.json"):
                structured_result_files.append(f.name)
            for f in root.glob("*_structured_result.json"):
                if f.name not in structured_result_files:
                    structured_result_files.append(f.name)

        # Build result registry
        registry = build_result_registry(work_dir, structured_result_files)

        # Extract verified result IDs
        verified_ids = set()
        for entry in registry.get("verified_results", []) or []:
            if isinstance(entry, dict) and entry.get("id"):
                verified_ids.add(str(entry["id"]))

        return cls(
            work_dir=work_dir,
            contract=contract,
            figure_manifest=manifest,
            structured_result_files=structured_result_files,
            result_registry=registry,
            verified_result_ids=verified_ids,
        )

    # ── Validation ─────────────────────────────────────────────────────

    def validate_for_paper(self) -> list[str]:
        """Validate all figures using the unified ``validate_figure()`` validator.

        Delegates to ``figure_artifacts.validate_figure()`` — the SINGLE source
        of truth for figure validation — and collects structured rejection reasons.
        """
        self.paper_ready_images = []
        self.figure_rejections = []
        seen: set[str] = set()
        root = Path(self.work_dir)
        document_language = self.contract.chart_language if self.contract else "Simplified Chinese"

        def _check(item: dict[str, Any], source: str) -> None:
            path = normalize_artifact_path(item.get("path") or item.get("filename") or "")
            if not path or path in seen:
                return
            seen.add(path)

            passed, reasons = validate_figure(
                item, document_language, root, self.verified_result_ids,
            )
            if passed:
                self.paper_ready_images.append(path)
            else:
                self.figure_rejections.append(FigureRejection(path=path, rejected_by=reasons))

        def _reject_path(path_like: str, reason: str) -> None:
            path = normalize_artifact_path(path_like)
            if not path or path in seen:
                return
            seen.add(path)
            self.figure_rejections.append(FigureRejection(path=path, rejected_by=[reason]))

        # Check manifest entries
        for item in self.figure_manifest:
            _check(item, FIGURE_MANIFEST)

        # Check structured result files
        for filename in self.structured_result_files:
            payload = load_json(str(root / filename))
            if not isinstance(payload, dict):
                continue
            for item in payload.get("generated_files", []) or []:
                if isinstance(item, dict):
                    _check(item, filename)
                elif isinstance(item, str) and is_image_path(item):
                    _reject_path(item, "generated_file_not_object")

        return self.paper_ready_images

    # ── Queries ────────────────────────────────────────────────────────

    def has_verified_results(self) -> bool:
        return len(self.verified_result_ids) > 0

    def verified_count(self) -> int:
        return len(self.verified_result_ids)

    def blocked_count(self) -> int:
        summary = self.result_registry.get("summary", {})
        return summary.get("blocked_count", 0) if isinstance(summary, dict) else 0

    def paper_ready_count(self) -> int:
        return len(self.paper_ready_images)

    def rejection_summary(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self.figure_rejections]

    # ── Quarantine ─────────────────────────────────────────────────────

    def quarantine_unregistered_images(self) -> list[str]:
        """Move unregistered exploratory images to debug_artifacts/.

        Returns list of moved relative paths.
        """
        import shutil
        root = Path(self.work_dir)
        if not root.exists():
            return []

        registered = set()
        # From manifest
        for item in self.figure_manifest:
            p = normalize_artifact_path(item.get("path") or "")
            if p:
                registered.add(p)
        # From structured results
        for filename in self.structured_result_files:
            payload = load_json(str(root / filename))
            if not isinstance(payload, dict):
                continue
            for item in payload.get("generated_files", []) or []:
                if isinstance(item, dict):
                    p = normalize_artifact_path(item.get("path") or "")
                    if p:
                        registered.add(p)

        debug_dir = root / "debug_artifacts"
        moved: list[str] = []
        candidates: list[Path] = []

        for candidate in root.iterdir():
            if candidate.is_file() and is_image_path(candidate.name):
                candidates.append(candidate)
        for dirname in ("output", "results"):
            folder = root / dirname
            if folder.exists():
                candidates.extend(
                    p for p in folder.rglob("*") if p.is_file() and is_image_path(p.name)
                )

        for image_path in sorted(candidates):
            rel = image_path.relative_to(root).as_posix()
            if rel in registered or rel.startswith("debug_artifacts/"):
                continue
            debug_dir.mkdir(parents=True, exist_ok=True)
            target = debug_dir / image_path.name
            if target.exists():
                target = debug_dir / f"{image_path.stem}_{len(moved) + 1}{image_path.suffix}"
            shutil.move(str(image_path), str(target))
            moved.append(rel)

        if moved:
            logger.info("Quarantined unregistered images: %s", moved)
        return moved
