"""Finalizer stage facade for all document exports."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.artifacts.diagnostics import build_verified_output_bundle, save_verified_output_bundle
from app.artifacts.exporters import DocumentFinalizer


@dataclass(frozen=True)
class FinalizedOutput:
    paper_path: str
    docx_path: str
    notebook_path: str


@dataclass(frozen=True)
class FailureOutput:
    paper_path: str
    docx_path: str


class FinalizerStage:
    """Thin facade around the unified DocumentFinalizer outlet."""

    @staticmethod
    async def finalize_success(
        *,
        work_dir: str | Path,
        task_id: str,
        task_type: str,
        paper_content: str,
        allowed_images: list[str] | None,
        required_images: list[str] | None,
        result_payload: dict[str, Any],
        code_interpreter=None,
    ) -> FinalizedOutput:
        paper_path, docx_path, notebook_path = await DocumentFinalizer.finalize_async(
            work_dir=str(work_dir),
            task_id=task_id,
            task_type=task_type,
            paper_content=paper_content,
            allowed_images=allowed_images,
            required_images=required_images,
            result_payload=result_payload,
            code_interpreter=code_interpreter,
        )
        bundle = build_verified_output_bundle(
            status="passed",
            work_dir=work_dir,
            task_id=task_id,
            task_type=task_type,
            paper_path=paper_path,
            docx_path=docx_path,
            notebook_path=notebook_path,
            result_payload=result_payload,
            allowed_images=allowed_images,
            required_images=required_images,
        )
        save_verified_output_bundle(work_dir, bundle)
        return FinalizedOutput(
            paper_path=paper_path,
            docx_path=docx_path,
            notebook_path=notebook_path,
        )

    @staticmethod
    def export_quality_failure(
        *,
        work_dir: str | Path,
        question: str,
        result_registry: dict[str, Any],
        gate_reason: str,
        stage_outputs: dict[str, Any],
    ) -> FailureOutput:
        paper_path, docx_path = DocumentFinalizer.export_failure_report(
            work_dir=str(work_dir),
            question=question,
            result_registry=result_registry,
            gate_reason=gate_reason,
            stage_outputs=stage_outputs,
        )
        bundle = build_verified_output_bundle(
            status="failed",
            work_dir=work_dir,
            task_type="writing",
            paper_path=paper_path,
            docx_path=docx_path,
            result_registry=result_registry,
            gate_reason=gate_reason,
            result_payload={"stages": stage_outputs},
        )
        save_verified_output_bundle(work_dir, bundle)
        return FailureOutput(paper_path=paper_path, docx_path=docx_path)
