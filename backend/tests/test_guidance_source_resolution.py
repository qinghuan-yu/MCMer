from copy import deepcopy
import hashlib

import pytest
from pydantic import ValidationError

from app.guidance.contracts import ProblemSpec
from app.guidance.recovery import canonical_digest


PARAGRAPH = (
    "工作模块使用一期后，下一期必须保养，完成这一期保养后方可重新服务或指导。"
    "其他未服务也未指导的熟练模块同样保养。指导过的模块下一期可以直接使用。"
    "载具使用后可连续使用，不使用时保养。无损毁，无期末残值。"
    "检查中的载具、新模块以及指导者均不另计保养费，训练成本同时对新模块和指导者计收。"
)
QUOTE = "训练成本同时对新模块和指导者计收。"
SOURCE = "question.txt#paragraph-3"


def _case(tmp_path):
    source = tmp_path / "question.txt"
    source.write_text(PARAGRAPH, encoding="utf-8")
    csv = tmp_path / "cost.csv"
    csv.write_text("item,cost\ntraining,8\n", encoding="utf-8", newline="")
    catalog = {
        "source_file": "question.txt",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "blocks": [{
            "kind": "paragraph", "block_id": "paragraph-3", "source_ref": SOURCE,
            "sha256": canonical_digest({"kind": "paragraph", "text": PARAGRAPH}),
            "text": PARAGRAPH, "rows": [],
        }],
    }
    profile = {"evidence": [{
        "evidence_id": "costs", "kind": "uploaded_csv", "source_ref": "cost.csv",
        "source_file": "cost.csv", "sha256": hashlib.sha256(csv.read_bytes()).hexdigest(),
        "size_bytes": len(csv.read_bytes()), "rows": [["item", "cost"], ["training", "8"]],
    }]}
    spec = ProblemSpec.model_validate({
        "task_summary": "公开同族题：训练费明确同时适用于学员和指导者。",
        "subproblems": [
            {"id": "sp1", "required_fact_ids": ["F_COST"],
             "required_uncertainty_ids": ["UNC001"], "depends_on_subproblem_ids": []},
            {"id": "sp2", "required_fact_ids": ["F_COST"],
             "required_uncertainty_ids": ["UNC001"], "depends_on_subproblem_ids": ["sp1"]},
            {"id": "downstream", "required_fact_ids": [],
             "required_uncertainty_ids": [], "depends_on_subproblem_ids": ["sp2"]},
            {"id": "independent", "required_fact_ids": [],
             "required_uncertainty_ids": ["REAL_UNCERTAINTY"], "depends_on_subproblem_ids": []},
        ],
        "data_sources": [{"id": "costs", "description": "Cost table", "source_ref": "cost.csv"}],
        "locked_facts": [{
            "fact_id": "F_COST", "statement": "Training unit cost is 8.",
            "source_refs": ["cost.csv"], "subproblem_ids": ["sp1", "sp2"],
            "semantic_key": "training_unit_cost", "value": 8, "unit": "yuan",
        }],
        "uncertainties": [{
            "uncertainty_id": "UNC001", "statement": "Training cost charging basis is ambiguous.",
            "source_refs": [SOURCE], "subproblem_ids": ["sp1", "sp2"],
            "semantic_key": "training_charging_basis", "candidate_values": ["trainees", "both"], "unit": "",
        }, {
            "uncertainty_id": "REAL_UNCERTAINTY", "statement": "Unspecified future arrivals.",
            "source_refs": [SOURCE], "subproblem_ids": ["independent"],
            "semantic_key": "future_arrivals", "candidate_values": ["low", "high"], "unit": "",
        }],
    })
    return spec, catalog, profile


def _correction(**changes):
    from app.guidance.source_resolution import SourceCorrectionSpec

    payload = {
        "correction_id": "resolve_charge_basis", "target_kind": "uncertainty", "target_id": "UNC001",
        "replacement_fact": {
            "fact_id": "F_CHARGE_BASIS", "statement": QUOTE, "source_refs": [SOURCE],
            "subproblem_ids": ["sp1", "sp2"], "semantic_key": "training_charging_basis",
            "value": "trainees_and_instructors", "unit": "",
        },
        "evidence": [{"source_ref": SOURCE, "quote": QUOTE}],
        "rationale": "The original source explicitly specifies both charged groups.",
    }
    payload.update(changes)
    return SourceCorrectionSpec.model_validate(payload)


