import asyncio
import json
from pathlib import Path

import pytest


class FakeProgramGenerator:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, *, approved_model, task: dict, work_dir: Path, **kwargs):
        from app.guidance.contracts import GeneratedProgram

        self.calls += 1
        return GeneratedProgram(
            source_code="print('complete solver')",
            declared_outputs=approved_model.required_outputs,
        )


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
            candidate_methods=[{
                "name": "piecewise least squares",
                "selected": True,
                "reason": "matches the stated piecewise trend",
            }],
            assumptions=["The branch decomposition uses an explicit identifiability constraint."],
            subproblem_models=[{
                "subproblem_id": "problem_1",
                "objective": "Fit branch flows.",
                "selected_method": "piecewise least squares",
                "rationale": "The constrained model identifies both branches.",
                "equations": ["q_main(t) = q_1(t) + q_2(t)"],
                "parameters": [{
                    "parameter_id": "breakpoint",
                    "symbol": "t_break",
                    "meaning": "piecewise breakpoint",
                    "unit": "sample",
                    "source_type": "estimated",
                    "source_detail": "data/attachment.xlsx",
                    "estimation_method": "least-squares scan",
                    "constraints": ["inside the observation interval"],
                }],
                "constraints": ["branch flows are nonnegative"],
                "solve_steps": ["Read Table 1.", "Fit the constrained piecewise model."],
                "expected_results": ["branch functions and residual metrics"],
            }],
            figure_requests=[{"path": "figures/problem1_fit.png", "role": "fit"}],
        )


class FakeReviewer:
    async def review(self, *, problem_spec, model_spec, task: dict, work_dir: Path):
        from app.guidance.contracts import ModelReview

        return ModelReview(
            model_id=model_spec.model_id,
            status="approved",
            identifiability="conditional_on_explicit_constraint",
            dimensional_consistency="passed",
            data_sufficiency="passed",
            computational_feasibility="passed",
        )


class RevisingFakeModeler(FakeModeler):
    def __init__(self) -> None:
        self.revision_calls = 0

    async def revise(
        self,
        *,
        problem_spec,
        previous_model,
        review,
        task: dict,
        work_dir: Path,
    ):
        from app.guidance.contracts import RevisedModelSpec

        self.revision_calls += 1
        return RevisedModelSpec.model_validate(
            {
                **previous_model.model_dump(mode="json"),
                "assumptions": [
                    *previous_model.assumptions,
                    review.required_revisions[0],
                ],
                "review_resolutions": [{
                    "requirement": review.required_revisions[0],
                    "resolution": "Added the explicit identification constraint.",
                    "affected_subproblem_ids": ["problem_1"],
                    "changed_fields": ["assumptions", "subproblem_models.0.constraints"],
                }],
            }
        )


class UnresolvedRevisionModeler(FakeModeler):
    async def revise(
        self,
        *,
        problem_spec,
        previous_model,
        review,
        task: dict,
        work_dir: Path,
    ):
        return previous_model


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
    assert payload["subproblems"][0]["id"] == "problem1"
    assert payload["data_sources"] == [{"path": "data/attachment.xlsx"}]


