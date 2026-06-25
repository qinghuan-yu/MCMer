import asyncio
import json
from pathlib import Path

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


class SequentialReviewStage:
    def __init__(self, calls: list[tuple[str, str | None]], statuses: list[str]) -> None:
        self.calls = calls
        self.statuses = iter(statuses)

    async def run(
        self,
        *,
        task_id: str,
        task: dict,
        input_path: Path | None,
        output_path: Path,
    ) -> Path:
        status = next(self.statuses)
        self.calls.append(("model_review", input_path.name if input_path else None))
        (output_path.parent / "model_review.json").write_text(
            json.dumps(
                {
                    "model_id": "revised-model",
                    "status": status,
                    "required_revisions": ["Add an explicit identifiability constraint."],
                }
            ),
            encoding="utf-8",
        )
        output_path.write_text(
            json.dumps({"review_status": status, "model_id": "revised-model"}),
            encoding="utf-8",
        )
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
    from app.guidance.pipeline import GuidancePipeline

    calls: list[tuple[str, str | None]] = []
    pipeline = GuidancePipeline(
        decomposition_stage=RecordingStage("decomposition", calls),
        modeling_stage=RecordingStage("modeling", calls),
        model_review_stage=RecordingStage(
            "model_review",
            calls,
            {"review_status": "blocked", "model_id": "rejected-model"},
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

    messages = asyncio.run(collect())

    assert [name for name, _ in calls] == [
        "decomposition",
        "modeling",
        "model_review",
        "guidance",
    ]
    assert messages[-1]["data"]["status"] == "completed"


def test_guidance_pipeline_returns_to_modeling_once_then_runs_approved_model(tmp_path: Path) -> None:
    from app.guidance.pipeline import GuidancePipeline

    calls: list[tuple[str, str | None]] = []
    pipeline = GuidancePipeline(
        decomposition_stage=RecordingStage("decomposition", calls),
        modeling_stage=RecordingStage("modeling", calls),
        model_review_stage=SequentialReviewStage(
            calls,
            ["revision_required", "approved"],
        ),
        calculation_stage=RecordingStage("calculation", calls),
        result_verification_stage=RecordingStage("result_verification", calls),
        guidance_stage=RecordingStage("guidance", calls),
    )
    task = {
        "task_id": "task-revision",
        "question": "Fit a conditionally identifiable model.",
        "work_dir": str(tmp_path),
    }

    async def collect():
        return [payload async for payload in pipeline.run("task-revision", task)]

    messages = asyncio.run(collect())

    assert calls == [
        ("decomposition", None),
        ("modeling", "problem_spec.json"),
        ("model_review", "model_spec.json"),
        ("modeling", "model_review.json"),
        ("model_review", "model_spec.json"),
        ("calculation", "approved_model_spec.json"),
        ("result_verification", "execution_manifest.json"),
        ("guidance", "verification_report.json"),
    ]
    assert messages[-1]["data"]["status"] == "completed"


def test_guidance_pipeline_writes_blocked_guidance_after_second_review_failure(tmp_path: Path) -> None:
    from app.guidance.pipeline import GuidancePipeline

    calls: list[tuple[str, str | None]] = []
    pipeline = GuidancePipeline(
        decomposition_stage=RecordingStage("decomposition", calls),
        modeling_stage=RecordingStage("modeling", calls),
        model_review_stage=SequentialReviewStage(
            calls,
            ["revision_required", "revision_required"],
        ),
        calculation_stage=RecordingStage("calculation", calls),
        result_verification_stage=RecordingStage("result_verification", calls),
        guidance_stage=RecordingStage("guidance", calls),
    )
    task = {
        "task_id": "task-blocked-after-revision",
        "question": "Fit an unidentifiable model.",
        "work_dir": str(tmp_path),
    }

    async def collect():
        return [
            payload
            async for payload in pipeline.run("task-blocked-after-revision", task)
        ]

    messages = asyncio.run(collect())

    assert calls == [
        ("decomposition", None),
        ("modeling", "problem_spec.json"),
        ("model_review", "model_spec.json"),
        ("modeling", "model_review.json"),
        ("model_review", "model_spec.json"),
        ("guidance", "approved_model_spec.json"),
    ]
    assert not (tmp_path / "solve.py").exists()
    assert not (tmp_path / "execution_manifest.json").exists()
    assert (tmp_path / "guidance.md").is_file()
    assert messages[-1]["data"]["status"] == "completed"


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


def test_default_guidance_pipeline_applies_standard_llm_timeout_budget() -> None:
    from app.guidance.workflow import build_default_guidance_pipeline

    pipeline = build_default_guidance_pipeline("standard")

    llms = [
        pipeline._stages["decomposition"].decomposer.llm,
        pipeline._stages["modeling"].modeler.llm,
        pipeline._stages["model_review"].reviewer.llm,
        pipeline._stages["calculation"].program_generator.llm,
    ]
    assert {llm.request_timeout for llm in llms} == {240}


def test_guidance_package_does_not_depend_on_legacy_workflow() -> None:
    guidance_root = Path(__file__).parents[1] / "app" / "guidance"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in guidance_root.glob("*.py")
    )

    assert "from app.core.workflow import" not in source
    assert "_writing_workflow" not in source
