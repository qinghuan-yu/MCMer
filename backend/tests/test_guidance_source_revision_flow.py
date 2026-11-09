"""已裁决题意进入数学修订，来源职责不会在审核后重新开启。"""

import asyncio
import json
from pathlib import Path

import pytest

from app.artifacts.evidence_profile import EvidenceProfile
from app.artifacts.input_catalog import build_input_catalog
from app.guidance.contracts import ProblemSpec
from app.guidance.model_ir import FactBindingSpec, ModelIR
from app.guidance.model_review_contracts import ApprovedModelIR, ModelIRReview


def _case(root, *, status="supported"):
    from app.guidance.source_resolution import (
        SourceAdjudicationResult, SourceInterpretationResult, resolve_problem_sources,
    )

    source = root / "question.txt"
    quote = "Training is charged to both learners and instructors."
    source.write_text(quote, encoding="utf-8")
    catalog = build_input_catalog(source, work_dir=root, extracted_text=quote).model_dump(mode="json")
    profile = EvidenceProfile().model_dump(mode="json")
    (root / "evidence_profile.json").write_text(json.dumps(profile), encoding="utf-8")
    ref = catalog["blocks"][0]["source_ref"]
    scope = ["repair", "shared"]
    identifiers = ["child", "repair", "shared", "independent"]
    spec = ProblemSpec.model_validate({
        "task_summary": "Use the registered training relation.",
        "subproblems": [{"id": sid, "required_fact_ids": [],
                         "required_uncertainty_ids": ["U_training"] if sid in scope else [],
                         "depends_on_subproblem_ids": ["shared"] if sid == "child" else []}
                        for sid in identifiers],
        "uncertainties": [{"uncertainty_id": "U_training", "statement": "Does training charge the instructor?",
                           "source_refs": [ref], "subproblem_ids": scope,
                           "semantic_key": "training.charge", "candidate_values": ["learner", "both"], "unit": ""}],
    })
    evidence = [{"source_ref": ref, "quote": quote}]
    interpretation = SourceInterpretationResult.model_validate({"decisions": [{
        "uncertainty_id": "U_training", "subproblem_ids": scope, "status": "specified",
        "replacement_fact": {"fact_id": "F_training", "statement": quote, "source_refs": [ref],
                             "subproblem_ids": scope, "semantic_key": "training.charge", "value": "both", "unit": ""},
        "evidence": evidence, "rationale": "The original sentence explicitly names both charged participants.",
    }]})
    adjudication = SourceAdjudicationResult.model_validate({"checks": [{
        "uncertainty_id": "U_training", "status": status, "evidence": evidence,
        "rationale": "Independent assessment of the complete charging relation.",
    }]})
    resolved, record = resolve_problem_sources(
        spec, interpretation=interpretation, adjudication=adjudication,
        input_catalog=catalog, evidence_profile=profile, work_dir=root,
    )
    original = ModelIR.model_validate_json(
        (Path(__file__).parent / "fixtures/model_ir/wuyi_anchor_bolt.json").read_text(encoding="utf-8")
    ).subproblems[0]
    model = ModelIR(model_id="source-first-model", subproblems=[original.model_copy(update={
        "subproblem_id": sid,
        "fact_bindings": [FactBindingSpec(fact_id="F_training", model_paths=["/objective"])]
        if sid in scope and resolved.locked_facts else [],
    }) for sid in identifiers])
    return spec, resolved, model, record, catalog


def _write(root, *, status="supported"):
    from app.guidance.model_review_validation import bind_model_review
    from app.guidance.stages import _build_reviewed_model_projection

    original, spec, model, record, catalog = _case(root, status=status)
    (root / "problem_spec_original.json").write_text(original.model_dump_json(indent=4), encoding="utf-8")
    spec_bytes = (json.dumps(spec.model_dump(mode="json"), ensure_ascii=False, indent=4) + "\n").encode("utf-8")
    (root / "problem_spec.json").write_bytes(spec_bytes)
    (root / "source_preflight.json").write_text(record.model_dump_json(indent=2), encoding="utf-8")
    (root / "model_ir.json").write_text(model.model_dump_json(indent=4), encoding="utf-8")
    review = ModelIRReview(model_id=model.model_id, subproblem_reviews=[{
        "subproblem_id": item.subproblem_id,
        "status": "revision_required" if item.subproblem_id == "repair" else "approved",
        "required_revisions": [{"requirement_id": "R_formula", "kind": "model", "basis_refs": [],
                                "description": "Repair the actual capacity formula.",
                                "acceptance_criteria": "The capacity relation is represented correctly."}]
        if item.subproblem_id == "repair" else [],
    } for item in model.subproblems])
    (root / "model_review.json").write_text(review.model_dump_json(indent=2), encoding="utf-8")
    projection = _build_reviewed_model_projection(model, review, spec)
    approved = ApprovedModelIR(
        review_status="partial", model_id=model.model_id,
        executable_subproblem_ids=[item.subproblem_id for item in projection.subproblems if item.execution_status == "solvable"],
        approved_model_ir=projection,
        review_binding=bind_model_review(review=review, model_ir=model, approved_model_ir=projection,
                                        problem_spec=spec, evidence_profile=EvidenceProfile().model_dump(mode="json"),
                                        input_catalog=catalog, revision_request=None, source_preflight=record),
    )
    (root / "approved_model_ir.json").write_text(approved.model_dump_json(indent=4), encoding="utf-8")
    return original, spec, model, review, record, spec_bytes


