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
    return payload



def _ready(spec, catalog, profile, tmp_path):
    from test_guidance_source_preflight import _ready as prepare
    return prepare(spec, catalog, profile, tmp_path)


@pytest.mark.parametrize("change", ["file", "block_digest", "block_text", "rehashed_forgery"])
def test_changed_original_or_forged_catalog_prevents_source_preflight(tmp_path, change):
    spec, catalog, profile = _case(tmp_path)
    if change == "file":
        (tmp_path / "question.txt").write_text(PARAGRAPH + " changed", encoding="utf-8")
    elif change == "block_digest":
        catalog["blocks"][0]["sha256"] = "0" * 64
    elif change == "block_text":
        catalog["blocks"][0]["text"] = QUOTE
    else:
        catalog["blocks"][0]["text"] = "Invented relation"
        catalog["blocks"][0]["sha256"] = canonical_digest({"kind": "paragraph", "text": "Invented relation"})
    with pytest.raises(ValueError, match="source|hash|digest|snapshot"):
        _ready(spec, catalog, profile, tmp_path)


def test_csv_snapshot_cannot_invent_cells_with_genuine_file_hash(tmp_path):
    spec, catalog, profile = _case(tmp_path)
    profile["evidence"][0]["rows"][1][1] = "9"
    with pytest.raises(ValueError, match="table|source|snapshot"):
        _ready(spec, catalog, profile, tmp_path)


def test_uploaded_table_profile_is_checked_against_original_rows(tmp_path):
    spec, catalog, profile = _case(tmp_path)
    profile["evidence"][0]["profile"] = {"columns": ["item", "cost"], "sample_rows": [["training", "9"]]}
    with pytest.raises(ValueError, match="profile|source"):
        _ready(spec, catalog, profile, tmp_path)


@pytest.mark.parametrize("suffix", [".csv", ".bin"])
def test_unparsed_upload_cannot_supply_fake_source_rows(tmp_path, suffix):
    from app.guidance.source_resolution import resolve_problem_sources
    from test_guidance_source_preflight import _decisions, _checks

    spec, catalog, profile = _case(tmp_path)
    reference = f"cost{suffix}"
    (tmp_path / reference).write_bytes((tmp_path / "cost.csv").read_bytes())
    item = profile["evidence"][0]
    item.update(kind="uploaded_file", source_ref=reference, source_file=reference)
    item["rows"][1][1] = "999"
    decisions = _decisions(spec)
    decisions.decisions[0].evidence[0].source_ref = reference
    decisions.decisions[0].evidence[0].quote = "training\t999"
    decisions.decisions[0].replacement_fact.source_refs = [reference]
    with pytest.raises(ValueError, match="source|kind|registered"):
        resolve_problem_sources(spec, interpretation=decisions, adjudication=_checks(decisions),
                                input_catalog=catalog, evidence_profile=profile, work_dir=tmp_path)


def test_reloading_rejects_retyped_source_even_with_same_file_hash(tmp_path):
    from app.guidance.source_resolution import validate_source_preflight

    spec, catalog, profile = _case(tmp_path)
    resolved, record = _ready(spec, catalog, profile, tmp_path)
    profile["evidence"][0]["kind"] = "uploaded_file"
    profile["evidence"][0]["rows"][1][1] = "999"
    with pytest.raises(ValueError, match="source|kind|snapshot"):
        validate_source_preflight(record, problem_spec=resolved, input_catalog=catalog,
                                  evidence_profile=profile, work_dir=tmp_path)


def test_complete_source_decisions_do_not_rewrite_undisputed_locked_facts(tmp_path):
    spec, catalog, profile = _case(tmp_path)
    original = deepcopy(spec.locked_facts)
    resolved, _ = _ready(spec, catalog, profile, tmp_path)
    assert resolved.locked_facts[:len(original)] == original
    assert resolved.subproblems[3] == spec.subproblems[3]


def test_old_correction_proposal_is_not_a_front_loaded_source_decision():
    from app.guidance.source_resolution import SourceDecisionSpec

    with pytest.raises(ValidationError):
        SourceDecisionSpec.model_validate(_correction())


@pytest.mark.parametrize("field,value", [
    ("status", "blocked"),
    ("affected_subproblem_ids", ["sp1"]),
    ("source_files_sha256", {"question.txt": "0" * 64}),
    ("resolved_problem_spec_sha256", "0" * 64),
])
def test_tampered_source_record_is_rejected_on_reload(tmp_path, field, value):
    from app.guidance.source_resolution import validate_source_preflight

    spec, catalog, profile = _case(tmp_path)
    resolved, record = _ready(spec, catalog, profile, tmp_path)
    with pytest.raises(ValueError, match="binding|snapshot"):
        validate_source_preflight(record.model_copy(update={field: value}), problem_spec=resolved,
                                  input_catalog=catalog, evidence_profile=profile, work_dir=tmp_path)
