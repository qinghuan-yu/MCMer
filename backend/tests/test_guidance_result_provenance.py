import asyncio
import hashlib
import json

import pytest

from app.artifacts.evidence_profile import build_evidence_profile
from app.artifacts.input_catalog import build_input_catalog
from app.guidance.model_ir import ModelIR
from app.guidance.model_review_contracts import ApprovedModelIR
from app.guidance.verification_stage import ResultVerificationStage
from test_guidance_recovery import _execute, _registry, _scalar_model


def _case(root, evidence_ids, *, source_id="upload.file.1", source_file="inputs/chain.txt", duplicate=False, missing_source=False, failed_nodes=()):
    (root / "inputs").mkdir()
    for name in ["chain", "independent"]:
        (root / f"inputs/{name}.txt").write_text("2", encoding="utf-8")
    record = {"evidence_id": source_id, "kind": "uploaded_file", "source_ref": source_file,
              "source_file": source_file, "sha256": hashlib.sha256(b"2").hexdigest(), "size_bytes": 1}
    (root / "evidence_profile.json").write_text(json.dumps({"evidence": [record] * (2 if duplicate else 1)}), encoding="utf-8")
    payload = _scalar_model().model_dump(mode="json")
    payload["subproblems"][0]["final_answers"][0]["evidence_ids"] = evidence_ids
    model = ModelIR.model_validate(payload)
    if missing_source:
        (root / source_file).unlink()
    _execute(root, model, _registry([], set(failed_nodes)))
    (root / "approved_model_ir.json").write_text(ApprovedModelIR(
        review_status="approved", model_id=model.model_id,
        executable_subproblem_ids=[part.subproblem_id for part in model.subproblems], approved_model_ir=model,
    ).model_dump_json(), encoding="utf-8")


