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


def _solvable_ir(model_id: str = "pipeline-model") -> dict:
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "model_ir"
        / "generic_multiperiod_inventory.json"
    )
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    payload["model_id"] = model_id
    return payload


def _approved_payload(status: str = "approved", model_id: str = "pipeline-model") -> dict:
    model_ir = _solvable_ir(model_id) if status == "approved" else _blocked_ir(model_id)
    return {
        "schema_version": "1.0",
        "review_status": status,
        "model_id": model_id,
        "model_ir_ref": "model_ir.json",
        "review_ref": "model_review.json",
        "executable_subproblem_ids": (
            ["finite_inventory_policy"] if status == "approved" else []
        ),
        "approved_model_ir": model_ir,
    }


def _partial_approved_payload() -> dict:
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "model_ir"
        / "generic_multiperiod_inventory.json"
    )
    model_ir = json.loads(fixture.read_text(encoding="utf-8"))
    model_ir["subproblems"].insert(0, {
        "execution_status": "blocked",
        "subproblem_id": "unavailable_estimate",
        "objective": "Estimate an unavailable quantity.",
        "blocking_reason": "The required observations are not registered.",
        "missing_inputs": ["registered observations"],
        "recovery_action": "Register the observations and regenerate this subproblem.",
        "expected_outputs": ["estimated quantity"],
    })
    return {
        "schema_version": "1.0",
        "review_status": "partial",
        "model_id": model_ir["model_id"],
        "model_ir_ref": "model_ir.json",
        "review_ref": "model_review.json",
        "executable_subproblem_ids": ["finite_inventory_policy"],
        "approved_model_ir": model_ir,
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
            "subproblem_reviews": [{
                "subproblem_id": "problem1",
                "status": status,
                "assumption_checks": [],
                "required_revisions": [{
                    "requirement_id": "declare-missing-operator",
                    "description": "Declare the missing operator explicitly.",
                    "acceptance_criteria": "The revised ModelIR declares the operator.",
                }] if status == "revision_required" else [],
            }],
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
        ("model_review", "model_review_submission.json"),
        ("calculation", "approved_model_ir.json"),
        ("result_verification", "execution_manifest.json"),
        ("guidance", "verification_report.json"),
    ]


def test_guidance_pipeline_executes_and_verifies_partial_approval(
    tmp_path: Path,
) -> None:
    from app.guidance.pipeline import GuidancePipeline

    calls: list[tuple[str, str | None]] = []
    pipeline = GuidancePipeline(
        decomposition_stage=RecordingStage("decomposition", calls),
        modeling_stage=RecordingStage("modeling", calls, _blocked_ir()),
        model_review_stage=RecordingStage(
            "model_review",
            calls,
            _partial_approved_payload(),
        ),
        calculation_stage=RecordingStage("calculation", calls),
        result_verification_stage=RecordingStage("result_verification", calls),
        guidance_stage=RecordingStage("guidance", calls),
    )

    _run_pipeline(pipeline, "task-partial-approval", tmp_path)

    assert calls == [
        ("decomposition", None),
        ("modeling", "problem_spec.json"),
        ("model_review", "model_review_submission.json"),
        ("calculation", "approved_model_ir.json"),
        ("result_verification", "execution_manifest.json"),
        ("guidance", "verification_report.json"),
    ]


def test_partial_review_and_verification_resolve_to_partial_task_status(
    tmp_path: Path,
) -> None:
    from app.guidance.pipeline import _resolve_final_status

    (tmp_path / "verification_report.json").write_text(
        json.dumps({"status": "partial"}),
        encoding="utf-8",
    )
    (tmp_path / "approved_model_ir.json").write_text(
        json.dumps({"review_status": "partial"}),
        encoding="utf-8",
    )

    assert _resolve_final_status(tmp_path, {"status": "PASS"}) == "partial"


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
        ("model_review", "model_review_submission.json"),
        ("modeling", "revision_request.json"),
        ("model_review", "model_review_submission.json"),
        ("calculation", "approved_model_ir.json"),
        ("result_verification", "execution_manifest.json"),
        ("guidance", "verification_report.json"),
    ]
    revision_request = json.loads(
        (tmp_path / "revision_request.json").read_text(encoding="utf-8")
    )
    assert revision_request["status"] == "revision_required"
    assert revision_request["model_ir_ref"] == "model_ir_initial.json"
    assert (tmp_path / "model_ir_initial.json").is_file()
    submission = json.loads(
        (tmp_path / "model_review_submission.json").read_text(encoding="utf-8")
    )
    assert submission == {
        "schema_version": "1.0",
        "model_ir_ref": "model_ir.json",
        "revision_request_ref": "revision_request.json",
    }


