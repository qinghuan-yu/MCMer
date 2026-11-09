import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from jsonschema import Draft202012Validator
import pytest

from app.guidance.contract_request import StructuredContractRequester
from app.guidance.contracts import ProblemSpec
from app.guidance.model_ir import ModelIR
from app.guidance.model_review_contracts import (
    MathematicalCorrespondenceCheck, RevisionRequirementSpec, SubproblemReviewSpec,
)
from app.guidance.model_review_validation import validate_subproblem_review


def _requirement(reference):
    return {
        "requirement_id": "cost_scope", "kind": "model", "basis_refs": [reference],
        "description": "Check the cost scope.",
        "acceptance_criteria": "Use the registered cost scope.",
    }


def _check(path):
    return {
        "requirement_id": "$formulation", "status": "passed", "model_paths": [path],
        "rationale": "The explicit constraint implements the required relation.",
    }


@pytest.mark.parametrize("reference", [
    "$objective", "/execution_components/0/model/objective",
    "file://problem_spec.json#locked_facts/F1", "problem_spec.json#locked_facts/F1/extra",
])
def test_advertised_basis_schema_rejects_non_problem_record_references(reference):
    value = _requirement(reference)
    schema = Draft202012Validator(RevisionRequirementSpec.model_json_schema())

    assert not schema.is_valid(value)
    with pytest.raises(ValueError):
        RevisionRequirementSpec.model_validate(value)


@pytest.mark.parametrize("reference", [
    "problem_spec.json#uncertainties/UNC-001", "problem_spec.json#locked_facts/F_cost.1",
])
def test_advertised_basis_schema_preserves_valid_record_references(reference):
    value = _requirement(reference)
    Draft202012Validator(RevisionRequirementSpec.model_json_schema()).validate(value)
    assert RevisionRequirementSpec.model_validate(value).basis_refs == [reference]


@pytest.mark.parametrize("path", [
    "/candidate_models", "/data/0", "/subproblems/0/execution_components/0", "/objective",
])
def test_advertised_math_schema_rejects_non_computational_roots(path):
    value = _check(path)
    schema = Draft202012Validator(MathematicalCorrespondenceCheck.model_json_schema())

    assert not schema.is_valid(value)
    with pytest.raises(ValueError):
        MathematicalCorrespondenceCheck.model_validate(value)


@pytest.mark.parametrize("path", [
    "/execution_components/0/model/constraints/0", "/equations/0", "/constraints/0", "/assumptions/0",
])
def test_advertised_math_schema_preserves_existing_computational_roots(path):
    value = _check(path)
    Draft202012Validator(MathematicalCorrespondenceCheck.model_json_schema()).validate(value)
    assert MathematicalCorrespondenceCheck.model_validate(value).model_paths == [path]


def test_reference_types_preserve_existing_whitespace_normalization():
    requirement = RevisionRequirementSpec.model_validate(_requirement("  problem_spec.json#locked_facts/F1\n"))
    check = MathematicalCorrespondenceCheck.model_validate(_check(" /execution_components/0/model/objective\n"))
    assert requirement.basis_refs == ["problem_spec.json#locked_facts/F1"]
    assert check.model_paths == ["/execution_components/0/model/objective"]


def test_initial_and_regeneration_requests_publish_the_same_reference_rules():
    first = {
        "subproblem_id": "inventory", "status": "revision_required",
        "required_revisions": [_requirement("$objective")],
    }
    valid = {
        **first,
        "required_revisions": [_requirement("problem_spec.json#locked_facts/F1")],
        "mathematical_checks": [_check("/execution_components/0/model/constraints/0")],
    }

    class LLM:
        max_tokens = 2000
        calls = []

        async def chat(self, **kwargs):
            self.calls.append(kwargs)
            response = first if len(self.calls) == 1 else valid
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(response)), finish_reason="stop",
            )])

    llm = LLM()
    requester = StructuredContractRequester(
        llm=llm, model_type=SubproblemReviewSpec,
        system_prompt="Review the supplied model.", agent_name="ReferenceSchemaProbe",
    )
    result = asyncio.run(requester.request({"target_subproblem_id": "inventory"}))

    assert result == SubproblemReviewSpec.model_validate(valid)
    assert len(llm.calls) == 2
    initial_schema = json.loads(llm.calls[0]["history"][0]["content"].split("目标 JSON Schema：", 1)[1])
    retry_schema = json.loads(llm.calls[1]["history"][1]["content"])["target_schema"]
    invalid_math = {**valid, "mathematical_checks": [_check("/candidate_models")]}
    for schema in (initial_schema, retry_schema):
        validator = Draft202012Validator(schema)
        validator.validate(valid)
        assert not validator.is_valid(first)
        assert not validator.is_valid(invalid_math)


@pytest.mark.parametrize("failure", ["missing_element", "other_scope"])
def test_reference_syntax_does_not_replace_dynamic_evidence_checks(failure):
    model = ModelIR.model_validate_json(
        (Path(__file__).parent / "fixtures/model_ir/generic_multiperiod_inventory.json").read_text(encoding="utf-8")
    ).subproblems[0]
    spec = ProblemSpec.model_validate({
        "task_summary": "Keep source records scoped and mathematical evidence concrete.",
        "subproblems": [
            {"id": model.subproblem_id, "required_fact_ids": [], "required_uncertainty_ids": [],
             "depends_on_subproblem_ids": []},
            {"id": "other", "required_fact_ids": ["F_other"], "required_uncertainty_ids": [],
             "depends_on_subproblem_ids": []},
        ],
        "locked_facts": [{
            "fact_id": "F_other", "statement": "The other subproblem has a cost.",
            "source_refs": ["question.txt#paragraph-1"], "subproblem_ids": ["other"],
            "semantic_key": "other_cost", "value": 1, "unit": "currency",
        }],
    })
    value = {
        "subproblem_id": model.subproblem_id, "status": "revision_required",
        "required_revisions": [{**_requirement("problem_spec.json#locked_facts/F_other"),
                                "basis_refs": [] if failure == "missing_element" else ["problem_spec.json#locked_facts/F_other"]}],
        "mathematical_checks": [_check("/execution_components/0/model/constraints/999999")]
        if failure == "missing_element" else [],
    }
    Draft202012Validator(SubproblemReviewSpec.model_json_schema()).validate(value)
    decision = SubproblemReviewSpec.model_validate(value)
    with pytest.raises(ValueError, match="does not exist|outside the subproblem scope"):
        validate_subproblem_review(decision, model, problem_spec=spec)
