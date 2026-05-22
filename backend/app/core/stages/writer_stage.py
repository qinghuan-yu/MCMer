"""Writer stage deterministic plumbing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.artifacts.registry import FigureBundle
from app.core.skills.writer_context import WriterContextSkill


@dataclass(frozen=True)
class WriterContextSnapshot:
    """Saved writer context plus the only image whitelist writer may use."""

    payload: dict[str, Any]
    path: str
    allowed_images: list[str]


class WriterStage:
    """Small facade for WriterAgent inputs.

    WriterAgent is allowed to see paper figures only through
    ``writer_context.allowed_image_paths``.  This class is the migration point
    from legacy ``writer_images`` plumbing to the stage protocol.
    """

    @staticmethod
    def prepare_context(
        *,
        work_dir: str | Path,
        result_registry: dict[str, Any],
        figure_bundle: FigureBundle,
        chart_language: str,
    ) -> WriterContextSnapshot:
        writer_context = WriterContextSkill.build_context(
            result_registry=result_registry,
            figure_bundle=figure_bundle,
            chart_language=chart_language,
        )
        context_path = writer_context.save(work_dir)
        payload = writer_context.to_dict()
        allowed_images = [
            str(path).strip()
            for path in payload.get("allowed_image_paths", [])
            if str(path).strip()
        ]
        return WriterContextSnapshot(
            payload=payload,
            path=str(context_path),
            allowed_images=allowed_images,
        )
