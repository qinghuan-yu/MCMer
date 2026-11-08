from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.guidance.model_ir_drafts import IndexedLinearCoreDraft
from app.guidance.parameter_evidence import validate_static_parameter_evidence


def _core(binding, *, periods=(1, 2), source_fact_ids=()):
    return IndexedLinearCoreDraft.model_validate({
        "index_sets": [{"id": "period", "values": list(periods)}],
        "parameters": [{
            "source_kind": "static", "id": "demand", "index_sets": ["period"],
            "source_fact_ids": list(source_fact_ids), "binding": binding,
        }],
    })


def test_fact_binding_materializes_values_and_provenance_without_llm_numbers():
    facts = [{"fact_id": "F1", "value": 12, "unit": "unit", "source_refs": ["input.docx#p1"]}]
    original = deepcopy(facts)
    core = _core({"kind": "fact", "fact_id": "F1"}).promote_core(locked_facts=facts)

    parameter = core.parameters[0]
    assert parameter.dense_values == []
    assert [value.value for value in parameter.values] == [12, 12]
    assert [value.coordinates for value in parameter.values] == [[1], [2]]
    assert [value.source_ref for value in parameter.values] == ["problem_spec.json#locked_facts/F1"] * 2
    assert parameter.source_fact_ids == ["F1"]
    assert facts == original
    assert validate_static_parameter_evidence(core, locked_facts=facts, evidence_profile={}) == []


def _profile(rows, source_ref="input.docx#table-1"):
    return {"evidence": [{
        "evidence_id": "table1", "kind": "embedded_table", "source_ref": source_ref,
        "source_file": "input.docx", "labels": [], "sha256": "a" * 64,
        "size_bytes": 10, "rows": rows, "profile": {},
    }]}


def _range(**overrides):
    return {
        "kind": "table_range", "source_ref": "input.docx#table-1",
        "row_start": 0, "row_stop": 2, "column_start": 1, "column_stop": 3,
        "order": "row_major", **overrides,
    }


@pytest.mark.parametrize(("order", "expected", "cells"), [
    ("row_major", [11, 5, 4, 7], [(0, 1), (0, 2), (1, 1), (1, 2)]),
    ("column_major", [11, 4, 5, 7], [(0, 1), (1, 1), (0, 2), (1, 2)]),
])
def test_wide_table_materializes_explicit_order_and_exact_cell_provenance(order, expected, cells):
    profile = _profile([["first", "11", "5"], ["second", "4", "7"]])
    core = _core(_range(order=order), periods=(1, 2, 3, 4)).promote_core(evidence_profile=profile)
    assert [item.value for item in core.parameters[0].values] == expected
    assert [item.source_ref for item in core.parameters[0].values] == [
        f"input.docx#table-1/rows/{row}/cells/{column}" for row, column in cells
    ]
    assert validate_static_parameter_evidence(core, locked_facts=[], evidence_profile=profile) == []
    core.parameters[0].values[0].value += 1
    assert validate_static_parameter_evidence(core, locked_facts=[], evidence_profile=profile)


def test_named_column_uses_index_labels_to_order_values():
    profile = _profile([["period", "demand"], [2, 17], [1, 9]])
    core = _core({"kind": "table_column", "source_ref": "input.docx#table-1", "column": "demand"}).promote_core(evidence_profile=profile)
    assert [item.value for item in core.parameters[0].values] == [9, 17]
    assert core.parameters[0].values[0].source_ref.endswith("/rows/2/cells/1")
    assert validate_static_parameter_evidence(core, locked_facts=[], evidence_profile=profile) == []
    core.parameters[0].values[0].source_ref = "input.docx#table-1/rows/1/cells/1"
    assert "index labels" in " ".join(validate_static_parameter_evidence(core, locked_facts=[], evidence_profile=profile))


@pytest.mark.parametrize(("raw", "unit", "expected"), [("200元/个", "元/个", 200), ("20%", "%", 0.2)])
def test_explicit_cell_unit_is_preserved_and_verified(raw, unit, expected):
    profile = _profile([["cost", raw]])
    core = _core(_range(row_stop=1, column_stop=2, unit=unit), periods=(1,)).promote_core(evidence_profile=profile)
    value = core.parameters[0].values[0]
    assert value.value == expected
    assert value.source_unit == unit
    assert validate_static_parameter_evidence(core, locked_facts=[], evidence_profile=profile) == []


