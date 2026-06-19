import asyncio
import json
from pathlib import Path

import pytest


class RecordingStage:
    def __init__(
        self,
        name: str,
        calls: list[tuple[str, str | None]],
        payload: dict | None = None,
    ) -> None:
        self.name = name
        self.calls = calls
        self.payload = payload or {}

    async def run(
        self,
        *,
        task_id: str,
        task: dict,
        input_path: Path | None,
        output_path: Path,
    ) -> Path:
        self.calls.append((self.name, input_path.name if input_path else None))
        output_path.write_text(json.dumps(self.payload), encoding="utf-8")
        return output_path


def test_guidance_pipeline_runs_the_six_business_stages_in_strict_order(tmp_path: Path) -> None:
    from app.guidance.pipeline import GuidancePipeline

    calls: list[tuple[str, str | None]] = []
    pipeline = GuidancePipeline(
        decomposition_stage=RecordingStage("decomposition", calls),
        modeling_stage=RecordingStage("modeling", calls),
        model_review_stage=RecordingStage(
            "model_review",
            calls,
            {"review_status": "approved", "model_id": "test-model"},
        ),
        calculation_stage=RecordingStage("calculation", calls),
        result_verification_stage=RecordingStage("result_verification", calls),
        guidance_stage=RecordingStage("guidance", calls),
    )
    task = {
        "task_id": "task-six-stages",
        "question": "Analyze the uploaded competition problem.",
        "work_dir": str(tmp_path),
        "workflow_mode": "standard",
    }

    async def collect():
        return [payload async for payload in pipeline.run("task-six-stages", task)]

    asyncio.run(collect())

    assert calls == [
        ("decomposition", None),
        ("modeling", "problem_spec.json"),
        ("model_review", "model_spec.json"),
        ("calculation", "approved_model_spec.json"),
        ("result_verification", "execution_manifest.json"),
        ("guidance", "verification_report.json"),
    ]


def test_guidance_pipeline_never_runs_calculation_for_unapproved_model(tmp_path: Path) -> None:
    from app.guidance.execution import UnapprovedModelError
    from app.guidance.pipeline import GuidancePipeline

    calls: list[tuple[str, str | None]] = []
    pipeline = GuidancePipeline(
        decomposition_stage=RecordingStage("decomposition", calls),
        modeling_stage=RecordingStage("modeling", calls),
        model_review_stage=RecordingStage(
            "model_review",
            calls,
            {"review_status": "revision_required", "model_id": "rejected-model"},
        ),
        calculation_stage=RecordingStage("calculation", calls),
        result_verification_stage=RecordingStage("result_verification", calls),
        guidance_stage=RecordingStage("guidance", calls),
    )
    task = {
        "task_id": "task-unapproved",
        "question": "Analyze the uploaded competition problem.",
        "work_dir": str(tmp_path),
    }

    async def collect():
        return [payload async for payload in pipeline.run("task-unapproved", task)]

    with pytest.raises(UnapprovedModelError):
        asyncio.run(collect())

    assert [name for name, _ in calls] == ["decomposition", "modeling", "model_review"]


def test_default_guidance_pipeline_uses_only_the_six_new_business_stages() -> None:
    from app.guidance.workflow import build_default_guidance_pipeline

    pipeline = build_default_guidance_pipeline()

    assert list(pipeline._stages) == [
        "decomposition",
        "modeling",
        "model_review",
        "calculation",
        "result_verification",
        "guidance",
    ]
    source = (Path(__file__).parents[1] / "app" / "guidance" / "pipeline.py").read_text(encoding="utf-8")
    assert "LLMGuidancePlanner" not in source
    assert "run_solve_spec" not in source


def test_guidance_package_does_not_depend_on_legacy_workflow() -> None:
    guidance_root = Path(__file__).parents[1] / "app" / "guidance"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in guidance_root.glob("*.py")
    )

    assert "app.core.workflow" not in source
    assert "_writing_workflow" not in source
