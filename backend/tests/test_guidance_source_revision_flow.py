import asyncio
import json
from pathlib import Path

import pytest

from app.artifacts.input_catalog import build_input_catalog
from app.guidance.contracts import ProblemSpec
from app.guidance.model_ir import FactBindingSpec, ModelIR
from app.guidance.model_review_contracts import ApprovedModelIR, ModelIRReview


def _case(root, *, target_kind="uncertainty"):
    source = root / "question.txt"
    source.write_text("Training is charged to both learners and instructors.", encoding="utf-8")
    catalog = build_input_catalog(source, work_dir=root, extracted_text=source.read_text(encoding="utf-8"))
    (root / "evidence_profile.json").write_text('{"evidence": []}', encoding="utf-8")
    source_ref = catalog.blocks[0].source_ref
    scope = ["repair", "shared"]
    target_id = "U_training" if target_kind == "uncertainty" else "F_training"
    record = {
        "statement": "Training may be charged only to learners.",
        "source_refs": [source_ref], "subproblem_ids": scope,
        "semantic_key": "training_charge_basis", "unit": "",
    }
    uncertain = target_kind == "uncertainty"
    identifiers = ["child", "repair", "shared", "independent"]
    spec = ProblemSpec.model_validate({
        "task_summary": "Use the registered training rule in two independent branches.",
        "subproblems": [{
            "id": identifier,
            "required_fact_ids": [target_id] if identifier in scope and not uncertain else [],
            "required_uncertainty_ids": [target_id] if identifier in scope and uncertain else [],
            "depends_on_subproblem_ids": ["shared"] if identifier == "child" else [],
        } for identifier in identifiers],
        "uncertainties": [{**record, "uncertainty_id": target_id,
                           "candidate_values": ["learners", "learners_and_instructors"]}] if uncertain else [],
        "locked_facts": [{**record, "fact_id": target_id, "value": "learners"}] if not uncertain else [],
    })
    original = ModelIR.model_validate_json(
        (Path(__file__).parent / "fixtures/model_ir/wuyi_anchor_bolt.json").read_text(encoding="utf-8")
    ).subproblems[0]
    model = ModelIR(model_id="source-revision", subproblems=[
        original.model_copy(update={
            "subproblem_id": identifier,
            "fact_bindings": [FactBindingSpec(fact_id=target_id, model_paths=["/objective"])]
            if identifier in scope and not uncertain else [],
        }) for identifier in identifiers
    ])
    correction = {
        "correction_id": "C_training", "target_kind": target_kind, "target_id": target_id,
        "replacement_fact": {
            "fact_id": "F_training", "statement": catalog.blocks[0].text,
            "source_refs": [source_ref], "subproblem_ids": scope,
            "semantic_key": "training_charge_basis", "value": "learners_and_instructors", "unit": "",
        },
        "evidence": [{"source_ref": source_ref, "quote": catalog.blocks[0].text}],
        "rationale": "The original text explicitly charges both participant groups.",
    }
    review = ModelIRReview.model_validate({
        "model_id": model.model_id,
        "subproblem_reviews": [{
            "subproblem_id": identifier,
            "status": "revision_required" if identifier == "repair" else "approved",
            "source_corrections": [correction] if identifier == "repair" else [],
            "required_revisions": [{
                "kind": "source", "requirement_id": "R_training",
                "basis_refs": [f"problem_spec.json#{'uncertainties' if uncertain else 'locked_facts'}/{target_id}"],
                "description": "Resolve the training-charge interpretation against the source.",
                "acceptance_criteria": "Use the source-supported charging relation.",
            }] if identifier == "repair" else [],
        } for identifier in identifiers],
    })
    return spec, model, review, catalog.model_dump(mode="json")


def _write(root, spec, model, review):
    # 以不同于model_dump_json默认的排版验证原始拆题字节真正保留。
    old_bytes = (json.dumps(spec.model_dump(mode="json"), ensure_ascii=False, indent=4) + "\n").encode("utf-8")
    (root / "problem_spec.json").write_bytes(old_bytes)
    (root / "model_ir.json").write_text(model.model_dump_json(), encoding="utf-8")
    (root / "model_review.json").write_text(review.model_dump_json(), encoding="utf-8")
    return old_bytes


