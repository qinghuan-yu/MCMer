"""结构需求义务：ProblemSpec 声明封闭类型需求，索引约束必须按结构履行（计划 F2b）。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.guidance.contracts import (
    ProblemFactsGenerationDraft,
    ProblemSpec,
    ProblemStructureGenerationDraft,
)
from app.guidance.model_ir import ModelIR
from app.guidance.model_ir_drafts import IndexedLinearConstraintDraft
from app.guidance.requirement_coverage import validate_requirement_coverage

FIXTURES = Path(__file__).parent / "fixtures/model_ir"
SUBPROBLEM = "finite_inventory_policy"


def _spec_payload() -> dict:
    return json.loads(
        (FIXTURES / "generic_multiperiod_inventory_problem_spec.json").read_text(encoding="utf-8")
    )


def _model_payload() -> dict:
    return json.loads(
        (FIXTURES / "generic_multiperiod_inventory.json").read_text(encoding="utf-8")
    )


def _constraints(payload: dict) -> dict[str, dict]:
    model = payload["subproblems"][0]["execution_components"][0]["model"]
    return {constraint["id"]: constraint for constraint in model["constraints"]}


def _drop(payload: dict, *constraint_ids: str) -> dict:
    model = payload["subproblems"][0]["execution_components"][0]["model"]
    model["constraints"] = [
        constraint for constraint in model["constraints"] if constraint["id"] not in constraint_ids
    ]
    return payload


def _errors(model_payload: dict, spec_payload: dict | None = None) -> str:
    try:
        validate_requirement_coverage(
            ProblemSpec.model_validate(spec_payload or _spec_payload()),
            ModelIR.model_validate(model_payload),
        )
    except ValueError as exc:
        return str(exc)
    return ""


# ---- ProblemSpec contract -------------------------------------------------

def test_problem_spec_accepts_typed_modeling_requirements() -> None:
    spec = ProblemSpec.model_validate(_spec_payload())
    assert [item.kind for item in spec.modeling_requirements] == [
        "state_conservation", "lagged_link", "demand_satisfaction",
        "upper_capacity", "upper_capacity",
    ]
    assert spec.modeling_requirements[1].lag == 1


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda r: r[1].pop("lag"), "lagged_link requires lag"),
        (lambda r: r[0].update(lag=1), "lag is only valid for lagged_link"),
        (lambda r: r[0].update(kind="free_text_rule"), "kind"),
        (lambda r: r[0].update(subproblem_ids=["problem9"]), "unknown subproblem"),
        (lambda r: r[0].update(source_fact_ids=["fact.missing"]), "unknown locked fact"),
        (lambda r: r[1].update(requirement_id=r[0]["requirement_id"]), "requirement ids must be unique"),
        (lambda r: r[0].update(source_refs=[]), "source_refs"),
    ],
)
def test_problem_spec_rejects_malformed_requirements(mutate, message: str) -> None:
    payload = _spec_payload()
    mutate(payload["modeling_requirements"])
    with pytest.raises(ValidationError, match=message):
        ProblemSpec.model_validate(payload)


def test_fact_draft_promotion_maps_requirement_fact_and_scope_ids() -> None:
    structure = ProblemStructureGenerationDraft.model_validate({
        "task_summary": "Capacity planning.",
        "subproblems": [{"id": "Q1", "depends_on_subproblem_ids": []}],
    })
    facts = ProblemFactsGenerationDraft.model_validate({
        "locked_facts": [{
            "fact_id": "capacity limit",
            "statement": "Capacity is 20 units.",
            "source_refs": ["input_catalog.json#paragraph-1"],
            "subproblem_ids": ["Q1"],
            "semantic_key": "capacity",
            "value": 20,
            "unit": "unit",
        }],
        "modeling_requirements": [{
            "requirement_id": "capacity rule",
            "kind": "upper_capacity",
            "statement": "Purchases cannot exceed capacity.",
            "subproblem_ids": ["Q1"],
            "source_refs": ["input_catalog.json#paragraph-1"],
            "source_fact_ids": ["capacity limit"],
        }],
    })
    spec = structure.promote(facts)
    requirement = spec.modeling_requirements[0]
    assert requirement.subproblem_ids == ["problem1"]
    assert requirement.source_fact_ids == [spec.locked_facts[0].fact_id]
    assert re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", requirement.requirement_id)


# ---- ModelIR contract -----------------------------------------------------

def test_constraint_draft_carries_fulfilled_requirements_to_canonical() -> None:
    draft = IndexedLinearConstraintDraft.model_validate({
        "id": "cap",
        "fulfills_requirement_ids": ["req.tier_capacity"],
        "terms": [{
            "coefficient": {"kind": "constant", "value": 1.0},
            "variable": {"variable_id": "x", "selectors": []},
        }],
        "relation": "less_equal",
        "rhs": {"kind": "parameter", "parameter_id": "cap", "selectors": []},
    })
    assert draft.promote().fulfills_requirement_ids == ["req.tier_capacity"]


# ---- structural coverage --------------------------------------------------

def test_complete_inventory_model_fulfills_every_requirement() -> None:
    assert _errors(_model_payload()) == ""


@pytest.mark.parametrize(
    ("removed", "requirement_id"),
    [
        (("initial_balance", "state_balance"), "req.stock_conservation"),
        (("commissioning_delay",), "req.commissioning_lag"),
        (("initial_commissioned",), "req.commissioning_lag"),
        (("tier_capacity",), "req.tier_capacity"),
        (("setup_capacity",), "req.setup_capacity"),
    ],
)
def test_removing_a_fulfilling_constraint_reports_the_requirement(
    removed: tuple[str, ...], requirement_id: str,
) -> None:
    assert requirement_id in _errors(_drop(_model_payload(), *removed))


def test_lag_offset_must_equal_declared_lag() -> None:
    payload = _model_payload()
    delay = _constraints(payload)["commissioning_delay"]
    delay["terms"][1]["variable"]["selectors"][0]["offset"] = -2
    assert "req.commissioning_lag" in _errors(payload)


def test_conservation_requires_opposite_signs_on_consecutive_states() -> None:
    payload = _model_payload()
    _constraints(payload)["state_balance"]["terms"][1]["coefficient"]["value"] = 1.0
    assert "req.stock_conservation" in _errors(payload)


def test_capacity_must_be_an_upper_bound_on_a_parameter() -> None:
    payload = _model_payload()
    _constraints(payload)["tier_capacity"]["relation"] = "greater_equal"
    assert "req.tier_capacity" in _errors(payload)

    payload = _model_payload()
    _constraints(payload)["tier_capacity"]["rhs"] = {"kind": "constant", "value": 2.0}
    assert "req.tier_capacity" in _errors(payload)


def test_demand_must_be_met_with_equality_or_lower_bound() -> None:
    payload = _model_payload()
    for constraint_id in ("initial_balance", "state_balance"):
        _constraints(payload)[constraint_id]["relation"] = "less_equal"
    assert "req.demand" in _errors(payload)


def test_unknown_fulfilled_requirement_id_is_rejected() -> None:
    payload = _model_payload()
    _constraints(payload)["fix_loss"]["fulfills_requirement_ids"] = ["req.invented"]
    assert "req.invented" in _errors(payload)


def test_requirement_bound_to_facts_needs_a_parameter_from_those_facts() -> None:
    spec = _spec_payload()
    spec["locked_facts"] = [{
        "fact_id": "fact.tier_capacity",
        "statement": "Tier one is limited to 2 units per period.",
        "source_refs": ["input_catalog.json#table-2"],
        "subproblem_ids": [SUBPROBLEM],
        "semantic_key": "capacity.tier_one",
        "value": 2.0,
        "unit": "unit",
    }]
    spec["subproblems"][0]["required_fact_ids"] = ["fact.tier_capacity"]
    spec["modeling_requirements"][3]["source_fact_ids"] = ["fact.tier_capacity"]
    payload = _model_payload()
    assert "req.tier_capacity" in _errors(payload, spec)

    model = payload["subproblems"][0]["execution_components"][0]["model"]
    tier = next(item for item in model["parameters"] if item["id"] == "tier_capacity")
    tier["source_fact_ids"] = ["fact.tier_capacity"]
    assert "req.tier_capacity" not in _errors(payload, spec)