def test_mathematical_revision_preserves_resolved_problem_and_source_permission_bytes(tmp_path):
    from app.guidance.pipeline import _preserve_revision_request

    original, spec, model, review, record, spec_bytes = _write(tmp_path)
    tracked = {name: (tmp_path / name).read_bytes() for name in (
        "problem_spec_original.json", "source_preflight.json", "model_ir.json", "approved_model_ir.json",
    )}
    request = _preserve_revision_request(tmp_path, tmp_path / "model_ir.json")
    assert (tmp_path / "problem_spec.json").read_bytes() == spec_bytes
    assert (tmp_path / "problem_spec_initial.json").read_bytes() == spec_bytes
    assert (tmp_path / "model_ir_initial.json").read_bytes() == tracked["model_ir.json"]
    assert (tmp_path / "approved_model_ir_initial.json").read_bytes() == tracked["approved_model_ir.json"]
    assert (tmp_path / "problem_spec_original.json").read_bytes() == tracked["problem_spec_original.json"]
    assert (tmp_path / "source_preflight.json").read_bytes() == tracked["source_preflight.json"]
    assert original.uncertainties and not spec.uncertainties
    saved = ModelIRReview.model_validate_json(request.read_bytes())
    assert saved.subproblem_reviews == review.subproblem_reviews
    assert saved.problem_spec_ref == "problem_spec_initial.json"
    with pytest.raises(ValueError, match="one revision"):
        _preserve_revision_request(tmp_path, tmp_path / "model_ir.json")


def test_pending_source_permission_blocks_shared_scope_and_descendants_before_generation(tmp_path, monkeypatch):
    from app.guidance.modeler import LLMModeler

    _, spec, model, record, _ = _case(tmp_path, status="contradicted")
    (tmp_path / "source_preflight.json").write_text(record.model_dump_json(), encoding="utf-8")
    calls = []

    async def generate(**kwargs):
        sid = kwargs["subproblem"]["id"]
        calls.append(sid)
        assert sid == "independent"
        return ModelIR(model_id="independent-model", subproblems=[model.subproblems[-1]])

    monkeypatch.setattr("app.guidance.modeler._request_single_subproblem_model", generate)
    result = asyncio.run(LLMModeler(object()).model(
        problem_spec=spec, evidence_profile=EvidenceProfile().model_dump(mode="json"), task={}, work_dir=tmp_path,
    ))
    assert calls == ["independent"]
    assert [item.subproblem_id for item in result.subproblems if item.execution_status == "solvable"] == ["independent"]


def test_new_source_conflict_only_blocks_affected_scope_without_reopening_application(tmp_path):
    from app.guidance.stages import _build_reviewed_model_projection

    _, spec, model, _, _ = _case(tmp_path)
    review = ModelIRReview(model_id=model.model_id, subproblem_reviews=[{
        "subproblem_id": item.subproblem_id,
        "status": "blocked" if item.subproblem_id == "repair" else "approved",
        "blocking_issues": ["A new source conflict requires explicit handling."] if item.subproblem_id == "repair" else [],
        "source_conflicts": [{"basis_refs": ["problem_spec.json#locked_facts/F_training"],
                              "rationale": "A later review found a conflict; it cannot rewrite the source decision."}]
        if item.subproblem_id == "repair" else [],
    } for item in model.subproblems])
    projection = _build_reviewed_model_projection(model, review, spec)
    assert [item.subproblem_id for item in projection.subproblems if item.execution_status == "solvable"] == ["independent"]


