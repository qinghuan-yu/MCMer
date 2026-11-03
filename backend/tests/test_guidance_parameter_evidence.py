from copy import deepcopy

import pytest

from app.guidance.indexed_linear import validate_indexed_linear_core
from app.guidance.model_ir import IndexedLinearCoreSpec


SOURCE = "inputs/problem.docx#table-1"


def _core(values, source=SOURCE + "/unit_cost"):
    return IndexedLinearCoreSpec.model_validate({
        "index_sets": [{"id": "period", "values": ["p1", "p2"]}],
        "parameters": [{
            "id": "unit_cost", "index_sets": ["period"],
            "dense_values": values, "dense_source_ref": source,
            "source_fact_ids": ["fact.table"],
        }],
        "variables": [{"id": "buy", "index_sets": ["period"], "domain": "integer"}],
        "objective": {"sense": "minimize", "terms": [{
            "for_each": {"t": "period"},
            "coefficient": {"kind": "parameter", "parameter_id": "unit_cost", "selectors": [{"binder": "t"}]},
            "variable": {"variable_id": "buy", "selectors": [{"binder": "t"}]},
        }]},
    })


def _evidence():
    return {"evidence": [{
        "source_ref": SOURCE,
        "rows": [["period", "unit_cost", "demand"], ["p1", "2", "4"], ["p2", "3", "5"]],
    }]}


FACTS = [{"fact_id": "fact.table", "value": "registered_dataset", "source_refs": [SOURCE]}]


def test_static_parameter_follows_source_column_and_period_order():
    assert validate_indexed_linear_core(
        _core([2, 3]), locked_facts=FACTS, evidence_profile=_evidence(),
    ) == []


@pytest.mark.parametrize(("values", "source"), [
    ([3, 2], SOURCE + "/unit_cost"),
    ([4, 5], SOURCE + "/unit_cost"),
    ([2, 3], SOURCE + "/invented_column"),
    ([2, 3], "inputs/other.docx#table-1/unit_cost"),
    ([999, 999], "artifact:invented#/unit_cost"),
])
def test_static_parameter_rejects_wrong_mapping_and_unregistered_sources(values, source):
    issues = validate_indexed_linear_core(
        _core(values, source), locked_facts=FACTS, evidence_profile=_evidence(),
    )
    assert any(issue.code == "indexed_parameter_evidence_mismatch" for issue in issues)


def test_registered_source_without_numeric_content_cannot_verify_static_values():
    evidence = _evidence()
    evidence["evidence"][0]["rows"] = []
    issues = validate_indexed_linear_core(
        _core([999, 999]), locked_facts=FACTS, evidence_profile=evidence,
    )
    assert any(issue.code == "indexed_parameter_evidence_mismatch" for issue in issues)


def test_runtime_structure_validation_does_not_treat_placeholders_as_static_evidence():
    payload = deepcopy(_core([2, 3]).model_dump())
    payload["parameters"] = [{
        "id": "unit_cost", "index_sets": ["period"],
        "runtime_source": {"artifact_ref": "forecast", "field_path": ["predictions"]},
    }]
    assert validate_indexed_linear_core(
        IndexedLinearCoreSpec.model_validate(payload),
        locked_facts=[], evidence_profile={"evidence": []},
    ) == []


def test_table_slice_matches_index_labels_when_domain_order_changes():
    core = _core([3, 2])
    core.index_sets[0].values = ["p2", "p1"]
    assert validate_indexed_linear_core(
        core, locked_facts=FACTS, evidence_profile=_evidence(),
    ) == []


def test_explicit_cell_must_agree_with_the_parameter_coordinate():
    payload = _core([2, 3]).model_dump()
    payload["parameters"][0] = {
        "id": "unit_cost", "index_sets": ["period"], "source_fact_ids": ["fact.table"],
        "values": [
            {"coordinates": ["p1"], "value": 3, "source_ref": SOURCE + "/rows/2/cells/1"},
            {"coordinates": ["p2"], "value": 2, "source_ref": SOURCE + "/rows/1/cells/1"},
        ],
    }
    issues = validate_indexed_linear_core(
        IndexedLinearCoreSpec.model_validate(payload),
        locked_facts=FACTS, evidence_profile=_evidence(),
    )
    assert any(issue.code == "indexed_parameter_evidence_mismatch" for issue in issues)


def test_numeric_index_ids_match_text_cells_from_document_tables():
    core = _core([2, 3])
    core.index_sets[0].values = [1, 2]
    evidence = _evidence()
    evidence["evidence"][0]["rows"][1][0] = "1"
    evidence["evidence"][0]["rows"][2][0] = "2"
    assert validate_indexed_linear_core(
        core, locked_facts=FACTS, evidence_profile=evidence,
    ) == []
