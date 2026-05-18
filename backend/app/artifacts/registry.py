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
    is_verified_paper_figure,
    load_figure_manifest,
    normalize_artifact_path,
    is_image_path,
    is_placeholder_filename,
    contains_placeholder_text,
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
        """Validate all figures and return the list of paper-ready image paths.

        Also populates ``self.figure_rejections`` with structured rejection
        reasons for every rejected figure.
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

            rejection = FigureRejection(path=path)

            # Run all validation checks and collect reasons
            if not is_image_path(path):
                rejection.rejected_by.append("not_an_image")
            elif not path.startswith(("output/", "figures/", "final_results/")):
                rejection.rejected_by.append("path_not_in_paper_dir")
            elif path.startswith("debug_artifacts/"):
                rejection.rejected_by.append("path_under_debug_artifacts")
            else:
                if item.get("paper_ready") is not True:
                    rejection.rejected_by.append("paper_ready_not_true")
                if item.get("chart_language_verified") is not True:
                    rejection.rejected_by.append("chart_language_not_verified")
                if item.get("created_by") != "save_paper_figure":
                    rejection.rejected_by.append("missing_created_by")
                hv = item.get("helper_version")
                if not isinstance(hv, (int, float)) or hv < 3:
                    rejection.rejected_by.append("missing_or_old_helper_version")
                if item.get("is_placeholder") is True:
                    rejection.rejected_by.append("is_placeholder_true")
                if is_placeholder_filename(path):
                    rejection.rejected_by.append("placeholder_filename")

                # Audit text checks
                audit_texts = item.get("visible_text_audit") or []
                if isinstance(audit_texts, list):
                    combined = " ".join(str(t) for t in audit_texts)
                    if contains_placeholder_text(combined):
                        rejection.rejected_by.append("placeholder_text_in_audit")
                    if not audit_texts:
                        rejection.rejected_by.append("empty_visible_text_audit")
                else:
                    rejection.rejected_by.append("visible_text_audit_not_list")

                # Language check
                lang = str(
                    item.get("visible_text_language")
                    or item.get("chart_language")
                    or ""
                ).strip().lower()
                from app.utils.figure_artifacts import expected_language_aliases
                if lang and lang not in expected_language_aliases(document_language):
                    rejection.rejected_by.append("wrong_visible_text_language")

                # Semantic binding checks
                if self.verified_result_ids:
                    linked = item.get("linked_result_ids")
                    if not isinstance(linked, list) or not linked:
                        rejection.rejected_by.append("missing_linked_result_ids")
                    else:
                        for rid in linked:
                            if str(rid) not in self.verified_result_ids:
                                rejection.rejected_by.append(f"linked_result_id_not_verified:{rid}")
                    source = item.get("source_data")
                    if not isinstance(source, list) or not source:
                        rejection.rejected_by.append("missing_source_data")

                # File existence check
                if not (root / path).exists():
                    rejection.rejected_by.append("file_not_on_disk")

            if rejection.rejected_by:
                self.figure_rejections.append(rejection)
            else:
                self.paper_ready_images.append(path)

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