def test_modeling_stage_consumes_saved_problem_spec_and_saves_model_spec(tmp_path: Path) -> None:
    from app.guidance.stages import ModelingStage

    problem_path = tmp_path / "problem_spec.json"
    problem_path.write_text(
        json.dumps({"task_summary": "Fit branch flows.", "subproblems": [{"id": "problem_1"}]}),
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
    assert payload["subproblem_models"][0]["equations"]
    assert payload["figure_requests"]


def test_modeling_stage_rejects_missing_or_extra_subproblem_models(tmp_path: Path) -> None:
    from app.guidance.stages import ModelingStage

    problem_path = tmp_path / "problem_spec.json"
    problem_path.write_text(
        json.dumps(
            {
                "task_summary": "Fit two traffic subproblems.",
                "subproblems": [{"id": "problem_1"}, {"id": "problem_2"}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="subproblem coverage mismatch"):
        asyncio.run(
            ModelingStage(modeler=FakeModeler()).run(
                task_id="task-incomplete-model",
                task={"work_dir": str(tmp_path)},
                input_path=problem_path,
                output_path=tmp_path / "model_spec.json",
            )
        )


def test_modeling_stage_revises_from_saved_review_contract(tmp_path: Path) -> None:
    from app.guidance.stages import ModelingStage

    problem_path = tmp_path / "problem_spec.json"
    problem_path.write_text(
        json.dumps({"task_summary": "Fit branch flows.", "subproblems": [{"id": "problem_1"}]}),
        encoding="utf-8",
    )
    previous_model = asyncio.run(
        FakeModeler().model(problem_spec=None, task={}, work_dir=tmp_path)
    )
    (tmp_path / "model_spec.json").write_text(
        previous_model.model_dump_json(),
        encoding="utf-8",
    )
    review_path = tmp_path / "model_review.json"
    review_path.write_text(
        json.dumps(
            {
                "model_id": "branch-flow-fit",
                "status": "revision_required",
                "problem_spec_ref": "problem_spec.json",
                "model_spec_ref": "model_spec.json",
                "required_revisions": ["Add an explicit identifiability constraint."],
            }
        ),
        encoding="utf-8",
    )
    modeler = RevisingFakeModeler()

    output = asyncio.run(
        ModelingStage(modeler=modeler).run(
            task_id="task-fit",
            task={"work_dir": str(tmp_path)},
            input_path=review_path,
            output_path=tmp_path / "model_spec.json",
        )
    )

    revised = json.loads(output.read_text(encoding="utf-8"))
    assert modeler.revision_calls == 1
    assert "Add an explicit identifiability constraint." in revised["assumptions"]


def test_modeling_stage_rejects_revision_without_resolving_each_review_requirement(
    tmp_path: Path,
) -> None:
    from app.guidance.stages import ModelingStage

    (tmp_path / "problem_spec.json").write_text(
        json.dumps({"task_summary": "Fit branch flows.", "subproblems": [{"id": "problem_1"}]}),
        encoding="utf-8",
    )
    previous_model = asyncio.run(FakeModeler().model(problem_spec=None, task={}, work_dir=tmp_path))
    (tmp_path / "model_spec.json").write_text(previous_model.model_dump_json(), encoding="utf-8")
    review_path = tmp_path / "model_review.json"
    review_path.write_text(
        json.dumps(
            {
                "model_id": "branch-flow-fit",
                "status": "revision_required",
                "required_revisions": ["Add an explicit identifiability constraint."],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unresolved review requirements"):
        asyncio.run(
            ModelingStage(modeler=UnresolvedRevisionModeler()).run(
                task_id="task-unresolved-revision",
                task={"work_dir": str(tmp_path)},
                input_path=review_path,
                output_path=tmp_path / "model_spec.json",
            )
        )


def test_model_review_stage_saves_review_and_approved_model_contracts(tmp_path: Path) -> None:
    from app.guidance.stages import ModelReviewStage

    (tmp_path / "problem_spec.json").write_text(
        json.dumps({"task_summary": "Fit branch flows.", "subproblems": [{"id": "problem_1"}]}),
        encoding="utf-8",
    )
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


def test_model_review_stage_passes_saved_problem_contract_to_reviewer(tmp_path: Path) -> None:
    from app.guidance.contracts import ModelReview
    from app.guidance.stages import ModelReviewStage

    class ProblemAwareReviewer:
        def __init__(self) -> None:
            self.problem_summary = ""

        async def review(self, *, problem_spec, model_spec, task: dict, work_dir: Path):
            self.problem_summary = problem_spec.task_summary
            return ModelReview(model_id=model_spec.model_id, status="approved")

    (tmp_path / "problem_spec.json").write_text(
        json.dumps({"task_summary": "Estimate all branch flows.", "subproblems": [{"id": "problem_1"}]}),
        encoding="utf-8",
    )
    model_spec = asyncio.run(FakeModeler().model(problem_spec=None, task={}, work_dir=tmp_path))
    model_path = tmp_path / "model_spec.json"
    model_path.write_text(model_spec.model_dump_json(), encoding="utf-8")
    reviewer = ProblemAwareReviewer()

    asyncio.run(
        ModelReviewStage(reviewer=reviewer).run(
            task_id="task-problem-aware-review",
            task={"work_dir": str(tmp_path)},
            input_path=model_path,
            output_path=tmp_path / "approved_model_spec.json",
        )
    )

    assert reviewer.problem_summary == "Estimate all branch flows."


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


def test_calculation_stage_removes_stale_failure_checkpoint_after_success(tmp_path: Path) -> None:
    from app.guidance.execution import LocalProgramExecutor
    from app.guidance.stages import CalculationStage

    (tmp_path / "calculation_failure.json").write_text(
        json.dumps({"failed_operation": "program_generation"}),
        encoding="utf-8",
    )
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
    stage = CalculationStage(
        program_generator=FakeProgramGenerator(),
        executor=LocalProgramExecutor(
            interpreter_factory=lambda work_dir: CountingInterpreter(tmp_path)
        ),
    )

    asyncio.run(
        stage.run(
            task_id="task-success-after-failure",
            task={"work_dir": str(tmp_path)},
            input_path=approved_path,
            output_path=tmp_path / "execution_manifest.json",
        )
    )

    assert not (tmp_path / "calculation_failure.json").exists()


def test_calculation_stage_profiles_uploaded_workbook_before_program_generation(
    tmp_path: Path,
) -> None:
    from openpyxl import Workbook

    from app.guidance.contracts import GeneratedProgram
    from app.guidance.execution import LocalProgramExecutor
    from app.guidance.stages import CalculationStage

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "表1"
    sheet.append(["时刻", "主路3车流量"])
    sheet.append(["7:00", 12.5])
    sheet.append(["7:02", 13.0])
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    workbook.save(data_dir / "附件(Attachment).xlsx")
    approved_path = tmp_path / "approved_model_spec.json"
    approved_path.write_text(
        json.dumps(
            {
                "review_status": "approved",
                "model_id": "traffic-flow-profile",
                "required_outputs": ["solver_results.json"],
            }
        ),
        encoding="utf-8",
    )

    class ProfileAwareProgramGenerator:
        def __init__(self) -> None:
            self.data_profile = None

        async def generate(self, *, approved_model, task: dict, work_dir: Path, data_profile):
            self.data_profile = data_profile
            return GeneratedProgram(
                source_code="print('profile-aware solver')",
                declared_outputs=approved_model.required_outputs,
            )

    generator = ProfileAwareProgramGenerator()
    interpreter = CountingInterpreter(tmp_path)
    stage = CalculationStage(
        program_generator=generator,
        executor=LocalProgramExecutor(interpreter_factory=lambda work_dir: interpreter),
    )

    asyncio.run(
        stage.run(
            task_id="task-profiled-generation",
            task={"work_dir": str(tmp_path)},
            input_path=approved_path,
            output_path=tmp_path / "execution_manifest.json",
        )
    )

    assert generator.data_profile is not None
    assert generator.data_profile["files"][0]["path"] == "data/附件(Attachment).xlsx"
    workbook_profile = generator.data_profile["files"][0]["workbook"]
    assert workbook_profile["sheets"][0]["name"] == "表1"
    assert workbook_profile["sheets"][0]["columns"] == ["时刻", "主路3车流量"]
    assert workbook_profile["sheets"][0]["sample_rows"][0] == ["7:00", 12.5]


def test_calculation_stage_saves_generation_failure_checkpoint(tmp_path: Path) -> None:
    from app.guidance.stages import CalculationStage

    class TimeoutProgramGenerator:
        async def generate(self, **kwargs):
            raise RuntimeError("GuidanceProgramGenerator: 请求超时，超过 240s")

    approved_path = tmp_path / "approved_model_spec.json"
    approved_path.write_text(
        json.dumps(
            {
                "review_status": "approved",
                "model_id": "traffic-flow",
                "required_outputs": ["solver_results.json"],
            }
        ),
        encoding="utf-8",
    )
    stage = CalculationStage(program_generator=TimeoutProgramGenerator(), executor=object())

    output = asyncio.run(
        stage.run(
            task_id="task-generation-timeout",
            task={"work_dir": str(tmp_path)},
            input_path=approved_path,
            output_path=tmp_path / "execution_manifest.json",
        )
    )

    checkpoint = json.loads((tmp_path / "calculation_failure.json").read_text(encoding="utf-8"))
    assert checkpoint["failed_operation"] == "program_generation"
    assert checkpoint["resume_from"] == "approved_model_spec.json"
    assert checkpoint["model_id"] == "traffic-flow"
    assert approved_path.is_file()

    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert manifest["execution_count"] == 0
    assert manifest["missing_required_outputs"] == ["solver_results.json"]
    assert "GuidanceProgramGenerator" in manifest["error_message"]


def test_calculation_stage_saves_executor_failure_checkpoint(tmp_path: Path) -> None:
    from app.guidance.contracts import GeneratedProgram
    from app.guidance.stages import CalculationStage

    class OutputMismatchProgramGenerator:
        async def generate(self, **kwargs):
            return GeneratedProgram(
                source_code="print('solver')",
                declared_outputs=["outputs/solver_results.json"],
            )

    class FailingExecutor:
        async def execute(self, **kwargs):
            raise ValueError(
                "declared output mismatch: required=['solver_results.json']; "
                "declared=['outputs/solver_results.json']"
            )

    approved_path = tmp_path / "approved_model_spec.json"
    approved_path.write_text(
        json.dumps(
            {
                "review_status": "approved",
                "model_id": "traffic-flow",
                "required_outputs": ["solver_results.json"],
            }
        ),
        encoding="utf-8",
    )
    stage = CalculationStage(
        program_generator=OutputMismatchProgramGenerator(),
        executor=FailingExecutor(),
    )

    output = asyncio.run(
        stage.run(
            task_id="task-executor-output-mismatch",
            task={"work_dir": str(tmp_path)},
            input_path=approved_path,
            output_path=tmp_path / "execution_manifest.json",
        )
    )

    checkpoint = json.loads((tmp_path / "calculation_failure.json").read_text(encoding="utf-8"))
    assert checkpoint["failed_operation"] == "solver_execution"
    assert checkpoint["resume_from"] == "approved_model_spec.json"
    assert checkpoint["model_id"] == "traffic-flow"

    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert manifest["execution_count"] == 0
    assert manifest["missing_required_outputs"] == ["solver_results.json"]
    assert "declared output mismatch" in manifest["error_message"]


def _write_verified_fit_artifacts(tmp_path: Path) -> Path:
    (tmp_path / "figures").mkdir(exist_ok=True)
    (tmp_path / "figures" / "problem1_fit.png").write_bytes(b"real-png")
    (tmp_path / "figure_registry.json").write_text(
        json.dumps(
            {
                "figures": [
                    {
                        "path": "figures/problem1_fit.png",
                        "role": "result_fit",
                        "source_data": ["data/attachment.xlsx"],
                        "linked_result_ids": ["result_piecewise_fit"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
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
                        "result_type": "regression",
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
                "evidence": [{
                    "kind": "regression",
                    "subproblem_id": "problem_1",
                    "result_id": "result_piecewise_fit",
                    "observed": [7.0, 8.5, 10.0],
                    "predicted": [7.0, 8.5, 10.0],
                    "residuals": [0.0, 0.0, 0.0],
                    "constraint_values": [7.0, 7.5, 8.0, 0.0, 1.0, 2.0],
                    "parameter_stability": {
                        "method": "leave-one-out refit",
                        "max_relative_change": 0.02,
                        "threshold": 0.10,
                    },
                }]
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
                    "subproblem_models": [{
                        "subproblem_id": "problem_1",
                        "objective": "Infer the branch flows.",
                        "selected_method": "constrained piecewise least squares",
                        "rationale": "The identification constraint makes the split estimable.",
                        "equations": ["q_main(t) = q_1(t) + q_2(t)"],
                        "parameters": [{
                            "parameter_id": "breakpoint",
                            "symbol": "t_break",
                            "meaning": "piecewise breakpoint",
                            "unit": "sample index",
                            "source_type": "estimated",
                            "source_detail": "data/attachment.xlsx",
                            "estimation_method": "breakpoint scan",
                            "constraints": ["t_break >= 0"],
                        }],
                        "constraints": ["branch flows are nonnegative"],
                        "solve_steps": ["Read Table 1.", "Fit the breakpoint and coefficients."],
                        "expected_results": ["fitted branch flows"],
                    }],
                    "figure_requests": [{"path": "figures/problem1_fit.png", "role": "fit"}],
                },
                "required_outputs": [
                    "parameter_registry.json",
                    "result_registry.json",
                    "figure_registry.json",
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


def test_result_verification_accepts_yes_as_positive_model_review_status(tmp_path: Path) -> None:
    from app.guidance.stages import ResultVerificationStage

    manifest_path = _write_verified_fit_artifacts(tmp_path)
    review = json.loads((tmp_path / "model_review.json").read_text(encoding="utf-8"))
    review["dimensional_consistency"] = "yes"
    (tmp_path / "model_review.json").write_text(json.dumps(review), encoding="utf-8")

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
    assert report["unit_checks"][0]["status"] == "passed"


def test_result_verification_accepts_registered_existing_figure_not_listed_in_manifest(tmp_path: Path) -> None:
    from app.guidance.stages import ResultVerificationStage

    manifest_path = _write_verified_fit_artifacts(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["generated_files"] = [
        path for path in manifest["generated_files"] if path != "figures/problem1_fit.png"
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

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
    assert report["blocking_issues"] == []


def test_result_verification_blocks_verified_result_without_parameter_evidence(tmp_path: Path) -> None:
    from app.guidance.stages import ResultVerificationStage

    manifest_path = _write_verified_fit_artifacts(tmp_path)
    (tmp_path / "parameter_registry.json").write_text(
        json.dumps({"parameters": []}),
        encoding="utf-8",
    )

    output = asyncio.run(
        ResultVerificationStage().run(
            task_id="task-missing-parameter-evidence",
            task={"work_dir": str(tmp_path)},
            input_path=manifest_path,
            output_path=tmp_path / "verification_report.json",
        )
    )

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "blocked"
    assert any("parameter evidence" in issue for issue in report["blocking_issues"])


def test_result_verification_blocks_parameter_registry_with_missing_source_ref(tmp_path: Path) -> None:
    from app.guidance.stages import ResultVerificationStage

    manifest_path = _write_verified_fit_artifacts(tmp_path)
    registry_path = tmp_path / "parameter_registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    del registry["parameters"][0]["source_ref"]
    registry_path.write_text(json.dumps(registry), encoding="utf-8")

    output = asyncio.run(
        ResultVerificationStage().run(
            task_id="task-malformed-parameter-registry",
            task={"work_dir": str(tmp_path)},
            input_path=manifest_path,
            output_path=tmp_path / "verification_report.json",
        )
    )

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "blocked"
    assert any("parameter_registry.json contract invalid" in issue for issue in report["blocking_issues"])


def test_result_verification_blocks_truncated_registry_json_without_crashing(tmp_path: Path) -> None:
    from app.guidance.stages import ResultVerificationStage

    manifest_path = _write_verified_fit_artifacts(tmp_path)
    (tmp_path / "parameter_registry.json").write_text(
        '{"parameters": [{"id": "truncated"}',
        encoding="utf-8",
    )

    output = asyncio.run(
        ResultVerificationStage().run(
            task_id="task-truncated-registry",
            task={"work_dir": str(tmp_path)},
            input_path=manifest_path,
            output_path=tmp_path / "verification_report.json",
        )
    )

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "blocked"
    assert any("parameter_registry.json contract invalid" in issue for issue in report["blocking_issues"])


def test_result_verification_blocks_solver_result_id_mismatch(tmp_path: Path) -> None:
    from app.guidance.stages import ResultVerificationStage

    manifest_path = _write_verified_fit_artifacts(tmp_path)
    solver_path = tmp_path / "solver_results.json"
    solver_results = json.loads(solver_path.read_text(encoding="utf-8"))
    solver_results["evidence"][0]["result_id"] = "result_not_registered"
    solver_path.write_text(json.dumps(solver_results), encoding="utf-8")

    output = asyncio.run(
        ResultVerificationStage().run(
            task_id="task-result-id-mismatch",
            task={"work_dir": str(tmp_path)},
            input_path=manifest_path,
            output_path=tmp_path / "verification_report.json",
        )
    )

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "blocked"
    assert any("solver/result registry ID mismatch" in issue for issue in report["blocking_issues"])


def test_result_verification_accepts_typed_regression_evidence(tmp_path: Path) -> None:
    from app.guidance.stages import ResultVerificationStage

    manifest_path = _write_verified_fit_artifacts(tmp_path)
    (tmp_path / "solver_results.json").write_text(
        json.dumps(
            {
                "evidence": [{
                    "kind": "regression",
                    "subproblem_id": "problem_1",
                    "result_id": "result_piecewise_fit",
                    "observed": [7.0, 8.5, 10.0],
                    "predicted": [7.0, 8.5, 10.0],
                    "residuals": [0.0, 0.0, 0.0],
                    "constraint_values": [7.0, 7.5, 8.0, 0.0, 1.0, 2.0],
                    "parameter_stability": {
                        "method": "leave-one-out refit",
                        "max_relative_change": 0.02,
                        "threshold": 0.10,
                    },
                }]
            }
        ),
        encoding="utf-8",
    )

    output = asyncio.run(
        ResultVerificationStage().run(
            task_id="task-typed-regression",
            task={"work_dir": str(tmp_path)},
            input_path=manifest_path,
            output_path=tmp_path / "verification_report.json",
        )
    )

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "verified"
    assert report["residual_checks"][0]["recomputed_metrics"]["rmse"] == 0.0
    assert report["residual_checks"][0]["status"] == "passed"
    assert report["sensitivity_checks"][0]["status"] == "passed"
    assert report["artifact_refs"]["result_registry"] == "result_registry.json"
    assert report["artifact_refs"]["figures"][0]["role"] == "result_fit"


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


def test_result_verification_blocks_unstable_regression_parameters(tmp_path: Path) -> None:
    from app.guidance.stages import ResultVerificationStage

    manifest_path = _write_verified_fit_artifacts(tmp_path)
    solver_path = tmp_path / "solver_results.json"
    solver_results = json.loads(solver_path.read_text(encoding="utf-8"))
    solver_results["evidence"][0]["parameter_stability"]["max_relative_change"] = 0.25
    solver_path.write_text(json.dumps(solver_results), encoding="utf-8")

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
    assert report["sensitivity_checks"][0]["status"] == "failed"
    assert any("parameter stability" in issue for issue in report["blocking_issues"])


def test_result_verification_blocks_nonessential_figure_role(tmp_path: Path) -> None:
    from app.guidance.stages import ResultVerificationStage

    manifest_path = _write_verified_fit_artifacts(tmp_path)
    registry_path = tmp_path / "figure_registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["figures"][0]["role"] = "decorative_cover"
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
    assert any("figure_registry.json contract invalid" in issue for issue in report["blocking_issues"])


def test_result_verification_blocks_figure_without_source_or_result_binding(tmp_path: Path) -> None:
    from app.guidance.stages import ResultVerificationStage

    manifest_path = _write_verified_fit_artifacts(tmp_path)
    registry_path = tmp_path / "figure_registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["figures"][0]["source_data"] = []
    registry["figures"][0]["linked_result_ids"] = ["result_missing"]
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
    assert any("figure_registry.json contract invalid" in issue for issue in report["blocking_issues"])


def test_result_verification_blocks_missing_figure_registry_without_crashing(tmp_path: Path) -> None:
    from app.guidance.stages import ResultVerificationStage

    manifest_path = _write_verified_fit_artifacts(tmp_path)
    (tmp_path / "figure_registry.json").unlink()

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
    assert any("not registered" in issue for issue in report["blocking_issues"])


def test_result_verification_blocks_failed_solver_with_missing_registries(tmp_path: Path) -> None:
    from app.guidance.stages import ResultVerificationStage

    (tmp_path / "model_review.json").write_text(
        json.dumps(
            {
                "model_id": "failed-solver",
                "status": "approved",
                "dimensional_consistency": "consistent; units are defined and aligned",
            }
        ),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "execution_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "task_id": "task-failed-solver",
                "model_id": "failed-solver",
                "status": "failed",
                "entrypoint": "solve.py",
                "program_sha256": "abc123",
                "execution_count": 1,
                "elapsed_seconds": 10.0,
                "error_message": "ValueError: plot labels do not match ticks",
                "generated_files": ["notebook.ipynb"],
                "missing_required_outputs": [
                    "solver_results.json",
                    "parameter_registry.json",
                    "result_registry.json",
                    "figure_registry.json",
                ],
            }
        ),
        encoding="utf-8",
    )

    output = asyncio.run(
        ResultVerificationStage().run(
            task_id="task-failed-solver",
            task={"work_dir": str(tmp_path)},
            input_path=manifest_path,
            output_path=tmp_path / "verification_report.json",
        )
    )

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "blocked"
    assert any("solver status is failed" in issue for issue in report["blocking_issues"])
    assert any("result_registry.json" in issue for issue in report["blocking_issues"])


def test_guidance_discloses_failed_solver_without_result_registries(tmp_path: Path) -> None:
    from app.guidance.stages import GuidanceStage, ResultVerificationStage

    (tmp_path / "approved_model_spec.json").write_text(
        json.dumps(
            {
                "review_status": "approved",
                "model_id": "failed-solver",
                "approved_model": {
                    "model_id": "failed-solver",
                    "selected_method": "constrained optimization",
                    "candidate_methods": [],
                    "assumptions": [],
                    "equations": ["minimize SSE"],
                    "solve_steps": ["Run the complete solver."],
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "model_review.json").write_text(
        json.dumps(
            {
                "model_id": "failed-solver",
                "status": "approved",
                "dimensional_consistency": "passed",
            }
        ),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "execution_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "task_id": "task-failed-solver",
                "model_id": "failed-solver",
                "status": "failed",
                "entrypoint": "solve.py",
                "program_sha256": "abc123",
                "execution_count": 1,
                "elapsed_seconds": 10.0,
                "error_message": "ValueError: plot labels do not match ticks",
                "generated_files": [],
                "missing_required_outputs": [
                    "solver_results.json",
                    "parameter_registry.json",
                    "result_registry.json",
                    "figure_registry.json",
                ],
            }
        ),
        encoding="utf-8",
    )
    report_path = asyncio.run(
        ResultVerificationStage().run(
            task_id="task-failed-solver",
            task={"work_dir": str(tmp_path)},
            input_path=manifest_path,
            output_path=tmp_path / "verification_report.json",
        )
    )

    output = asyncio.run(
        GuidanceStage().run(
            task_id="task-failed-solver",
            task={"work_dir": str(tmp_path)},
            input_path=report_path,
            output_path=tmp_path / "guidance.md",
        )
    )

    guidance = output.read_text(encoding="utf-8")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    audit = json.loads((tmp_path / "guidance_audit_report.json").read_text(encoding="utf-8"))
    assert "# 计算链路阻断诊断" in guidance
    assert "失败阶段：`calculation`" in guidance
    assert "失败操作：`solver_execution`" in guidance
    assert "solver status is failed" in guidance
    assert "result_registry.json" in guidance
    assert "## 参数与来源表" not in guidance
    assert "constrained optimization" not in guidance
    assert "model review did not pass dimensional consistency" not in report["blocking_issues"]
    assert "model review did not pass dimensional consistency" not in guidance
    assert "No such file or directory" not in guidance
    assert str(tmp_path) not in guidance
    assert audit["status"] == "PASS"
    assert audit["blocks"] == []
    assert not [
        warning
        for warning in audit["warnings"]
        if warning["type"] == "missing_expected_section"
    ]


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
    assert "data/attachment.xlsx" in guidance
    assert "result_fit" in guidance
    assert "可辨识" in guidance
    assert audit["status"] == "PASS"
    assert audit["blocks"] == []


def test_guidance_downgrades_forged_verified_report_without_checks(tmp_path: Path) -> None:
    from app.guidance.stages import GuidanceStage, ResultVerificationStage

    manifest_path = _write_verified_fit_artifacts(tmp_path)
    report_path = asyncio.run(
        ResultVerificationStage().run(
            task_id="task-forged-verification",
            task={"work_dir": str(tmp_path)},
            input_path=manifest_path,
            output_path=tmp_path / "verification_report.json",
        )
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["equation_checks"] = []
    report_path.write_text(json.dumps(report), encoding="utf-8")

    output = asyncio.run(
        GuidanceStage().run(
            task_id="task-forged-verification",
            task={"work_dir": str(tmp_path)},
            input_path=report_path,
            output_path=tmp_path / "guidance.md",
        )
    )

    guidance = output.read_text(encoding="utf-8")
    assert guidance.startswith("# 结果复核阻断诊断")
    assert "verified guidance evidence missing: equation checks" in guidance
    assert "## 参数与来源表" not in guidance


def test_guidance_stage_renders_blocked_verification_as_diagnostic_not_formal_plan(
    tmp_path: Path,
) -> None:
    from app.guidance.stages import GuidanceStage, ResultVerificationStage

    manifest_path = _write_verified_fit_artifacts(tmp_path)
    report_path = asyncio.run(
        ResultVerificationStage().run(
            task_id="task-blocked-verification",
            task={"work_dir": str(tmp_path)},
            input_path=manifest_path,
            output_path=tmp_path / "verification_report.json",
        )
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["status"] = "blocked"
    report["constraint_checks"] = [{"status": "failed", "result_id": "result_piecewise_fit"}]
    report["blocking_issues"] = ["result_piecewise_fit: recorded nonnegative constraints fail"]
    report_path.write_text(json.dumps(report), encoding="utf-8")

    output = asyncio.run(
        GuidanceStage().run(
            task_id="task-blocked-verification",
            task={"work_dir": str(tmp_path)},
            input_path=report_path,
            output_path=tmp_path / "guidance.md",
        )
    )

    guidance = output.read_text(encoding="utf-8")
    audit = json.loads((tmp_path / "guidance_audit_report.json").read_text(encoding="utf-8"))
    assert guidance.startswith("# 结果复核阻断诊断")
    assert "失败阶段：`verification`" in guidance
    assert "约束满足复核：0/1 项通过" in guidance
    assert "result_piecewise_fit: recorded nonnegative constraints fail" in guidance
    assert "## 参数与来源表" not in guidance
    assert "## 必要计算结果" not in guidance
    assert audit["status"] == "PASS"
    assert audit["blocks"] == []
    assert not [
        warning
        for warning in audit["warnings"]
        if warning["type"] == "missing_expected_section"
    ]


def test_guidance_audit_blocks_deleted_registered_figure(tmp_path: Path) -> None:
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
    (tmp_path / "figures" / "problem1_fit.png").unlink()

    asyncio.run(
        GuidanceStage().run(
            task_id="task-fit",
            task={"work_dir": str(tmp_path)},
            input_path=report_path,
            output_path=tmp_path / "guidance.md",
        )
    )

    audit = json.loads((tmp_path / "guidance_audit_report.json").read_text(encoding="utf-8"))
    assert audit["status"] == "BLOCK"
    assert any(block["type"] == "missing_figure_file" for block in audit["blocks"])


def test_guidance_stage_writes_blocked_artifacts_from_failed_model_review(tmp_path: Path) -> None:
    from app.guidance.stages import GuidanceStage

    (tmp_path / "model_review.json").write_text(
        json.dumps(
            {
                "model_id": "unidentifiable-model",
                "status": "revision_required",
                "identifiability": "failed",
                "dimensional_consistency": "passed",
                "data_sufficiency": "passed",
                "computational_feasibility": "passed",
                "blocking_issues": ["Branch parameters are not uniquely identifiable."],
                "required_revisions": ["Add an independently justified identification constraint."],
            }
        ),
        encoding="utf-8",
    )
    approved_path = tmp_path / "approved_model_spec.json"
    approved_path.write_text(
        json.dumps(
            {
                "review_status": "revision_required",
                "model_id": "unidentifiable-model",
                "approved_model": {
                    "model_id": "unidentifiable-model",
                    "selected_method": "unconstrained branch decomposition",
                    "candidate_methods": [],
                    "assumptions": [],
                    "equations": ["q_main = q_1 + q_2"],
                    "solve_steps": [],
                },
            }
        ),
        encoding="utf-8",
    )

    output = asyncio.run(
        GuidanceStage().run(
            task_id="task-blocked",
            task={"work_dir": str(tmp_path)},
            input_path=approved_path,
            output_path=tmp_path / "guidance.md",
        )
    )

    guidance = output.read_text(encoding="utf-8")
    report = json.loads((tmp_path / "verification_report.json").read_text(encoding="utf-8"))
    results = json.loads((tmp_path / "result_registry.json").read_text(encoding="utf-8"))
    audit = json.loads((tmp_path / "guidance_audit_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "blocked"
    assert results["blocked_results"]
    assert "Branch parameters are not uniquely identifiable." in guidance
    assert "Add an independently justified identification constraint." in guidance
    assert "unconstrained branch decomposition" not in guidance
    assert "q_main = q_1 + q_2" not in guidance
    assert audit["status"] == "PASS"
    assert audit["blocks"] == []


def test_guidance_stage_renders_modeling_failure_as_diagnostic_not_empty_plan(
    tmp_path: Path,
) -> None:
    from app.guidance.stages import GuidanceStage

    (tmp_path / "modeling_failure.json").write_text(
        json.dumps(
            {
                "task_id": "task-modeling-failure",
                "failed_stage": "modeling",
                "failed_operation": "model_spec_generation",
                "input_ref": "problem_spec.json",
                "error_type": "ContractGenerationError",
                "error": "identifiability plan missing for subproblems: problem1, problem2",
                "resume_from": "problem_spec.json",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "model_review.json").write_text(
        json.dumps(
            {
                "model_id": "modeling-contract-failure",
                "status": "blocked",
                "identifiability": "not_reviewed",
                "dimensional_consistency": "not_reviewed",
                "data_sufficiency": "not_reviewed",
                "computational_feasibility": "blocked",
                "blocking_issues": [
                    "identifiability plan missing for subproblems: problem1, problem2"
                ],
                "required_revisions": [
                    "Regenerate a syntactically valid ModelSpec JSON contract before model review."
                ],
            }
        ),
        encoding="utf-8",
    )
    approved_path = tmp_path / "approved_model_spec.json"
    approved_path.write_text(
        json.dumps(
            {
                "review_status": "blocked",
                "model_id": "modeling-contract-failure",
                "approved_model": {
                    "model_id": "modeling-contract-failure",
                    "selected_method": "blocked_before_model_review",
                    "candidate_methods": [],
                    "assumptions": [],
                    "subproblem_models": [],
                },
            }
        ),
        encoding="utf-8",
    )

    output = asyncio.run(
        GuidanceStage().run(
            task_id="task-modeling-failure",
            task={"work_dir": str(tmp_path)},
            input_path=approved_path,
            output_path=tmp_path / "guidance.md",
        )
    )

    guidance = output.read_text(encoding="utf-8")
    results = json.loads((tmp_path / "result_registry.json").read_text(encoding="utf-8"))
    blocked_ids = [item["id"] for item in results["blocked_results"]]
    assert "# 建模链路阻断诊断" in guidance
    assert "失败阶段：`modeling`" in guidance
    assert "失败操作：`model_spec_generation`" in guidance
    assert "恢复入口：`problem_spec.json`" in guidance
    assert "identifiability plan missing for subproblems: problem1, problem2" in guidance
    assert len(blocked_ids) == len(set(blocked_ids))
    assert "## 参数与来源表" not in guidance
    assert "blocked_before_model_review" not in guidance


def test_review_positive_value_accepts_consistent_with_explanation() -> None:
    from app.guidance.stages import _is_positive_review_value

    assert _is_positive_review_value("consistent; units are defined and aligned")