def test_modeling_stage_reads_the_resolved_problem_during_mathematical_revision(tmp_path):
    from app.guidance.pipeline import _preserve_revision_request
    from app.guidance.stages import ModelingStage

    _, spec, model, _, record, _ = _write(tmp_path)
    request = _preserve_revision_request(tmp_path, tmp_path / "model_ir.json")
    captured = []

    class Modeler:
        async def revise(self, **kwargs):
            captured.append(kwargs)
            return model

    asyncio.run(ModelingStage(modeler=Modeler()).run(
        task_id="resolved-mathematical-revision", task={}, input_path=request, output_path=tmp_path / "model_ir.json",
    ))
    assert captured[0]["problem_spec"] == spec
    assert not captured[0]["problem_spec"].uncertainties
    assert captured[0]["previous_model_ir"] == model
    assert json.loads((tmp_path / "source_preflight.json").read_text(encoding="utf-8")) == record.model_dump(mode="json")


def test_source_preflight_survives_mathematical_stages_but_new_decomposition_discards_it(tmp_path):
    from app.guidance.recovery import invalidate_stage_outputs

    names = ("source_preflight.json", "problem_spec_original.json", "problem_spec_initial.json", "approved_model_ir_initial.json")
    for name in names:
        (tmp_path / name).write_text("{}", encoding="utf-8")
    for stage in ("modeling", "model_review"):
        invalidate_stage_outputs(tmp_path, stage)
        assert all((tmp_path / name).exists() for name in names)
    invalidate_stage_outputs(tmp_path, "decomposition")
    assert not any((tmp_path / name).exists() for name in names)


def test_pipeline_applies_source_before_modeling_and_keeps_one_mathematical_revision(tmp_path):
    from app.guidance.pipeline import GuidancePipeline
    from app.guidance.stages import ModelingStage, ModelReviewStage, SourceStage

    original, resolved, model, initial_review, record, _ = _write(tmp_path)
    calls = []

    class Decomposition:
        async def run(self, **kwargs):
            kwargs["output_path"].write_text(original.model_dump_json(), encoding="utf-8")
            return kwargs["output_path"]

    class Source:
        async def prepare_sources(self, **kwargs):
            calls.append("source")
            assert kwargs["problem_spec"] == original
            return resolved, record

    class Modeler:
        async def model(self, **kwargs):
            calls.append("model")
            assert kwargs["problem_spec"] == resolved
            return model

        async def revise(self, **kwargs):
            calls.append("revise")
            assert kwargs["problem_spec"] == resolved
            assert kwargs["review"].subproblem_reviews[1].required_revisions[0].kind == "model"
            return model

    class Reviewer:
        async def review(self, **kwargs):
            calls.append("review")
            if kwargs["revision_request"] is None:
                return initial_review
            return ModelIRReview(model_id=model.model_id, subproblem_reviews=[{
                "subproblem_id": item.subproblem_id,
                "status": "blocked" if item.subproblem_id == "repair" else "approved",
                "blocking_issues": ["A new source conflict cannot reopen source interpretation."]
                if item.subproblem_id == "repair" else [],
                "source_conflicts": [{"basis_refs": ["problem_spec.json#locked_facts/F_training"],
                                      "rationale": "A later source conflict requires explicit handling."}]
                if item.subproblem_id == "repair" else [],
                "revision_checks": [{"requirement_id": "R_formula", "status": "accepted",
                                     "model_paths": ["/objective"], "rationale": "The mathematical repair was addressed."}]
                if item.subproblem_id == "repair" else [],
            } for item in model.subproblems])

    class Calculation:
        async def run(self, **kwargs):
            calls.append("calculate")
            approved = ApprovedModelIR.model_validate_json(kwargs["input_path"].read_bytes())
            assert approved.executable_subproblem_ids == ["independent"]
            kwargs["output_path"].write_text("{}", encoding="utf-8")
            return kwargs["output_path"]

    class Output:
        async def run(self, **kwargs):
            kwargs["output_path"].write_text('{"status": "partial"}', encoding="utf-8")
            return kwargs["output_path"]

    pipeline = GuidancePipeline(
        decomposition_stage=Decomposition(), source_stage=SourceStage(source_adjudicator=Source()),
        modeling_stage=ModelingStage(modeler=Modeler()), model_review_stage=ModelReviewStage(reviewer=Reviewer()),
        calculation_stage=Calculation(), result_verification_stage=Output(), guidance_stage=Output(),
    )

    async def collect():
        return [event async for event in pipeline.run("source-first-single-revision", {"work_dir": str(tmp_path)})]

    events = asyncio.run(collect())
    assert calls == ["source", "model", "review", "revise", "review", "calculate"]
    assert events[-1]["data"]["status"] == "partial"
    assert ProblemSpec.model_validate_json((tmp_path / "problem_spec_original.json").read_bytes()) == original
    assert ProblemSpec.model_validate_json((tmp_path / "problem_spec_initial.json").read_bytes()) == resolved
    assert json.loads((tmp_path / "source_preflight.json").read_text(encoding="utf-8")) == record.model_dump(mode="json")