def _verify(root):
    asyncio.run(ResultVerificationStage().run(task_id="source-provenance", task={},
        input_path=root / "execution_manifest.json", output_path=root / "verification_report.json"))
    return json.loads((root / "verification_report.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("ids", [["upload.file.1"], ["source", "upload.file.1"]])
def test_declared_registered_sources_and_executed_nodes_are_distinct_valid_evidence(tmp_path, ids):
    _case(tmp_path, ids)
    report = _verify(tmp_path)
    assert report["execution_verified"]["status"] == "passed"
    assert report["evidence_supported"]["status"] == "passed"
    result = json.loads((tmp_path / "result_registry.json").read_text(encoding="utf-8"))["verified_results"][0]
    assert result["evidence_ids"] == ids
    assert result["source_node"] == "dependent"
    assert report["model_adequate"]["mathematical_review"]["status"] == "not_assessed"


@pytest.mark.parametrize(("ids", "source_file"), [
    (["missing_source"], "inputs/chain.txt"),
    (["upload.file.1", "unexecuted_node"], "inputs/chain.txt"),
    (["upload.file.1"], "inputs/independent.txt"),
])
def test_unknown_unexecuted_or_undeclared_sources_remain_rejected(tmp_path, ids, source_file):
    _case(tmp_path, ids, source_file=source_file)
    assert _verify(tmp_path)["evidence_supported"]["status"] == "failed"


@pytest.mark.parametrize("mutation", ["change", "delete"])
def test_source_registration_snapshot_cannot_change_after_execution(tmp_path, mutation):
    _case(tmp_path, ["source"])
    profile = tmp_path / "evidence_profile.json"
    if mutation == "delete":
        profile.unlink()
    else:
        value = json.loads(profile.read_text(encoding="utf-8"))
        value["evidence"][0]["evidence_id"] = "different_registration"
        profile.write_text(json.dumps(value), encoding="utf-8")
    report = _verify(tmp_path)
    assert report["execution_verified"]["status"] == "failed"


@pytest.mark.parametrize("ambiguity", ["duplicate", "node_collision"])
def test_ambiguous_evidence_identifiers_are_rejected(tmp_path, ambiguity):
    source_id = "source" if ambiguity == "node_collision" else "upload.file.1"
    _case(tmp_path, [source_id], source_id=source_id, duplicate=ambiguity == "duplicate")
    assert _verify(tmp_path)["evidence_supported"]["status"] == "failed"


def test_registered_source_must_refer_to_a_real_file(tmp_path):
    _case(tmp_path, ["upload.file.1"], missing_source=True)
    assert _verify(tmp_path)["evidence_supported"]["status"] == "failed"


def test_failed_subproblem_source_does_not_invalidate_independent_result_evidence(tmp_path):
    _case(tmp_path, ["upload.file.1"], missing_source=True, failed_nodes={"source"})
    results = json.loads((tmp_path / "result_registry.json").read_text(encoding="utf-8"))
    assert [result["subproblem_id"] for result in results["verified_results"]] == ["independent"]
    assert [result["subproblem_id"] for result in results["blocked_results"]] == ["chain"]
    report = _verify(tmp_path)
    assert report["coverage_status"] == "partial"
    assert report["execution_verified"]["status"] == "passed"
    assert report["evidence_supported"]["status"] == "passed"
    assert any("subproblem chain blocked" in issue for issue in report["blocking_issues"])


def _embedded_table_case(root, *, mutation=None, evidence_ids=None):
    from docx import Document

    (root / "inputs").mkdir()
    source_path = root / "inputs/problem.docx"
    document = Document()
    document.add_paragraph("附件一：已登记的需求表。")
    table = document.add_table(rows=2, cols=2)
    for cells, values in zip(table.rows, [["周期", "需求"], ["1", "2"]]):
        for cell, value in zip(cells.cells, values):
            cell.text = value
    document.save(source_path)
    catalog = build_input_catalog(source_path, work_dir=root).model_dump(mode="json")
    profile = build_evidence_profile(root).model_dump(mode="json")
    record = profile["evidence"][0]
    assert record["kind"] == "embedded_table"
    assert record["rows"] == [["周期", "需求"], ["1", "2"]]
    block = next(block for block in catalog["blocks"] if block["kind"] == "table")

    if mutation == "profile_rows":
        record["rows"][1][1] = "99"
    elif mutation == "profile_hash":
        record["sha256"] = "0" * 64
    elif mutation == "shared_false_hash":
        record["sha256"] = block["sha256"] = "0" * 64
    elif mutation == "catalog_rows":
        block["rows"][1][1] = "99"
    elif mutation == "source_document":
        document.tables[0].cell(1, 1).text = "99"
        document.save(source_path)
    (root / "evidence_profile.json").write_text(json.dumps(profile), encoding="utf-8")
    (root / "input_catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    (root / "inputs/independent.txt").write_text("2", encoding="utf-8")

    payload = _scalar_model().model_dump(mode="json")
    subproblem = payload["subproblems"][0]
    subproblem["data"][0]["source_ref"] = record["source_ref"]
    subproblem["final_answers"][0]["evidence_ids"] = evidence_ids or [record["evidence_id"]]
    model = ModelIR.model_validate(payload)
    assert _execute(root, model, _registry([], set())).status == "passed"
    (root / "approved_model_ir.json").write_text(ApprovedModelIR(
        review_status="approved", model_id=model.model_id,
        executable_subproblem_ids=[part.subproblem_id for part in model.subproblems],
        approved_model_ir=model,
    ).model_dump_json(), encoding="utf-8")


@pytest.mark.parametrize("ids", [["question.table.1"], ["source", "question.table.1"]])
def test_docx_table_registration_supports_result_sources_without_node_relabeling(tmp_path, ids):
    _embedded_table_case(tmp_path, evidence_ids=ids)
    report = _verify(tmp_path)
    assert report["execution_verified"]["status"] == "passed"
    assert report["evidence_supported"]["status"] == "passed"
    result = json.loads((tmp_path / "result_registry.json").read_text(encoding="utf-8"))["verified_results"][0]
    assert result["evidence_ids"] == ids
    assert result["source_node"] == "dependent"
    assert report["model_adequate"]["mathematical_review"]["status"] == "not_assessed"


@pytest.mark.parametrize("mutation", [
    "profile_rows", "profile_hash", "shared_false_hash", "catalog_rows", "source_document",
])
def test_embedded_table_registration_must_match_catalog_content_hash_and_original(tmp_path, mutation):
    _embedded_table_case(tmp_path, mutation=mutation)
    report = _verify(tmp_path)
    # 不一致已在执行前写入，执行快照仍有效，由来源核验独立拒绝。
    assert report["execution_verified"]["status"] == "passed"
    assert report["evidence_supported"]["status"] == "failed"


@pytest.mark.parametrize("mutation", ["change", "delete"])
def test_input_catalog_snapshot_cannot_change_after_execution(tmp_path, mutation):
    _embedded_table_case(tmp_path, evidence_ids=["source"])
    catalog_path = tmp_path / "input_catalog.json"
    if mutation == "delete":
        catalog_path.unlink()
    else:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["blocks"][0]["text"] = "执行后修改的题面。"
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    report = _verify(tmp_path)
    assert report["execution_verified"]["status"] == "failed"