def test_blocked_review_emits_three_layer_guidance_without_running_calculation(
    tmp_path: Path,
) -> None:
    from app.guidance.pipeline import GuidancePipeline
    from app.guidance.guidance_stage import GuidanceStage

    calls: list[tuple[str, str | None]] = []
    pipeline = GuidancePipeline(
        decomposition_stage=RecordingStage("decomposition", calls),
        modeling_stage=RecordingStage("modeling", calls, _blocked_ir()),
        model_review_stage=RecordingStage(
            "model_review",
            calls,
            _approved_payload(status="blocked"),
        ),
        calculation_stage=RecordingStage("calculation", calls),
        result_verification_stage=RecordingStage("result_verification", calls),
        guidance_stage=GuidanceStage(),
    )

    messages = _run_pipeline(pipeline, "task-blocked-review", tmp_path)

    assert calls == [
        ("decomposition", None),
        ("modeling", "problem_spec.json"),
        ("model_review", "model_review_submission.json"),
    ]
    report = json.loads((tmp_path / "verification_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "blocked"
    assert report["execution_verified"]["status"] == "not_assessed"
    assert report["evidence_supported"]["status"] == "blocked"
    assert report["model_adequate"]["status"] == "blocked"
    assert report["model_adequate"]["numerical_validation"]["candidate_roles"] == []
    assert (tmp_path / "guidance.md").is_file()
    result = next(item for item in messages if item["type"] == "result")
    assert result["data"]["status"] == "blocked"
    assert result["data"]["verification_layers"] == {
        "execution_verified": "not_assessed",
        "evidence_supported": "blocked",
        "model_adequate": "blocked",
    }


def test_modeling_failure_contracts_remain_inside_model_ir_boundary(tmp_path: Path) -> None:
    from app.guidance.pipeline import _write_modeling_failure_contracts

    (tmp_path / "problem_spec.json").write_text(json.dumps({
        "task_summary": "Estimate two requested outputs.",
        "subproblems": [
            {
                "id": "problem1",
                "objective": "Estimate output one.",
                "required_fact_ids": [],
                "required_uncertainty_ids": [],
                "depends_on_subproblem_ids": [],
            },
            {
                "id": "problem2",
                "objective": "Estimate output two.",
                "required_fact_ids": [],
                "required_uncertainty_ids": [],
                "depends_on_subproblem_ids": [],
            },
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


def test_modeling_failure_contracts_archive_long_errors_without_breaking_fallback(
    tmp_path: Path,
) -> None:
    from app.guidance.pipeline import _write_modeling_failure_contracts

    (tmp_path / "problem_spec.json").write_text(json.dumps({
        "task_summary": "Estimate the requested output.",
        "subproblems": [
            {
                "id": "problem1",
                "objective": "Estimate output one.",
                "required_fact_ids": [],
                "required_uncertainty_ids": [],
                "depends_on_subproblem_ids": [],
            },
        ],
    }), encoding="utf-8")
    full_error = "fact coverage mismatch: " + "missing_fact, " * 200

    approved_path = _write_modeling_failure_contracts(
        tmp_path,
        task_id="task-long-modeling-error",
        input_path=tmp_path / "problem_spec.json",
        exc=ValueError(full_error),
    )

    failure = json.loads((tmp_path / "modeling_failure.json").read_text(encoding="utf-8"))
    approved = json.loads(approved_path.read_text(encoding="utf-8"))
    blocking_reason = approved["approved_model_ir"]["subproblems"][0]["blocking_reason"]
    assert failure["error"] == full_error
    assert len(blocking_reason) <= 600
    assert "modeling_failure.json" in blocking_reason
    assert approved["review_status"] == "blocked"


def test_modeling_failure_contracts_bound_objectives_for_brief_problem_specs(
    tmp_path: Path,
) -> None:
    from app.guidance.pipeline import GuidancePipeline
    from app.guidance.guidance_stage import GuidanceStage

    subproblems = [
        {
            "id": f"problem{index}",
            "title": f"Problem {index}",
            "brief_description": f"Optimize the requested outcome for case {index}.",
            "required_fact_ids": [],
            "required_uncertainty_ids": [],
            "depends_on_subproblem_ids": [],
        }
        for index in range(1, 6)
    ]
    problem_spec = {
        "task_summary": "A" * 10_000,
        "subproblems": subproblems,
    }

    class FailingModelingStage:
        async def run(self, **kwargs):
            raise ValueError("generated ModelIR did not satisfy the contract")

    calls: list[tuple[str, str | None]] = []
    pipeline = GuidancePipeline(
        decomposition_stage=RecordingStage("decomposition", calls, problem_spec),
        modeling_stage=FailingModelingStage(),
        model_review_stage=RecordingStage("model_review", calls),
        calculation_stage=RecordingStage("calculation", calls),
        result_verification_stage=RecordingStage("result_verification", calls),
        guidance_stage=GuidanceStage(),
    )

    messages = _run_pipeline(pipeline, "task-long-summary-failure", tmp_path)

    approved = json.loads(
        (tmp_path / "approved_model_ir.json").read_text(encoding="utf-8")
    )
    blocked = approved["approved_model_ir"]["subproblems"]
    assert [item["objective"] for item in blocked] == [
        item["brief_description"] for item in subproblems
    ]
    assert all(len(item["objective"]) <= 500 for item in blocked)
    report = json.loads(
        (tmp_path / "verification_report.json").read_text(encoding="utf-8")
    )
    assert report["execution_verified"]["status"] == "not_assessed"
    assert report["evidence_supported"]["status"] == "blocked"
    assert report["model_adequate"]["status"] == "blocked"
    assert (tmp_path / "guidance.md").is_file()
    result = next(item for item in messages if item["type"] == "result")
    assert result["data"]["status"] == "blocked"


def test_default_pipeline_uses_six_stages_timeout_budget_and_default_operators() -> None:
    from app.core.workflow_budget import resolve_workflow_budget
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
    budget = resolve_workflow_budget("standard").guidance
    assert [llm.request_timeout for llm in llms] == [
        budget.decomposition,
        budget.indexed_constraints,
        budget.review,
    ]
    modeler = pipeline._stages["modeling"].modeler
    assert (
        modeler.architecture_timeout,
        modeler.indexed_core_timeout,
        modeler.indexed_constraints_timeout,
    ) == (
        budget.architecture,
        budget.indexed_core,
        budget.indexed_constraints,
    )
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