def _adjudication(spec, proposals, catalog, profile, tmp_path):
    from app.guidance.source_adjudication import (
        SourceAdjudicationCheck, SourceAdjudicationResult, bind_source_adjudication,
    )

    unique = {correction.correction_id: correction for _, correction in proposals}
    return bind_source_adjudication(
        problem_spec=spec, proposals=proposals, input_catalog=catalog,
        evidence_profile=profile, work_dir=tmp_path,
        result=SourceAdjudicationResult(checks=[SourceAdjudicationCheck(
            correction_id=correction.correction_id, status="supported",
            evidence=correction.evidence,
            rationale="The fixture independently confirms the complete relation in the cited source.",
        ) for correction in unique.values()]),
    )


def _resolve(spec, proposals, catalog, profile, tmp_path):
    from app.guidance.source_resolution import resolve_problem_sources

    return resolve_problem_sources(
        spec, proposals, input_catalog=catalog, evidence_profile=profile, work_dir=tmp_path,
        adjudication=_adjudication(spec, proposals, catalog, profile, tmp_path),
    )


def test_resolves_false_ambiguity_into_a_complete_locked_fact_without_changing_input(tmp_path):
    spec, catalog, profile = _case(tmp_path)
    original = spec.model_dump(mode="json")
    correction = _correction()

    resolved, record = _resolve(spec, [("sp1", correction)], catalog, profile, tmp_path)

    assert [item.uncertainty_id for item in resolved.uncertainties] == ["REAL_UNCERTAINTY"]
    fact = next(item for item in resolved.locked_facts if item.fact_id == "F_CHARGE_BASIS")
    assert fact.value == "trainees_and_instructors"
    assert fact.statement == QUOTE
    assert resolved.subproblems[0].required_fact_ids == ["F_COST", "F_CHARGE_BASIS"]
    assert resolved.subproblems[1].required_fact_ids == ["F_COST", "F_CHARGE_BASIS"]
    assert resolved.subproblems[0].required_uncertainty_ids == []
    assert spec.model_dump(mode="json") == original
    assert resolved is not spec and resolved.subproblems[3] is not spec.subproblems[3]
    assert record.prior_problem_spec_sha256 == canonical_digest(original)
    assert record.resolved_problem_spec_sha256 == canonical_digest(resolved)
    assert record.prior_problem_spec_sha256 != record.resolved_problem_spec_sha256
    assert record.source_evidence_sha256[SOURCE] == catalog["blocks"][0]["sha256"]


def test_shared_source_invalidates_all_owners_and_descendants_but_preserves_unrelated_topic(tmp_path):
    spec, catalog, profile = _case(tmp_path)
    resolved, record = _resolve(spec, [("sp2", _correction())], catalog, profile, tmp_path)

    assert record.affected_subproblem_ids == ["sp1", "sp2", "downstream"]
    assert resolved.subproblems[3] == spec.subproblems[3]
    assert resolved.uncertainties[0] == spec.uncertainties[1]


def test_identical_shared_corrections_are_deduplicated(tmp_path):
    spec, catalog, profile = _case(tmp_path)
    correction = _correction()
    _, record = _resolve(spec, [("sp1", correction), ("sp2", correction)], catalog, profile, tmp_path)
    assert len(record.corrections) == 1


def test_equivalent_shared_corrections_keep_each_withdrawal_identity(tmp_path):
    spec, catalog, profile = _case(tmp_path)
    first = _correction()
    second = _correction(correction_id="resolve_charge_basis_from_sp2")
    resolved, record = _resolve(spec, [("sp1", first), ("sp2", second)], catalog, profile, tmp_path)
    assert [item.correction_id for item in record.corrections] == [first.correction_id, second.correction_id]
    assert [item.fact_id for item in resolved.locked_facts].count("F_CHARGE_BASIS") == 1


def test_same_correction_identity_cannot_target_different_source_records(tmp_path):
    spec, catalog, profile = _case(tmp_path)
    second = _correction(
        target_kind="locked_fact", target_id="F_COST",
        replacement_fact=spec.locked_facts[0].model_dump(mode="json"),
        evidence=[{"source_ref": "cost.csv", "quote": "training\t8"}],
    )
    with pytest.raises(ValueError, match="correction.*identity|correction.*identifier"):
        _resolve(spec, [("sp1", _correction()), ("sp2", second)], catalog, profile, tmp_path)


def test_conflicting_corrections_reject_the_entire_snapshot(tmp_path):
    spec, catalog, profile = _case(tmp_path)
    original = spec.model_dump(mode="json")
    alternative = _correction().model_dump(mode="json")
    alternative["replacement_fact"]["value"] = "trainees_only"
    from app.guidance.source_resolution import SourceCorrectionSpec

    with pytest.raises(ValueError, match="conflicting.*correction"):
        _resolve(spec, [("sp1", _correction()), ("sp2", SourceCorrectionSpec.model_validate(alternative))], catalog, profile, tmp_path)
    assert spec.model_dump(mode="json") == original


