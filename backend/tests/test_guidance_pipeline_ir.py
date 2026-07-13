import asyncio
import json
from pathlib import Path


def _blocked_ir(model_id: str = "pipeline-model") -> dict:
    return {
        "schema_version": "1.0",
        "model_id": model_id,
        "subproblems": [{
            "execution_status": "blocked",
            "subproblem_id": "problem1",
            "objective": "Compile the model.",
            "blocking_reason": "A required operator is unavailable.",
            "missing_inputs": ["registered operator"],
            "recovery_action": "Register and validate the operator.",
            "expected_outputs": ["compiled result"],
        }],
    }


def _approved_payload(status: str = "approved", model_id: str = "pipeline-model") -> dict:
    return {
        "schema_version": "1.0",
        "review_status": status,
        "model_id": model_id,
        "model_ir_ref": "model_ir.json",
        "review_ref": "model_review.json",
        "approved_model_ir": _blocked_ir(model_id),
    }


class RecordingStage:
    def __init__(self, name: str, calls: list[tuple[str, str | None]], payload: dict | None = None) -> None:
        self.name = name
        self.calls = calls
        self.payload = payload or {}

    async def run(self, *, task_id: str, task: dict, input_path: Path | None, output_path: Path) -> Path:
        self.calls.append((self.name, input_path.name if input_path else None))
        output_path.write_text(json.dumps(self.payload), encoding="utf-8")
        return output_path


class SequentialModelingStage:
    def __init__(self, calls: list[tuple[str, str | None]]) -> None:
        self.calls = calls

    async def run(self, *, task_id: str, task: dict, input_path: Path | None, output_path: Path) -> Path:
        self.calls.append(("modeling", input_path.name if input_path else None))
        output_path.write_text(json.dumps(_blocked_ir()), encoding="utf-8")
        return output_path


class SequentialReviewStage:
    def __init__(self, calls: list[tuple[str, str | None]]) -> None:
        self.calls = calls
        self.statuses = iter(["revision_required", "approved"])

    async def run(self, *, task_id: str, task: dict, input_path: Path | None, output_path: Path) -> Path:
        status = next(self.statuses)
        self.calls.append(("model_review", input_path.name if input_path else None))
        review = {
            "schema_version": "1.0",
            "model_id": "pipeline-model",
            "status": status,
            "model_ir_ref": "model_ir.json",
            "required_revisions": ["Declare the missing operator explicitly"] if status == "revision_required" else [],
        }
        (output_path.parent / "model_review.json").write_text(json.dumps(review), encoding="utf-8")
        output_path.write_text(json.dumps(_approved_payload(status)), encoding="utf-8")
        return output_path


def _run_pipeline(pipeline, task_id: str, work_dir: Path) -> list[dict]:
    async def collect() -> list[dict]:
        return [item async for item in pipeline.run(task_id, {
            "task_id": task_id,
            "question": "Analyze the problem.",
            "work_dir": str(work_dir),
            "workflow_mode": "standard",
        })]

    return asyncio.run(collect())


def test_guidance_pipeline_runs_the_six_business_stages_in_strict_order(tmp_path: Path) -> None:
    from app.guidance.pipeline import GuidancePipeline

    calls: list[tuple[str, str | None]] = []
    pipeline = GuidancePipeline(
        decomposition_stage=RecordingStage("decomposition", calls),
        modeling_stage=RecordingStage("modeling", calls, _blocked_ir()),
        model_review_stage=RecordingStage("model_review", calls, _approved_payload()),
        calculation_stage=RecordingStage("calculation", calls),
        result_verification_stage=RecordingStage("result_verification", calls),
        guidance_stage=RecordingStage("guidance", calls),
    )

    _run_pipeline(pipeline, "task-six-stages", tmp_path)

    assert calls == [
        ("decomposition", None),
        ("modeling", "problem_spec.json"),
        ("model_review", "model_ir.json"),
        ("calculation", "approved_model_ir.json"),
        ("result_verification", "execution_manifest.json"),
        ("guidance", "verification_report.json"),
    ]