def test_preserving_revision_applies_source_correction_and_keeps_original_bytes(tmp_path):
    from app.guidance.pipeline import _preserve_revision_request

    spec, model, review, _ = _case(tmp_path)
    old_bytes = _write(tmp_path, spec, model, review)
    revision_path = _preserve_revision_request(tmp_path, tmp_path / "model_ir.json")

    updated = ProblemSpec.model_validate_json((tmp_path / "problem_spec.json").read_text(encoding="utf-8"))
    assert updated.uncertainties == []
    assert updated.locked_facts[0].value == "learners_and_instructors"
    assert (tmp_path / "problem_spec_initial.json").read_bytes() == old_bytes
    assert json.loads(revision_path.read_text(encoding="utf-8"))["problem_spec_ref"] == "problem_spec_initial.json"
    resolution = json.loads((tmp_path / "source_resolution.json").read_text(encoding="utf-8"))
    assert resolution["affected_subproblem_ids"] == ["child", "repair", "shared"]
    assert spec.uncertainties[0].uncertainty_id == "U_training"


def test_pending_source_correction_blocks_shared_scope_and_descendants(tmp_path):
    from app.guidance.stages import _build_reviewed_model_projection

    spec, model, review, _ = _case(tmp_path)
    projection = _build_reviewed_model_projection(model, review, spec)

    assert [item.subproblem_id for item in projection.subproblems if item.execution_status == "solvable"] == ["independent"]
    assert projection.subproblems[-1] == model.subproblems[-1]
    assert "source" in projection.subproblems[2].blocking_reason.lower()


@pytest.mark.parametrize("target_kind", ["uncertainty", "locked_fact"])
def test_unresolved_source_basis_without_correction_blocks_shared_scope(tmp_path, target_kind):
    from app.guidance.model_review_validation import validate_model_review
    from app.guidance.stages import _build_reviewed_model_projection

    spec, model, review, _ = _case(tmp_path, target_kind=target_kind)
    review.subproblem_reviews[1].source_corrections = []
    validate_model_review(review, model, problem_spec=spec)

    projection = _build_reviewed_model_projection(model, review, spec)

    assert [item.subproblem_id for item in projection.subproblems if item.execution_status == "solvable"] == ["independent"]
    assert projection.subproblems[-1] == model.subproblems[-1]


@pytest.mark.parametrize("target_kind", ["uncertainty", "locked_fact"])
def test_unresolved_shared_source_blocks_revision_reuse_and_generation(tmp_path, monkeypatch, target_kind):
    from app.guidance.modeler import LLMModeler

    spec, model, review, _ = _case(tmp_path, target_kind=target_kind)
    review.subproblem_reviews[1].source_corrections = []
    calls = []

    async def generate(**kwargs):
        calls.append(kwargs["subproblem"]["id"])
        raise AssertionError("Unresolved source must not reach generation")

    monkeypatch.setattr("app.guidance.modeler._request_single_subproblem_model", generate)
    result = asyncio.run(LLMModeler(object()).revise(
        problem_spec=spec, previous_model_ir=model, review=review,
        evidence_profile={"evidence": []}, task={}, work_dir=tmp_path,
    ))

    assert calls == []
    assert [item.subproblem_id for item in result.subproblems if item.execution_status == "solvable"] == ["independent"]
    assert result.subproblems[-1] == model.subproblems[-1]


def test_source_changed_models_and_descendants_regenerate_even_when_model_values_match(tmp_path, monkeypatch):
    from app.guidance.modeler import LLMModeler
    from app.guidance.pipeline import _preserve_revision_request

    spec, model, review, _ = _case(tmp_path, target_kind="locked_fact")
    _write(tmp_path, spec, model, review)
    _preserve_revision_request(tmp_path, tmp_path / "model_ir.json")
    current = ProblemSpec.model_validate_json((tmp_path / "problem_spec.json").read_text(encoding="utf-8"))
    calls = []
    original = {item.subproblem_id: item for item in model.subproblems}

    async def generate(**kwargs):
        identifier = kwargs["subproblem"]["id"]
        calls.append(identifier)
        assert kwargs["problem_spec"].locked_facts[0].value == "learners_and_instructors"
        return ModelIR(model_id="same-formula", subproblems=[original[identifier]])

    monkeypatch.setattr("app.guidance.modeler._request_single_subproblem_model", generate)
    result = asyncio.run(LLMModeler(object()).revise(
        problem_spec=current, previous_model_ir=model, review=review,
        evidence_profile={"evidence": []}, task={}, work_dir=tmp_path,
    ))
    assert calls == ["shared", "child", "repair"]
    assert result == model


def test_source_obligations_are_not_sent_as_model_repair_instructions(tmp_path):
    from app.guidance.modeling_context import _subproblem_review_payload

    _, _, review, _ = _case(tmp_path)
    payload = _subproblem_review_payload(review, "repair")
    assert payload["subproblem_reviews"][0]["required_revisions"] == []
    assert review.subproblem_reviews[1].required_revisions[0].kind == "source"