@pytest.mark.parametrize("proposer", ["independent", "unknown"])
def test_source_correction_cannot_be_proposed_outside_the_registered_scope(tmp_path, proposer):
    spec, catalog, profile = _case(tmp_path)
    with pytest.raises(ValueError, match="scope"):
        _resolve(spec, [(proposer, _correction())], catalog, profile, tmp_path)


@pytest.mark.parametrize("field,value", [
    ("subproblem_ids", ["sp1"]),
    ("semantic_key", "different_relation"),
    ("fact_id", "F_COST"),
])
def test_replacement_cannot_change_scope_semantic_identity_or_overwrite_another_fact(tmp_path, field, value):
    spec, catalog, profile = _case(tmp_path)
    replacement = _correction().replacement_fact.model_dump(mode="json")
    replacement[field] = value
    with pytest.raises(ValueError):
        _resolve(spec, [("sp1", _correction(replacement_fact=replacement))], catalog, profile, tmp_path)


@pytest.mark.parametrize("evidence", [
    [{"source_ref": SOURCE, "quote": "Only trainees are charged."}],
    [{"source_ref": "question.txt#paragraph-999", "quote": QUOTE}],
])
def test_source_quote_must_be_in_the_exact_registered_original(tmp_path, evidence):
    spec, catalog, profile = _case(tmp_path)
    with pytest.raises(ValueError, match="source|quote|evidence"):
        _resolve(spec, [("sp1", _correction(evidence=evidence))], catalog, profile, tmp_path)


@pytest.mark.parametrize("field,value", [("rationale", " "), ("evidence", []), ("replacement_fact", None)])
def test_correction_cannot_delete_ambiguity_without_a_fact_or_evidence(field, value):
    with pytest.raises(ValidationError):
        _correction(**{field: value})


@pytest.mark.parametrize("change", ["file", "block_digest", "block_text"])
def test_changed_source_bytes_or_block_payload_prevent_resolution(tmp_path, change):
    spec, catalog, profile = _case(tmp_path)
    if change == "file":
        (tmp_path / "question.txt").write_text(PARAGRAPH + " changed", encoding="utf-8")
    elif change == "block_digest":
        catalog["blocks"][0]["sha256"] = "0" * 64
    else:
        catalog["blocks"][0]["text"] = QUOTE
    with pytest.raises(ValueError, match="source|hash|digest|snapshot"):
        _resolve(spec, [("sp1", _correction())], catalog, profile, tmp_path)


def test_correcting_locked_fact_preserves_id_and_supports_exact_uploaded_table_quote(tmp_path):
    spec, catalog, profile = _case(tmp_path)
    data = spec.model_dump(mode="json")
    data["locked_facts"][0]["value"] = 9
    incorrect = ProblemSpec.model_validate(data)
    replacement = deepcopy(data["locked_facts"][0])
    replacement["value"] = 8
    correction = _correction(
        correction_id="correct_cost", target_kind="locked_fact", target_id="F_COST",
        replacement_fact=replacement, evidence=[{"source_ref": "cost.csv", "quote": "training\t8"}],
    )
    resolved, record = _resolve(incorrect, [("sp1", correction)], catalog, profile, tmp_path)
    assert resolved.locked_facts[0].fact_id == "F_COST"
    assert resolved.locked_facts[0].value == 8
    assert [item.uncertainty_id for item in resolved.uncertainties] == ["UNC001", "REAL_UNCERTAINTY"]
    assert record.source_evidence_sha256["cost.csv"] == profile["evidence"][0]["sha256"]


def test_table_snapshot_cannot_invent_cells_while_keeping_original_file_hash(tmp_path):
    spec, catalog, profile = _case(tmp_path)
    profile["evidence"][0]["rows"][1][1] = "9"
    replacement = spec.locked_facts[0].model_dump(mode="json")
    replacement["value"] = 9
    correction = _correction(
        correction_id="correct_cost", target_kind="locked_fact", target_id="F_COST",
        replacement_fact=replacement, evidence=[{"source_ref": "cost.csv", "quote": "training\t9"}],
    )
    with pytest.raises(ValueError, match="table|source|snapshot"):
        _resolve(spec, [("sp1", correction)], catalog, profile, tmp_path)


