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
