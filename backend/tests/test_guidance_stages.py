import asyncio
import json
from pathlib import Path


class FakeProgramGenerator:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, *, approved_model, task: dict, work_dir: Path):
        from app.guidance.contracts import GeneratedProgram

        self.calls += 1
        return GeneratedProgram(source_code="print('complete solver')")


class CountingInterpreter:
    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir
        self.execute_calls = 0

    async def execute_code(self, code: str):
        self.execute_calls += 1
        (self.work_dir / "solver_results.json").write_text(
            json.dumps({"verified_results": [{"id": "fit", "value": 2.5}]}),
            encoding="utf-8",
        )
        return "complete solver", False, ""

    async def save_notebook(self, filepath: str):
        Path(filepath).write_text("{}", encoding="utf-8")

    async def close(self):
        return None


class FakeDecomposer:
    async def decompose(self, *, question: str, data_files: list[str], task: dict):
        from app.guidance.contracts import ProblemSpec

        return ProblemSpec(
            task_summary=question,
            subproblems=[{"id": "problem_1", "objective": "fit branch flows"}],
            data_sources=[{"path": data_files[0]}],
        )


class FakeModeler:
    async def model(self, *, problem_spec, task: dict, work_dir: Path):
        from app.guidance.contracts import ModelSpec

        return ModelSpec(
            model_id="branch-flow-fit",
            selected_method="constrained piecewise least squares",
            candidate_methods=[{"name": "piecewise least squares", "selected": True}],
            assumptions=["The branch decomposition uses an explicit identifiability constraint."],
            equations=["q_main(t) = q_1(t) + q_2(t)"],
            parameters=[{"symbol": "t_break", "source": "estimated"}],
            solve_steps=["Read Table 1.", "Fit the constrained piecewise model."],
            required_outputs=[
                "solver_results.json",
                "parameter_registry.json",
                "result_registry.json",
                "figures/problem1_fit.png",
            ],
            figure_requests=[{"path": "figures/problem1_fit.png", "role": "fit"}],
        )


class FakeReviewer:
    async def review(self, *, model_spec, task: dict, work_dir: Path):
        from app.guidance.contracts import ModelReview

        return ModelReview(
            model_id=model_spec.model_id,
            status="approved",
            identifiability="conditional_on_explicit_constraint",
            dimensional_consistency="passed",
            data_sufficiency="passed",
            computational_feasibility="passed",
        )


