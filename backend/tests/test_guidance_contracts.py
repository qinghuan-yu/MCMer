import pytest
from pydantic import ValidationError


def test_model_spec_rejects_llm_owned_execution_outputs() -> None:
    from app.guidance.contracts import ModelSpec

    with pytest.raises(ValidationError):
        ModelSpec(
            model_id="traffic-flow",
            selected_method="constrained least squares",
            required_outputs=["表1.1 问题1支路车流量函数表达式"],
        )


def test_execution_required_outputs_excludes_planned_figures() -> None:
    from app.guidance.contracts import CORE_EXECUTION_OUTPUTS, ModelSpec, execution_required_outputs

    model_spec = ModelSpec(
        model_id="figure-planning-model",
        selected_method="structured modeling",
        candidate_methods=[
            {
                "name": "structured modeling",
                "selected": True,
                "reason": "separates hard execution artifacts from planned figures",
            }
        ],
        subproblem_models=[
            {
                "subproblem_id": "problem1",
                "objective": "Model task 1.",
                "selected_method": "structured modeling",
                "rationale": "The task has a concrete model.",
                "equations": ["y = f(x)"],
                "parameters": [
                    {
                        "parameter_id": "theta_1",
                        "symbol": "theta_1",
                        "meaning": "task parameter",
                        "unit": "dimensionless",
                        "source_type": "assumption",
                        "source_detail": "problem statement",
                        "estimation_method": "declared assumption",
                        "constraints": ["finite"],
                    }
                ],
                "constraints": ["finite parameter values"],
                "solve_steps": ["Build the model.", "Compute the result."],
                "expected_results": ["task result"],
            }
        ],
        figure_requests=[
            {"path": "figures/problem1_fit.png", "role": "fit"},
            {"path": "figures/problem2_diagnostics.png", "role": "diagnostic"},
        ],
    )

    assert execution_required_outputs(model_spec) == list(CORE_EXECUTION_OUTPUTS)


def test_result_registry_rejects_duplicate_ids_across_verified_and_blocked() -> None:
    from app.guidance.artifacts import ResultRegistry

    with pytest.raises(ValidationError, match="duplicate result id"):
        ResultRegistry.model_validate(
            {
                "verified_results": [
                    {
                        "id": "problem1",
                        "result_type": "regression",
                        "claim_text": "problem1 has a verified regression result",
                        "metrics": {"rmse": 0.0},
                        "source_data": ["data/attachment.xlsx"],
                    }
                ],
                "blocked_results": [
                    {
                        "id": "problem1",
                        "message": "problem1 failed later",
                        "reason": "exception after appending verified result",
                        "source_data": ["data/attachment.xlsx"],
                    }
                ],
            }
        )


def test_result_registry_rejects_reader_summary_that_cannot_render_final_answers() -> None:
    from app.guidance.artifacts import ResultRegistry

    with pytest.raises(ValidationError):
        ResultRegistry.model_validate(
            {
                "verified_results": [
                    {
                        "id": "result_prediction",
                        "result_type": "forecast",
                        "claim_text": "Forecast next-period demand.",
                        "metrics": {"forecast_value": 208.0},
                        "source_data": ["data/forecast.xlsx"],
                    }
                ],
                "blocked_results": [],
                "summary": {
                    "verified_count": 1,
                    "blocked_count": 0,
                    "coverage_status": "verified",
                    "reader_decision_guide": [
                        "Observed demand is known; next-period demand is unknown."
                    ],
                    "method_route_comparison": [],
                    "identifiability_analysis": {},
                    "claim_registry": [
                        {
                            "claim": "Forecast next-period demand.",
                            "status": "evidence_verified",
                            "evidence": ["result_prediction"],
                        }
                    ],
                    "final_answer_tables": [
                        {
                            "table": "next-period forecast",
                            "status": "evidence_verified",
                            "result_id": "result_prediction",
                        }
                    ],
                },
            }
        )


def test_model_spec_requires_executable_spec_for_each_subproblem() -> None:
    from app.guidance.contracts import ModelSpec

    with pytest.raises(ValidationError):
        ModelSpec(
            model_id="traffic-flow",
            selected_method="constrained least squares",
        )


def test_candidate_method_contract_requires_named_object() -> None:
    from app.guidance.contracts import CandidateMethodSpec

    candidate = CandidateMethodSpec(
        name="constrained least squares",
        selected=True,
        reason="respects the physical constraints",
    )

    assert candidate.name == "constrained least squares"
    with pytest.raises(ValidationError):
        CandidateMethodSpec.model_validate("least squares")
    with pytest.raises(ValidationError):
        CandidateMethodSpec.model_validate({})