def test_binding_cannot_invent_percent_unit_for_a_bare_number():
    profile = _profile([["rate", 20]])
    with pytest.raises(ValueError, match="unit"):
        _core(_range(row_stop=1, column_stop=2, unit="%"), periods=(1,)).promote_core(evidence_profile=profile)


def test_compound_unit_containing_percent_keeps_its_original_scale():
    profile = _profile([["cost_per_percentage_point", "20元/%"]])
    core = _core(_range(row_stop=1, column_stop=2, unit="元/%"), periods=(1,)).promote_core(evidence_profile=profile)
    assert core.parameters[0].values[0].value == 20
    assert validate_static_parameter_evidence(core, locked_facts=[], evidence_profile=profile) == []


@pytest.mark.parametrize(("overrides", "message"), [
    ({"source_ref": "other.docx#table-1"}, "unknown registered table"),
    ({"source_ref": "input.docx#table-1/rows/0/cells/1"}, "unknown registered table"),
    ({"row_stop": 3}, "bounds"),
    ({"column_stop": 4}, "bounds"),
    ({"row_stop": 1}, "cells for"),
    ({"column_start": 0, "column_stop": 2}, "unit"),
    ({"unit": "USD"}, "unit"),
])
def test_invalid_table_binding_is_rejected_instead_of_guessing(overrides, message):
    profile = _profile([["a", "11元", "5元"], ["b", "4元", "7元"]])
    binding = _range(**{"unit": "元", **overrides})
    with pytest.raises(ValueError, match=message):
        _core(binding, periods=(1, 2, 3, 4)).promote_core(evidence_profile=profile)


def test_duplicate_registered_table_is_ambiguous():
    profile = _profile([["a", 11, 5], ["b", 4, 7]])
    profile["evidence"].append(deepcopy(profile["evidence"][0]))
    with pytest.raises(ValueError, match="ambiguous"):
        _core(_range(), periods=(1, 2, 3, 4)).promote_core(evidence_profile=profile)


def test_raw_static_values_are_not_accepted_from_generation():
    payload = _core({"kind": "fact", "fact_id": "F1"}).model_dump(mode="json")
    payload["parameters"][0]["dense_values"] = [999, 999]
    with pytest.raises(ValueError, match="Extra inputs"):
        IndexedLinearCoreDraft.model_validate(payload)


def test_fact_binding_rejects_facts_outside_provided_scope():
    with pytest.raises(ValueError, match="static source"):
        _core({"kind": "fact", "fact_id": "foreign"}).promote_core(locked_facts=[])


@pytest.mark.parametrize("weeks", [8, 104])
def test_original_attachment_demand_materializes_without_manual_values(weeks):
    fixture = json.loads((Path(__file__).parent / "fixtures/static_bindings/original_attachment_tables.json").read_text(encoding="utf-8"))
    profile = _profile(fixture["demand_rows"], fixture["demand_source_ref"])
    core = _core(_range(
        source_ref=fixture["demand_source_ref"], row_stop=weeks // 8, column_stop=9,
    ), periods=range(1, weeks + 1)).promote_core(evidence_profile=profile)
    values = [item.value for item in core.parameters[0].values]
    assert values[:8] == fixture["expected"]["first_eight"]
    assert len(values) == weeks
    if weeks == 104:
        assert values[-8:] == fixture["expected"]["last_eight"]
        assert sum(values) == fixture["expected"]["demand_sum"]
    assert validate_static_parameter_evidence(core, locked_facts=[], evidence_profile=profile) == []


def test_original_attachment_costs_retain_exact_units_and_cells():
    fixture = json.loads((Path(__file__).parent / "fixtures/static_bindings/original_attachment_tables.json").read_text(encoding="utf-8"))
    profile = _profile(fixture["cost_rows"], fixture["cost_source_ref"])
    costs = []
    for row, unit in enumerate(["元/个", "元/个", "元/个/周", "元/个/周", "元/个"], start=1):
        core = _core(_range(
            source_ref=fixture["cost_source_ref"], row_start=row, row_stop=row + 1,
            column_stop=2, unit=unit,
        ), periods=(1,)).promote_core(evidence_profile=profile)
        costs.append(core.parameters[0].values[0].value)
        assert validate_static_parameter_evidence(core, locked_facts=[], evidence_profile=profile) == []
    assert costs == fixture["expected"]["cost_values"]
