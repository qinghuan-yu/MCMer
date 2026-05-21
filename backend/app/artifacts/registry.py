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
from app.artifacts.protocols import canonical_caption_for_role, normalize_figure_role
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


@dataclass
class FigureRequestStatus:
    """Semantic request satisfaction status for one required figure request."""

    figure_request_id: str
    required: bool
    satisfied: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "figure_request_id": self.figure_request_id,
            "required": self.required,
            "satisfied": self.satisfied,
            "reason": self.reason,
        }


@dataclass
class FigureBundle:
    """Writer-facing structured figure bundle.

    - available_figures: semantic-verified figures satisfying required requests
    - diagnostic_figures: valid artifacts that are intentionally excluded
    """

    required_requests: list[dict[str, Any]] = field(default_factory=list)
    satisfied_requests: list[dict[str, Any]] = field(default_factory=list)
    missing_requests: list[dict[str, Any]] = field(default_factory=list)
    available_figures: list[dict[str, Any]] = field(default_factory=list)
    recommended_requests: list[dict[str, Any]] = field(default_factory=list)
    blocked_requests: list[dict[str, Any]] = field(default_factory=list)
    result_figures: list[dict[str, Any]] = field(default_factory=list)
    explanatory_figures: list[dict[str, Any]] = field(default_factory=list)
    diagnostic_figures: list[dict[str, Any]] = field(default_factory=list)
    required_images: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "required_requests": self.required_requests,
            "satisfied_requests": self.satisfied_requests,
            "missing_requests": self.missing_requests,
            "available_figures": self.available_figures,
            "recommended_requests": self.recommended_requests,
            "blocked_requests": self.blocked_requests,
            "result_figures": self.result_figures,
            "explanatory_figures": self.explanatory_figures,
            "diagnostic_figures": self.diagnostic_figures,
            "required_images": self.required_images,
        }


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
    artifact_valid_images: list[str] = field(default_factory=list)
    # Backward compatibility: historical name used by older code paths.
    paper_ready_images: list[str] = field(default_factory=list)
    figure_rejections: list[FigureRejection] = field(default_factory=list)
    verified_result_ids: set[str] = field(default_factory=set)
    figure_bundle: FigureBundle = field(default_factory=FigureBundle)

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
        self.artifact_valid_images = []
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
                self.artifact_valid_images.append(path)
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

        # Keep deprecated alias in sync for compatibility.
        self.paper_ready_images = list(self.artifact_valid_images)
        return self.artifact_valid_images

    def _load_figure_requests(self) -> list[dict[str, Any]]:
        """Load figure_requests from figure_plan.json first, then solve_spec.json."""
        figure_plan_path = Path(self.work_dir) / "figure_plan.json"
        if figure_plan_path.exists():
            try:
                payload = json.loads(figure_plan_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    requests = payload.get("figure_requests", [])
                    if isinstance(requests, list):
                        planned = [item for item in requests if isinstance(item, dict)]
                        if planned:
                            return planned
            except Exception:
                pass

        solve_spec_path = Path(self.work_dir) / "solve_spec.json"
        if not solve_spec_path.exists():
            return []
        try:
            payload = json.loads(solve_spec_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(payload, dict):
            return []
        requests = payload.get("figure_requests", [])
        if not isinstance(requests, list):
            return []
        return [item for item in requests if isinstance(item, dict)]

    def _artifact_by_path(self) -> dict[str, dict[str, Any]]:
        """Build a path->artifact index from manifest and structured outputs."""
        root = Path(self.work_dir)
        indexed: dict[str, dict[str, Any]] = {}

        def _add(item: object) -> None:
            if not isinstance(item, dict):
                return
            path = normalize_artifact_path(item.get("path") or item.get("filename") or "")
            if not path:
                return
            indexed[path] = dict(item)

        for item in self.figure_manifest:
            _add(item)

        for filename in self.structured_result_files:
            payload = load_json(str(root / filename))
            if not isinstance(payload, dict):
                continue
            for item in payload.get("generated_files", []) or []:
                _add(item)

        return indexed

    def build_figure_bundle(self) -> FigureBundle:
        """Build writer-facing FigureBundle with semantic request checks.

        Semantic admission criteria for required requests:
        - artifact already passed validate_for_paper() (artifact gate)
        - figure_request_id exists and is required
        - semantic_verified is True
        """
        if not self.artifact_valid_images:
            self.validate_for_paper()

        artifacts = self._artifact_by_path()
        requests = self._load_figure_requests()

        required_requests = [
            item for item in requests
            if bool(item.get("required", True))
        ]
        recommended_requests = [
            item for item in requests
            if not bool(item.get("required", True)) and str(item.get("status") or "") == "recommended"
        ]
        blocked_requests = [
            item for item in requests
            if str(item.get("status") or "") == "blocked" or str(item.get("blocked_reason") or "").strip()
        ]
        required_ids = {
            str(item.get("id") or "").strip()
            for item in required_requests
            if str(item.get("id") or "").strip()
        }
        request_by_id = {
            str(item.get("id") or "").strip(): item
            for item in requests
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }

        available: list[dict[str, Any]] = []
        result_figures: list[dict[str, Any]] = []
        explanatory_figures: list[dict[str, Any]] = []
        diagnostics: list[dict[str, Any]] = []
        matched_request_ids: set[str] = set()
        required_images: list[str] = []

        explanatory_roles = {
            "geometry_schematic",
            "timeline_schematic",
            "trajectory_overview",
            "optimization_variable_diagram",
            "algorithm_flowchart",
        }
        forbidden_explanatory_roles = {
            "workflow",
            "agent_workflow",
            "verified_result_summary",
            "diagnostics",
            "system_architecture",
        }

        for path in self.artifact_valid_images:
            artifact = artifacts.get(path, {})
            figure_role = normalize_figure_role(artifact.get("figure_role") or "")
            figure_request_id = str(artifact.get("figure_request_id") or "").strip()
            semantic_role = normalize_figure_role(artifact.get("semantic_role") or figure_role)
            semantic_verified = bool(artifact.get("semantic_verified"))
            paper_section = str(artifact.get("paper_section") or "").strip().lower()
            figure_kind = str(artifact.get("figure_kind") or "").strip().lower()

            req_payload = request_by_id.get(figure_request_id, {})
            req_kind = str(req_payload.get("figure_kind") or "").strip().lower()
            effective_kind = figure_kind or req_kind or ("explanatory_figure" if semantic_role in explanatory_roles else "result_figure")
            canonical_caption = (
                str(req_payload.get("canonical_caption") or "").strip()
                or str(artifact.get("canonical_caption") or "").strip()
                or canonical_caption_for_role(semantic_role, self.contract.chart_language if self.contract else "Simplified Chinese")
            )

            item = {
                "path": path,
                "caption": canonical_caption or str(artifact.get("caption") or artifact.get("figure_caption") or semantic_role or path),
                "canonical_caption": canonical_caption,
                "figure_request_id": figure_request_id,
                "semantic_role": semantic_role,
                "semantic_verified": semantic_verified,
                "figure_kind": effective_kind,
                "view_type": str(artifact.get("view_type") or req_payload.get("view_type") or "").strip(),
                "linked_result_ids": [
                    str(value).strip()
                    for value in (artifact.get("linked_result_ids") or req_payload.get("linked_result_ids") or [])
                    if str(value).strip()
                ],
                "source_data": [
                    str(value).strip()
                    for value in (artifact.get("source_data") or artifact.get("data_bindings") or req_payload.get("required_data") or [])
                    if str(value).strip()
                ],
                "chart_language_verified": bool(artifact.get("chart_language_verified", True)),
                "paper_ready": bool(artifact.get("paper_ready", True)),
            }

            is_diagnostic = (
                figure_role == "verified_result_summary"
                or paper_section == "diagnostics"
                or not semantic_verified
                or semantic_role in forbidden_explanatory_roles
            )
            if is_diagnostic:
                diagnostics.append(item)
                continue

            if required_ids and figure_request_id in required_ids:
                available.append(item)
                matched_request_ids.add(figure_request_id)
                if effective_kind == "explanatory_figure":
                    explanatory_figures.append(item)
                else:
                    result_figures.append(item)

                if bool(req_payload.get("must_include_in_paper")):
                    required_images.append(path)
            elif not required_ids:
                # Backward-compatible mode: no explicit requests configured.
                available.append(item)
                if effective_kind == "explanatory_figure":
                    explanatory_figures.append(item)
                else:
                    result_figures.append(item)

        satisfied: list[dict[str, Any]] = []
        missing: list[dict[str, Any]] = []
        for req in required_requests:
            req_id = str(req.get("id") or "").strip()
            if not req_id:
                continue
            if req_id in matched_request_ids:
                satisfied.append({"figure_request_id": req_id, "required": True, "satisfied": True})
            else:
                missing.append({
                    "figure_request_id": req_id,
                    "required": True,
                    "satisfied": False,
                    "reason": str(req.get("blocked_reason") or "").strip() or "no semantic_verified artifact bound to this request",
                })

        self.figure_bundle = FigureBundle(
            required_requests=required_requests,
            satisfied_requests=satisfied,
            missing_requests=missing,
            available_figures=available,
            recommended_requests=recommended_requests,
            blocked_requests=blocked_requests,
            result_figures=result_figures,
            explanatory_figures=explanatory_figures,
            diagnostic_figures=diagnostics,
            required_images=list(dict.fromkeys(required_images)),
        )
        return self.figure_bundle

    # ── Queries ────────────────────────────────────────────────────────

    def has_verified_results(self) -> bool:
        return len(self.verified_result_ids) > 0

    def verified_count(self) -> int:
        return len(self.verified_result_ids)

    def blocked_count(self) -> int:
        summary = self.result_registry.get("summary", {})
        return summary.get("blocked_count", 0) if isinstance(summary, dict) else 0

    def paper_ready_count(self) -> int:
        return len(self.artifact_valid_images)

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