def test_problem_spec_assigns_stable_ids_to_blank_subproblems_for_coverage() -> None:
    from app.guidance.contracts import ModelSpec, ProblemSpec, validate_subproblem_coverage

    problem_spec = ProblemSpec(
        task_summary="Solve five decomposed modeling tasks.",
        subproblems=[
            {"objective": "Analyze the first task."},
            {"id": "", "objective": "Analyze the second task."},
            {"id": "   ", "objective": "Analyze the third task."},
            {"id": None, "objective": "Analyze the fourth task."},
            {"id": "problem5", "objective": "Analyze the fifth task."},
        ],
    )
    model_spec = ModelSpec(
        model_id="five-task-model",
        selected_method="structured modeling",
        candidate_methods=[
            {
                "name": "structured modeling",
                "selected": True,
                "reason": "covers every decomposed task",
            }
        ],
        subproblem_models=[
            {
                "subproblem_id": f"problem{index}",
                "objective": f"Model task {index}.",
                "selected_method": "structured modeling",
                "rationale": "Each task receives a concrete model.",
                "equations": ["y = f(x)"],
                "parameters": [
                    {
                        "parameter_id": f"theta_{index}",
                        "symbol": f"theta_{index}",
                        "meaning": "task parameter",
                        "unit": "dimensionless",
                        "source_type": "assumption",
                        "source_detail": "problem statement",
                        "estimation_method": "declared assumption",
                        "constraints": ["finite"],
                    }
                ],
                "constraints": ["finite parameter values"],
                "solve_steps": ["Build the model.", "Compute the result."],
                "expected_results": ["task result"],
            }
            for index in range(1, 6)
        ],
    )

    validate_subproblem_coverage(problem_spec, model_spec)
    assert [item["id"] for item in problem_spec.subproblems] == [
        "problem1",
        "problem2",
        "problem3",
        "problem4",
        "problem5",
    ]


def test_problem_spec_canonicalizes_prefixed_subproblem_ids() -> None:
    from app.guidance.contracts import ProblemSpec

    problem_spec = ProblemSpec(
        task_summary="Solve decomposed modeling tasks.",
        subproblems=[
            {"id": "P1", "objective": "Analyze task 1."},
            {"id": "problem 2", "objective": "Analyze task 2."},
            {"id": "subproblem_3", "objective": "Analyze task 3."},
        ],
    )

    assert [item["id"] for item in problem_spec.subproblems] == [
        "problem1",
        "problem2",
        "problem3",
    ]


def test_model_spec_canonicalizes_numeric_subproblem_ids_for_coverage() -> None:
    from app.guidance.contracts import ModelSpec, ProblemSpec, validate_subproblem_coverage

    problem_spec = ProblemSpec(
        task_summary="Solve five decomposed modeling tasks.",
        subproblems=[
            {"id": f"problem{index}", "objective": f"Analyze task {index}."}
            for index in range(1, 6)
        ],
    )
    model_spec = ModelSpec(
        model_id="numeric-subproblem-id-model",
        selected_method="structured modeling",
        candidate_methods=[
            {
                "name": "structured modeling",
                "selected": True,
                "reason": "covers every decomposed task",
            }
        ],
        subproblem_models=[
            {
                "subproblem_id": str(index),
                "objective": f"Model task {index}.",
                "selected_method": "structured modeling",
                "rationale": "Each task receives a concrete model.",
                "equations": ["y = f(x)"],
                "parameters": [
                    {
                        "parameter_id": f"theta_{index}",
                        "symbol": f"theta_{index}",
                        "meaning": "task parameter",
                        "unit": "dimensionless",
                        "source_type": "assumption",
                        "source_detail": "problem statement",
                        "estimation_method": "declared assumption",
                        "constraints": ["finite"],
                    }
                ],
                "constraints": ["finite parameter values"],
                "solve_steps": ["Build the model.", "Compute the result."],
                "expected_results": ["task result"],
            }
            for index in range(1, 6)
        ],
    )

    validate_subproblem_coverage(problem_spec, model_spec)
    assert [item.subproblem_id for item in model_spec.subproblem_models] == [
        "problem1",
        "problem2",
        "problem3",
        "problem4",
        "problem5",
    ]


def test_model_spec_canonicalizes_prefixed_subproblem_ids_for_coverage() -> None:
    from app.guidance.contracts import ModelSpec, ProblemSpec, validate_subproblem_coverage

    problem_spec = ProblemSpec(
        task_summary="Solve five decomposed modeling tasks.",
        subproblems=[
            {"id": f"problem{index}", "objective": f"Analyze task {index}."}
            for index in range(1, 6)
        ],
    )
    model_spec = ModelSpec(
        model_id="prefixed-subproblem-id-model",
        selected_method="structured modeling",
        candidate_methods=[
            {
                "name": "structured modeling",
                "selected": True,
                "reason": "covers every decomposed task",
            }
        ],
        subproblem_models=[
            {
                "subproblem_id": f"P{index}",
                "objective": f"Model task {index}.",
                "selected_method": "structured modeling",
                "rationale": "Each task receives a concrete model.",
                "equations": ["y = f(x)"],
                "parameters": [
                    {
                        "parameter_id": f"theta_{index}",
                        "symbol": f"theta_{index}",
                        "meaning": "task parameter",
                        "unit": "dimensionless",
                        "source_type": "assumption",
                        "source_detail": "problem statement",
                        "estimation_method": "declared assumption",
                        "constraints": ["finite"],
                    }
                ],
                "constraints": ["finite parameter values"],
                "solve_steps": ["Build the model.", "Compute the result."],
                "expected_results": ["task result"],
            }
            for index in range(1, 6)
        ],
    )

    validate_subproblem_coverage(problem_spec, model_spec)
    assert [item.subproblem_id for item in model_spec.subproblem_models] == [
        "problem1",
        "problem2",
        "problem3",
        "problem4",
        "problem5",
    ]
