import pytest
from pydantic import ValidationError


def test_result_registry_rejects_duplicate_ids_across_verified_and_blocked() -> None:
    from app.guidance.artifacts import ResultRegistry

    with pytest.raises(ValidationError, match="duplicate result id"):
        ResultRegistry.model_validate({
            "verified_results": [{
                "id": "problem1",
                "result_type": "regression",
                "claim_text": "problem1 has a verified regression result",
                "metrics": {"rmse": 0.0},
                "source_data": ["data/attachment.xlsx"],
            }],
            "blocked_results": [{
                "id": "problem1",
                "message": "problem1 failed later",
                "reason": "exception after appending verified result",
                "source_data": ["data/attachment.xlsx"],
            }],
        })


def test_result_registry_rejects_summary_that_cannot_render_final_answers() -> None:
    from app.guidance.artifacts import ResultRegistry

    with pytest.raises(ValidationError):
        ResultRegistry.model_validate({
            "verified_results": [{
                "id": "result_prediction",
                "result_type": "forecast",
                "claim_text": "Forecast next-period demand.",
                "metrics": {"forecast_value": 208.0},
                "source_data": ["data/forecast.xlsx"],
            }],
            "blocked_results": [],
            "summary": {
                "verified_count": 1,
                "blocked_count": 0,
                "coverage_status": "verified",
                "reader_decision_guide": ["Observed demand is known; next-period demand is unknown."],
                "method_route_comparison": [],
                "identifiability_analysis": {},
                "claim_registry": [{
                    "claim": "Forecast next-period demand.",
                    "status": "evidence_verified",
                    "evidence": ["result_prediction"],
                }],
                "final_answer_tables": [{
                    "table": "next-period forecast",
                    "status": "evidence_verified",
                    "result_id": "result_prediction",
                }],
            },
        })


def test_problem_spec_assigns_stable_ids_to_blank_subproblems() -> None:
    from app.guidance.contracts import ProblemSpec

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