def test_unresolved_source_obligation_blocks_generation_without_calling_model(tmp_path, monkeypatch):
    from app.guidance.modeler import LLMModeler

    spec, model, review, _ = _case(tmp_path)
    calls = []

    async def generate(**kwargs):
        calls.append(kwargs["subproblem"]["id"])
        return ModelIR(model_id="unused", subproblems=[model.subproblems[1]])

    monkeypatch.setattr("app.guidance.modeler._request_single_subproblem_model", generate)
    result = asyncio.run(LLMModeler(object()).revise(
        problem_spec=spec, previous_model_ir=model, review=review,
        evidence_profile={"evidence": []}, task={}, work_dir=tmp_path,
    ))
    assert calls == []
    repaired = next(item for item in result.subproblems if item.subproblem_id == "repair")
    assert repaired.execution_status == "blocked"
    assert "source" in repaired.blocking_reason.lower()


def test_source_resolution_survives_modeling_and_is_removed_by_new_decomposition(tmp_path):
    from app.guidance.recovery import invalidate_stage_outputs

    snapshots = ("source_resolution.json", "problem_spec_initial.json", "approved_model_ir_initial.json")
    for name in snapshots:
        (tmp_path / name).write_text("{}", encoding="utf-8")
    invalidate_stage_outputs(tmp_path, "modeling")
    assert all((tmp_path / name).exists() for name in snapshots)
    invalidate_stage_outputs(tmp_path, "model_review")
    assert all((tmp_path / name).exists() for name in snapshots)
    invalidate_stage_outputs(tmp_path, "decomposition")
    assert not any((tmp_path / name).exists() for name in snapshots)


def test_preserving_revision_keeps_original_approved_binding_bytes(tmp_path):
    from app.guidance.model_review_validation import bind_model_review
    from app.guidance.pipeline import _preserve_revision_request
    from app.guidance.stages import _build_reviewed_model_projection

    spec, model, review, catalog = _case(tmp_path)
    _write(tmp_path, spec, model, review)
    projection = _build_reviewed_model_projection(model, review, spec)
    approved = ApprovedModelIR(
        review_status="partial", model_id=model.model_id,
        executable_subproblem_ids=["independent"], approved_model_ir=projection,
        review_binding=bind_model_review(
            review=review, model_ir=model, approved_model_ir=projection, problem_spec=spec,
            evidence_profile={"evidence": []}, input_catalog=catalog, revision_request=None,
        ),
    )
    original_bytes = (json.dumps(approved.model_dump(mode="json"), indent=4) + "\n").encode("utf-8")
    (tmp_path / "approved_model_ir.json").write_bytes(original_bytes)
    _preserve_revision_request(tmp_path, tmp_path / "model_ir.json")
    assert (tmp_path / "approved_model_ir_initial.json").read_bytes() == original_bytes


def _resolved_model(model):
    return model.model_copy(update={"subproblems": [
        item.model_copy(update={"fact_bindings": [
            FactBindingSpec(fact_id="F_training", model_paths=["/objective"])
        ]}) if item.subproblem_id in {"repair", "shared"} else item
        for item in model.subproblems
    ]})


def test_modeling_stage_reads_resolved_spec_instead_of_initial_review_snapshot(tmp_path):
    from app.guidance.pipeline import _preserve_revision_request
    from app.guidance.stages import ModelingStage

    spec, model, review, _ = _case(tmp_path)
    _write(tmp_path, spec, model, review)
    request = _preserve_revision_request(tmp_path, tmp_path / "model_ir.json")
    captured = []
    revised = _resolved_model(model)

    class Modeler:
        async def revise(self, **kwargs):
            captured.append(kwargs)
            return revised

    asyncio.run(ModelingStage(modeler=Modeler()).run(
        task_id="resolved-stage", task={}, input_path=request,
        output_path=tmp_path / "model_ir.json",
    ))

    assert len(captured) == 1
    assert captured[0]["review"].problem_spec_ref == "problem_spec_initial.json"
    assert captured[0]["problem_spec"].uncertainties == []
    assert captured[0]["problem_spec"].locked_facts[0].value == "learners_and_instructors"
    assert captured[0]["previous_model_ir"] == model
    assert ModelIR.model_validate_json((tmp_path / "model_ir.json").read_text(encoding="utf-8")) == revised


