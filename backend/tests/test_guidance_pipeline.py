import asyncio
import json
from pathlib import Path


class RecordingStage:
    def __init__(self, name: str, calls: list[tuple[str, str | None]]) -> None:
        self.name = name
        self.calls = calls

    async def run(
        self,
        *,
        task_id: str,
        task: dict,
        input_path: Path | None,
        output_path: Path,
    ) -> Path:
        self.calls.append((self.name, input_path.name if input_path else None))
        output_path.write_text("{}", encoding="utf-8")
        return output_path


class FakePlanner:
    async def plan(self, *, question: str, data_files: list[str], workflow_mode: str):
        return {
            "spec_id": "traffic-guidance",
            "subproblem_id": "problem_1",
            "problem_summary": "Estimate traffic flow from registered inputs.",
            "method_guidance": "Use a deterministic conservation equation.",
            "assumptions": ["The registered observations are representative."],
            "steps": ["Register source parameters.", "Evaluate the conservation equation."],
            "parameters": [
                {
                    "symbol": "q_main",
                    "name": "Main-road flow",
                    "value": 120,
                    "unit": "vehicles/min",
                    "source_type": "uploaded_data",
                    "source_ref": "traffic.csv",
                },
                {
                    "symbol": "q_branch",
                    "name": "Branch flow",
                    "value": 30,
                    "unit": "vehicles/min",
                    "source_type": "uploaded_data",
                    "source_ref": "traffic.csv",
                },
                {
                    "symbol": "q_total",
                    "name": "Total flow",
                    "formula": "q_main + q_branch",
                    "unit": "vehicles/min",
                },
            ],
            "results": [
                {
                    "id": "result_total_flow",
                    "claim_text": "The registered total flow is 150 vehicles/min.",
                    "parameters": ["q_main", "q_branch", "q_total"],
                    "source_data": ["traffic.csv"],
                }
            ],
        }


class UnresolvedFitPlanner:
    async def plan(self, *, question: str, data_files: list[str], workflow_mode: str):
        return {
            "spec_id": "unresolved-fit",
            "subproblem_id": "problem_1",
            "problem_summary": "A piecewise model requires fitted coefficients.",
            "method_guidance": "Fit coefficients before evaluating the continuity condition.",
            "assumptions": [],
            "steps": ["Estimate the missing coefficients from source observations."],
            "parameters": [
                {"symbol": "b21", "source_type": "estimated", "source_ref": "least squares"},
                {"symbol": "a21", "source_type": "estimated", "source_ref": "least squares"},
                {"symbol": "a22", "source_type": "estimated", "source_ref": "least squares"},
                {"symbol": "t_c", "source_type": "estimated", "source_ref": "least squares"},
                {"symbol": "b22", "formula": "b21 + (a21 - a22) * t_c"},
            ],
            "results": [],
        }


def test_guidance_pipeline_runs_the_six_business_stages_in_strict_order(tmp_path: Path) -> None:
    from app.guidance.pipeline import GuidancePipeline

    calls: list[tuple[str, str | None]] = []
    pipeline = GuidancePipeline(
        decomposition_stage=RecordingStage("decomposition", calls),
        modeling_stage=RecordingStage("modeling", calls),
        model_review_stage=RecordingStage("model_review", calls),
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


def test_guidance_pipeline_writes_auditable_markdown_without_docx(tmp_path: Path) -> None:
    from app.guidance.pipeline import GuidancePipeline

    task = {
        "task_id": "task-1",
        "question": "Estimate traffic flow.",
        "work_dir": str(tmp_path),
        "workflow_mode": "standard",
    }

    async def collect():
        pipeline = GuidancePipeline(planner=FakePlanner())
        return [payload async for payload in pipeline.run("task-1", task)]

    messages = asyncio.run(collect())

    guidance = tmp_path / "guidance.md"
    assert guidance.is_file()
    assert (tmp_path / "solve_spec.json").is_file()
    assert (tmp_path / "result_registry.json").is_file()
    assert (tmp_path / "parameter_registry.json").is_file()
    assert (tmp_path / "guidance_audit_report.json").is_file()
    assert not (tmp_path / "guidance.docx").exists()
    assert "param_q_total" in guidance.read_text(encoding="utf-8")
    audit = json.loads((tmp_path / "guidance_audit_report.json").read_text(encoding="utf-8"))
    assert audit["status"] == "PASS"
    assert messages[-1]["data"]["primary_artifact_type"] == "guidance"
    assert messages[-1]["data"]["docx_path"] == ""


def test_guidance_package_does_not_depend_on_legacy_workflow() -> None:
    guidance_root = Path(__file__).parents[1] / "app" / "guidance"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in guidance_root.glob("*.py")
    )

    assert "app.core.workflow" not in source
    assert "_writing_workflow" not in source


def test_guidance_pipeline_completes_with_blocked_disclosure_for_unresolved_formula(tmp_path: Path) -> None:
    from app.guidance.pipeline import GuidancePipeline

    task = {
        "task_id": "task-blocked",
        "question": "Fit a piecewise model.",
        "work_dir": str(tmp_path),
        "workflow_mode": "standard",
    }

    async def collect():
        return [payload async for payload in GuidancePipeline(planner=UnresolvedFitPlanner()).run("task-blocked", task)]

    messages = asyncio.run(collect())
    result_registry = json.loads((tmp_path / "result_registry.json").read_text(encoding="utf-8"))

    assert messages[-1]["data"]["status"] == "completed"
    assert (tmp_path / "guidance.md").is_file()
    assert result_registry["summary"]["blocked_count"] == 5
    assert "阻断" in (tmp_path / "guidance.md").read_text(encoding="utf-8")
