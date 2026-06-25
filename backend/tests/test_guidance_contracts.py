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