def test_pipeline_applies_one_source_correction_and_isolates_final_pending_scope(tmp_path):
    from app.guidance.pipeline import GuidancePipeline
    from app.guidance.recovery import canonical_digest
    from app.guidance.stages import ModelingStage, ModelReviewStage

    spec, model, initial_review, _ = _case(tmp_path)
    calls = []
    first_correction = initial_review.subproblem_reviews[1].source_corrections[0]
    next_correction = first_correction.model_copy(update={
        "correction_id": "C_training_again", "target_kind": "locked_fact", "target_id": "F_training",
        "replacement_fact": first_correction.replacement_fact.model_copy(update={
            "statement": "Learners and instructors are both charged for training.",
        }),
    })

    class Decomposition:
        async def run(self, **kwargs):
            kwargs["output_path"].write_text(spec.model_dump_json(), encoding="utf-8")
            return kwargs["output_path"]

    class Modeler:
        async def model(self, **kwargs):
            calls.append("model")
            return model

        async def revise(self, **kwargs):
            calls.append("revise")
            assert kwargs["problem_spec"].uncertainties == []
            assert kwargs["problem_spec"].locked_facts == [first_correction.replacement_fact]
            return _resolved_model(model)

    class Reviewer:
        async def review(self, **kwargs):
            calls.append("review")
            if kwargs["revision_request"] is None:
                return initial_review
            assert kwargs["problem_spec"].locked_facts == [first_correction.replacement_fact]
            return ModelIRReview.model_validate({
                "model_id": model.model_id,
                "subproblem_reviews": [{
                    "subproblem_id": item.subproblem_id,
                    "status": "revision_required" if item.subproblem_id == "repair" else "approved",
                    "source_corrections": [next_correction] if item.subproblem_id == "repair" else [],
                    "revision_checks": [{
                        "requirement_id": "R_training", "status": "withdrawn",
                        "source_correction_ids": ["C_training"],
                        "rationale": "Applied source resolution establishes the charging relation.",
                    }] if item.subproblem_id == "repair" else [],
                } for item in model.subproblems],
            })

    class Calculation:
        async def run(self, **kwargs):
            calls.append("calculate")
            approved = ApprovedModelIR.model_validate_json(kwargs["input_path"].read_text(encoding="utf-8"))
            assert approved.executable_subproblem_ids == ["independent"]
            kwargs["output_path"].write_text("{}", encoding="utf-8")
            return kwargs["output_path"]

    class Output:
        async def run(self, **kwargs):
            kwargs["output_path"].write_text("{}", encoding="utf-8")
            return kwargs["output_path"]

    pipeline = GuidancePipeline(
        decomposition_stage=Decomposition(), modeling_stage=ModelingStage(modeler=Modeler()),
        model_review_stage=ModelReviewStage(reviewer=Reviewer()), calculation_stage=Calculation(),
        result_verification_stage=Output(), guidance_stage=Output(),
    )

    async def collect():
        return [item async for item in pipeline.run("bounded-source-revision", {"work_dir": str(tmp_path)})]

    events = asyncio.run(collect())
    assert calls == ["model", "review", "revise", "review", "calculate"]
    assert events[-1]["data"]["status"] == "partial"
    current = ProblemSpec.model_validate_json((tmp_path / "problem_spec.json").read_text(encoding="utf-8"))
    assert current.locked_facts == [first_correction.replacement_fact]
    record = json.loads((tmp_path / "source_resolution.json").read_text(encoding="utf-8"))
    assert [item["correction_id"] for item in record["corrections"]] == ["C_training"]
    approved = ApprovedModelIR.model_validate_json((tmp_path / "approved_model_ir.json").read_text(encoding="utf-8"))
    assert approved.executable_subproblem_ids == ["independent"]
    assert approved.review_binding.source_resolution_sha256 == canonical_digest(record)
    assert approved.review_binding.problem_spec_sha256 == canonical_digest(current)
    assert approved.approved_model_ir.subproblems[-1] == model.subproblems[-1]
    assert all(item.execution_status == "blocked" for item in approved.approved_model_ir.subproblems[:-1])


@pytest.mark.parametrize("broken_snapshot", ["missing_initial", "altered_digest"])
def test_loading_source_resolution_rejects_missing_or_corrupted_snapshot(tmp_path, broken_snapshot):
    from app.guidance.pipeline import _preserve_revision_request
    from app.guidance.recovery import load_source_resolution

    spec, model, review, _ = _case(tmp_path)
    _write(tmp_path, spec, model, review)
    _preserve_revision_request(tmp_path, tmp_path / "model_ir.json")
    current = ProblemSpec.model_validate_json((tmp_path / "problem_spec.json").read_text(encoding="utf-8"))
    assert load_source_resolution(tmp_path, current) is not None
    if broken_snapshot == "missing_initial":
        (tmp_path / "problem_spec_initial.json").unlink()
    else:
        path = tmp_path / "source_resolution.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record["resolved_problem_spec_sha256"] = "0" * 64
        path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="source|ProblemSpec|snapshot"):
        load_source_resolution(tmp_path, current)