def test_guidance_pipeline_allows_exactly_one_model_ir_revision(tmp_path: Path) -> None:
    from app.guidance.pipeline import GuidancePipeline

    calls: list[tuple[str, str | None]] = []
    pipeline = GuidancePipeline(
        decomposition_stage=RecordingStage("decomposition", calls),
        modeling_stage=SequentialModelingStage(calls),
        model_review_stage=SequentialReviewStage(calls),
        calculation_stage=RecordingStage("calculation", calls),
        result_verification_stage=RecordingStage("result_verification", calls),
        guidance_stage=RecordingStage("guidance", calls),
    )

    _run_pipeline(pipeline, "task-revision", tmp_path)

    assert calls == [
        ("decomposition", None),
        ("modeling", "problem_spec.json"),
        ("model_review", "model_ir.json"),
        ("modeling", "model_review.json"),
        ("model_review", "model_ir.json"),
        ("calculation", "approved_model_ir.json"),
        ("result_verification", "execution_manifest.json"),
        ("guidance", "verification_report.json"),
    ]


def test_modeling_failure_contracts_remain_inside_model_ir_boundary(tmp_path: Path) -> None:
    from app.guidance.pipeline import _write_modeling_failure_contracts

    (tmp_path / "problem_spec.json").write_text(json.dumps({
        "task_summary": "Estimate two requested outputs.",
        "subproblems": [
            {"id": "problem1", "objective": "Estimate output one."},
            {"id": "problem2", "objective": "Estimate output two."},
        ],
    }), encoding="utf-8")

    approved_path = _write_modeling_failure_contracts(
        tmp_path,
        task_id="task-ir-failure",
        input_path=tmp_path / "problem_spec.json",
        exc=ValueError("candidate payload is not valid Model IR"),
    )

    approved = json.loads(approved_path.read_text(encoding="utf-8"))
    assert approved_path.name == "approved_model_ir.json"
    assert [item["subproblem_id"] for item in approved["approved_model_ir"]["subproblems"]] == [
        "problem1",
        "problem2",
    ]
    assert all(item["execution_status"] == "blocked" for item in approved["approved_model_ir"]["subproblems"])
    assert not (tmp_path / "approved_model_spec.json").exists()
    assert "ModelSpec" not in (tmp_path / "model_review.json").read_text(encoding="utf-8")


def test_default_pipeline_uses_six_stages_timeout_budget_and_default_operators() -> None:
    from app.guidance.operator_catalog import DEFAULT_OPERATOR_IDS
    from app.guidance.workflow import build_default_guidance_pipeline

    pipeline = build_default_guidance_pipeline("standard")

    assert list(pipeline._stages) == [
        "decomposition",
        "modeling",
        "model_review",
        "calculation",
        "result_verification",
        "guidance",
    ]
    llms = [
        pipeline._stages["decomposition"].decomposer.llm,
        pipeline._stages["modeling"].modeler.llm,
        pipeline._stages["model_review"].reviewer.llm,
    ]
    assert {llm.request_timeout for llm in llms} == {120}
    calculation = pipeline._stages["calculation"]
    assert calculation.compiler.registry is calculation.executor.registry
    assert all(calculation.compiler.registry.get(item) is not None for item in DEFAULT_OPERATOR_IDS)


def test_pipeline_progress_describes_operator_graph_not_generated_program() -> None:
    source = (Path(__file__).parents[1] / "app" / "guidance" / "pipeline.py").read_text(encoding="utf-8")

    assert "完整求解程序" not in source
    assert "执行算子图" in source


def test_guidance_package_does_not_depend_on_legacy_workflow() -> None:
    guidance_root = Path(__file__).parents[1] / "app" / "guidance"
    source = "\n".join(path.read_text(encoding="utf-8") for path in guidance_root.glob("*.py"))

    assert "from app.core.workflow import" not in source
    assert "_writing_workflow" not in source
    assert "TemplateProgramGenerator" not in source
    assert "LocalProgramExecutor" not in source