@pytest.mark.parametrize("suffix", [".csv", ".bin"])
def test_unparsed_upload_cannot_supply_invented_rows_by_changing_its_kind(tmp_path, suffix):
    spec, catalog, profile = _case(tmp_path)
    reference = f"cost{suffix}"
    (tmp_path / reference).write_bytes((tmp_path / "cost.csv").read_bytes())
    spec.locked_facts[0].source_refs = [reference]
    spec.data_sources[0].source_ref = reference
    item = profile["evidence"][0]
    item.update(kind="uploaded_file", source_ref=reference, source_file=reference)
    item["rows"][1][1] = "999"
    replacement = spec.locked_facts[0].model_dump(mode="json")
    replacement["value"] = 999
    correction = _correction(
        correction_id="forged_cost", target_kind="locked_fact", target_id="F_COST",
        replacement_fact=replacement, evidence=[{"source_ref": reference, "quote": "training\t999"}],
    )

    with pytest.raises(ValueError, match="source|kind|unparsed"):
        _resolve(spec, [("sp1", correction)], catalog, profile, tmp_path)


def test_reloading_resolution_rejects_retyped_source_with_invented_rows(tmp_path):
    from app.guidance.source_resolution import SourceResolutionRecord, validate_source_resolution

    spec, catalog, profile = _case(tmp_path)
    adjudication = _adjudication(spec, [("sp1", _correction())], catalog, profile, tmp_path)
    profile["evidence"][0]["kind"] = "uploaded_file"
    profile["evidence"][0]["rows"][1][1] = "999"
    payload = spec.model_dump(mode="json")
    payload["locked_facts"][0]["value"] = 999
    resolved = ProblemSpec.model_validate(payload)
    correction = _correction(
        correction_id="forged_cost", target_kind="locked_fact", target_id="F_COST",
        replacement_fact=resolved.locked_facts[0].model_dump(mode="json"),
        evidence=[{"source_ref": "cost.csv", "quote": "training\t999"}],
    )
    record = SourceResolutionRecord(
        prior_problem_spec_sha256=canonical_digest(spec),
        resolved_problem_spec_sha256=canonical_digest(resolved), corrections=[correction],
        affected_subproblem_ids=["sp1", "sp2", "downstream"],
        source_evidence_sha256={"cost.csv": profile["evidence"][0]["sha256"]},
        source_adjudication_sha256=canonical_digest(adjudication),
    )

    with pytest.raises(ValueError, match="source|kind|unparsed"):
        validate_source_resolution(
            record, previous_problem_spec=spec, problem_spec=resolved,
            input_catalog=catalog, evidence_profile=profile, work_dir=tmp_path,
            adjudication=adjudication,
        )


def test_no_corrections_return_an_independent_equal_snapshot(tmp_path):
    spec, catalog, profile = _case(tmp_path)
    resolved, record = _resolve(spec, [], catalog, profile, tmp_path)
    assert resolved == spec and resolved is not spec
    assert record.affected_subproblem_ids == []
    assert record.corrections == []
    assert record.prior_problem_spec_sha256 == record.resolved_problem_spec_sha256


def test_resolution_record_is_recomputed_against_both_snapshots(tmp_path):
    from app.guidance.source_resolution import validate_source_resolution

    spec, catalog, profile = _case(tmp_path)
    resolved, record = _resolve(spec, [("sp1", _correction())], catalog, profile, tmp_path)
    adjudication = _adjudication(spec, [("sp1", _correction())], catalog, profile, tmp_path)
    validate_source_resolution(
        record, previous_problem_spec=spec, problem_spec=resolved,
        input_catalog=catalog, evidence_profile=profile, work_dir=tmp_path,
        adjudication=adjudication,
    )
    changed = resolved.model_copy(update={"task_summary": "different snapshot"})
    with pytest.raises(ValueError, match="snapshot|resolution"):
        validate_source_resolution(
            record, previous_problem_spec=spec, problem_spec=changed,
            input_catalog=catalog, evidence_profile=profile, work_dir=tmp_path,
            adjudication=adjudication,
        )


@pytest.mark.parametrize("field,value", [
    ("affected_subproblem_ids", ["sp1"]),
    ("source_evidence_sha256", {SOURCE: "0" * 64}),
    ("prior_problem_spec_sha256", "0" * 64),
])
def test_tampered_resolution_record_is_not_accepted(tmp_path, field, value):
    from app.guidance.source_resolution import validate_source_resolution

    spec, catalog, profile = _case(tmp_path)
    resolved, record = _resolve(spec, [("sp1", _correction())], catalog, profile, tmp_path)
    adjudication = _adjudication(spec, [("sp1", _correction())], catalog, profile, tmp_path)
    with pytest.raises(ValueError, match="snapshot|resolution"):
        validate_source_resolution(
            record.model_copy(update={field: value}), previous_problem_spec=spec, problem_spec=resolved,
            input_catalog=catalog, evidence_profile=profile, work_dir=tmp_path,
            adjudication=adjudication,
        )