def test_decomposition_stage_saves_problem_spec_with_uploaded_files(tmp_path: Path) -> None:
    from app.guidance.stages import DecompositionStage

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "attachment.xlsx").write_bytes(b"xlsx")
    output = asyncio.run(
        DecompositionStage(decomposer=FakeDecomposer()).run(
            task_id="task-fit",
            task={"work_dir": str(tmp_path), "question": "Fit branch flows."},
            input_path=None,
            output_path=tmp_path / "problem_spec.json",
        )
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["subproblems"][0]["id"] == "problem_1"
    assert payload["data_sources"] == [{"path": "data/attachment.xlsx"}]


def test_modeling_stage_consumes_saved_problem_spec_and_saves_model_spec(tmp_path: Path) -> None:
    from app.guidance.stages import ModelingStage

    problem_path = tmp_path / "problem_spec.json"
    problem_path.write_text(
        json.dumps({"task_summary": "Fit branch flows.", "subproblems": []}),
        encoding="utf-8",
    )
    output = asyncio.run(
        ModelingStage(modeler=FakeModeler()).run(
            task_id="task-fit",
            task={"work_dir": str(tmp_path)},
            input_path=problem_path,
            output_path=tmp_path / "model_spec.json",
        )
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["selected_method"] == "constrained piecewise least squares"
    assert payload["candidate_methods"]
    assert payload["equations"]
    assert payload["figure_requests"]


def test_model_review_stage_saves_review_and_approved_model_contracts(tmp_path: Path) -> None:
    from app.guidance.stages import ModelReviewStage

    model_spec = asyncio.run(
        FakeModeler().model(problem_spec=None, task={}, work_dir=tmp_path)
    )
    model_path = tmp_path / "model_spec.json"
    model_path.write_text(model_spec.model_dump_json(), encoding="utf-8")
    output = asyncio.run(
        ModelReviewStage(reviewer=FakeReviewer()).run(
            task_id="task-fit",
            task={"work_dir": str(tmp_path)},
            input_path=model_path,
            output_path=tmp_path / "approved_model_spec.json",
        )
    )

    approved = json.loads(output.read_text(encoding="utf-8"))
    review = json.loads((tmp_path / "model_review.json").read_text(encoding="utf-8"))
    assert approved["review_status"] == "approved"
    assert approved["approved_model"]["model_id"] == "branch-flow-fit"
    assert review["identifiability"] == "conditional_on_explicit_constraint"


def test_calculation_stage_generates_and_executes_one_complete_solver(tmp_path: Path) -> None:
    from app.guidance.execution import LocalProgramExecutor
    from app.guidance.stages import CalculationStage

    approved_path = tmp_path / "approved_model_spec.json"
    approved_path.write_text(
        json.dumps(
            {
                "review_status": "approved",
                "model_id": "branch-flow-fit",
                "required_outputs": ["solver_results.json"],
            }
        ),
        encoding="utf-8",
    )
    generator = FakeProgramGenerator()
    interpreter = CountingInterpreter(tmp_path)
    stage = CalculationStage(
        program_generator=generator,
        executor=LocalProgramExecutor(interpreter_factory=lambda work_dir: interpreter),
    )

    output_path = asyncio.run(
        stage.run(
            task_id="task-fit",
            task={"work_dir": str(tmp_path)},
            input_path=approved_path,
            output_path=tmp_path / "execution_manifest.json",
        )
    )

    assert generator.calls == 1
    assert interpreter.execute_calls == 1
    assert (tmp_path / "solve.py").read_text(encoding="utf-8") == "print('complete solver')"
    manifest = json.loads(output_path.read_text(encoding="utf-8"))
    assert manifest["execution_count"] == 1


def _write_verified_fit_artifacts(tmp_path: Path) -> Path:
    (tmp_path / "figures").mkdir(exist_ok=True)
    (tmp_path / "figures" / "problem1_fit.png").write_bytes(b"real-png")
    (tmp_path / "parameter_registry.json").write_text(
        json.dumps(
            {
                "parameters": [
                    {
                        "id": "param_t_break",
                        "symbol": "t_break",
                        "name": "breakpoint",
                        "value": 30,
                        "unit": "sample index",
                        "source_type": "estimated_from_data",
                        "source_ref": "data/attachment.xlsx",
                        "trust_status": "estimated",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "result_registry.json").write_text(
        json.dumps(
            {
                "verified_results": [
                    {
                        "id": "result_piecewise_fit",
                        "claim_text": "The piecewise fit reproduces the observed main-road flow.",
                        "metrics": {"rmse": 0.0, "max_abs_residual": 0.0},
                        "source_data": ["data/attachment.xlsx"],
                    }
                ],
                "blocked_results": [],
                "summary": {"coverage_status": "verified"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "solver_results.json").write_text(
        json.dumps(
            {
                "observed": [7.0, 8.5, 10.0],
                "recomposed": [7.0, 8.5, 10.0],
                "residual": [0.0, 0.0, 0.0],
                "branch1": [7.0, 7.5, 8.0],
                "branch2": [0.0, 1.0, 2.0],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "approved_model_spec.json").write_text(
        json.dumps(
            {
                "review_status": "approved",
                "model_id": "branch-flow-fit",
                "approved_model": {
                    "model_id": "branch-flow-fit",
                    "selected_method": "constrained piecewise least squares",
                    "candidate_methods": [{"name": "piecewise least squares"}],
                    "assumptions": ["Branch separation is conditional on an explicit identifiability constraint."],
                    "equations": ["q_main(t) = q_1(t) + q_2(t)"],
                    "solve_steps": ["Read Table 1.", "Fit the breakpoint and coefficients."],
                    "figure_requests": [{"path": "figures/problem1_fit.png", "role": "fit"}],
                },
                "required_outputs": [
                    "parameter_registry.json",
                    "result_registry.json",
                    "figures/problem1_fit.png",
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "model_review.json").write_text(
        json.dumps(
            {
                "model_id": "branch-flow-fit",
                "status": "approved",
                "identifiability": "conditional_on_explicit_constraint",
                "dimensional_consistency": "passed",
                "data_sufficiency": "passed",
                "computational_feasibility": "passed",
            }
        ),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "execution_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "task_id": "task-fit",
                "model_id": "branch-flow-fit",
                "status": "passed",
                "entrypoint": "solve.py",
                "program_sha256": "abc123",
                "execution_count": 1,
                "elapsed_seconds": 0.1,
                "generated_files": [
                    "solver_results.json",
                    "parameter_registry.json",
                    "result_registry.json",
                    "figures/problem1_fit.png",
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest_path


def test_result_verification_stage_checks_outputs_and_residual_metrics(tmp_path: Path) -> None:
    from app.guidance.stages import ResultVerificationStage

    manifest_path = _write_verified_fit_artifacts(tmp_path)
    output = asyncio.run(
        ResultVerificationStage().run(
            task_id="task-fit",
            task={"work_dir": str(tmp_path)},
            input_path=manifest_path,
            output_path=tmp_path / "verification_report.json",
        )
    )

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "verified"
    assert report["input_checks"][0]["status"] == "passed"
    assert report["residual_checks"][0]["status"] == "passed"
    assert report["artifact_refs"]["result_registry"] == "result_registry.json"


def test_result_verification_blocks_tampered_residual_metrics(tmp_path: Path) -> None:
    from app.guidance.stages import ResultVerificationStage

    manifest_path = _write_verified_fit_artifacts(tmp_path)
    registry_path = tmp_path / "result_registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["verified_results"][0]["metrics"]["rmse"] = 1.0
    registry_path.write_text(json.dumps(registry), encoding="utf-8")

    output = asyncio.run(
        ResultVerificationStage().run(
            task_id="task-fit",
            task={"work_dir": str(tmp_path)},
            input_path=manifest_path,
            output_path=tmp_path / "verification_report.json",
        )
    )

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "blocked"
    assert report["residual_checks"][0]["status"] == "failed"


def test_guidance_stage_uses_verified_contracts_and_writes_audit(tmp_path: Path) -> None:
    from app.guidance.stages import GuidanceStage, ResultVerificationStage

    manifest_path = _write_verified_fit_artifacts(tmp_path)
    report_path = asyncio.run(
        ResultVerificationStage().run(
            task_id="task-fit",
            task={"work_dir": str(tmp_path)},
            input_path=manifest_path,
            output_path=tmp_path / "verification_report.json",
        )
    )
    output = asyncio.run(
        GuidanceStage().run(
            task_id="task-fit",
            task={"work_dir": str(tmp_path)},
            input_path=report_path,
            output_path=tmp_path / "guidance.md",
        )
    )

    guidance = output.read_text(encoding="utf-8")
    audit = json.loads((tmp_path / "guidance_audit_report.json").read_text(encoding="utf-8"))
    assert "## 模型选择理由" in guidance
    assert "param_t_break" in guidance
    assert "result_piecewise_fit" in guidance
    assert "figures/problem1_fit.png" in guidance
    assert "可辨识" in guidance
    assert audit["status"] == "PASS"
