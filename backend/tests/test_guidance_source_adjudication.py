from copy import deepcopy
import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.guidance.recovery import canonical_digest
from app.guidance.source_resolution import SourceResolutionRecord, resolve_problem_sources, validate_source_resolution
from test_guidance_source_resolution import SOURCE, QUOTE, _case, _correction


def test_genuine_quote_cannot_authorize_a_wrong_relation_without_adjudication(tmp_path):
    spec, catalog, profile = _case(tmp_path)
    replacement = _correction().replacement_fact.model_dump(mode="json")
    replacement.update(
        statement="训练费仅按新模块收取，指导者不另收费。",
        value="trainees_only",
    )
    wrong = _correction(replacement_fact=replacement)

    with pytest.raises(ValueError, match="adjudication|permission"):
        resolve_problem_sources(
            spec, [("sp1", wrong)], input_catalog=catalog,
            evidence_profile=profile, work_dir=tmp_path,
        )


def _result(proposals, status="supported"):
    from app.guidance.source_adjudication import SourceAdjudicationResult

    unique = {correction.correction_id: correction for _, correction in proposals}
    return SourceAdjudicationResult.model_validate({"checks": [
        {"correction_id": identifier, "status": status,
         "evidence": [item.model_dump(mode="json") for item in correction.evidence],
         "rationale": "Compare the replacement relation with the exact original source."}
        for identifier, correction in unique.items()
    ]})


def _permit(spec, proposals, catalog, profile, tmp_path, *, status="supported"):
    from app.guidance.source_adjudication import bind_source_adjudication

    return bind_source_adjudication(
        problem_spec=spec, proposals=proposals, input_catalog=catalog,
        evidence_profile=profile, work_dir=tmp_path, result=_result(proposals, status),
    )


def test_supported_correction_is_bound_applied_and_reloaded_with_original_owners(tmp_path):
    from app.guidance.source_adjudication import supported_source_proposals, validate_source_adjudication

    spec, catalog, profile = _case(tmp_path)
    first = _correction()
    second = _correction(correction_id="second_owner")
    proposals = [("sp2", first), ("sp1", first), ("sp2", second)]
    permit = _permit(spec, proposals, catalog, profile, tmp_path)

    assert len(permit.checks) == 2
    assert supported_source_proposals(permit) == proposals
    assert permit.source_files_sha256 == {
        "question.txt": catalog["source_sha256"], "cost.csv": profile["evidence"][0]["sha256"],
    }
    validate_source_adjudication(permit, problem_spec=spec, input_catalog=catalog,
                                 evidence_profile=profile, work_dir=tmp_path)
    resolved, record = resolve_problem_sources(
        spec, proposals, input_catalog=catalog, evidence_profile=profile,
        work_dir=tmp_path, adjudication=permit,
    )
    assert record.source_adjudication_sha256 == canonical_digest(permit)
    assert record.affected_subproblem_ids == ["sp1", "sp2", "downstream"]
    assert resolved.subproblems[3] == spec.subproblems[3]
    assert len([item for item in resolved.locked_facts if item.fact_id == "F_CHARGE_BASIS"]) == 1
    validate_source_resolution(record, previous_problem_spec=spec, problem_spec=resolved,
                               input_catalog=catalog, evidence_profile=profile,
                               work_dir=tmp_path, adjudication=permit)


@pytest.mark.parametrize("status", ["contradicted", "not_assessed"])
def test_unsupported_corrections_have_no_application_permission(tmp_path, status):
    from app.guidance.source_adjudication import supported_source_proposals

    spec, catalog, profile = _case(tmp_path)
    proposals = [("sp1", _correction())]
    permit = _permit(spec, proposals, catalog, profile, tmp_path, status=status)
    assert supported_source_proposals(permit) == []
    with pytest.raises(ValueError, match="supported|permission"):
        resolve_problem_sources(spec, proposals, input_catalog=catalog,
                                evidence_profile=profile, work_dir=tmp_path, adjudication=permit)


@pytest.mark.parametrize("change", ["problem", "catalog", "profile", "source", "proposal", "owner", "check"])
def test_changed_inputs_or_adjudication_invalidate_permission(tmp_path, change):
    spec, catalog, profile = _case(tmp_path)
    proposals = [("sp1", _correction())]
    permit = _permit(spec, proposals, catalog, profile, tmp_path)
    if change == "problem":
        spec = spec.model_copy(update={"task_summary": "Changed source context"})
    elif change == "catalog":
        catalog = deepcopy(catalog)
        catalog["blocks"][0]["block_id"] = "paragraph-4"
    elif change == "profile":
        profile = deepcopy(profile)
        profile["evidence"][0]["size_bytes"] += 1
    elif change == "source":
        (tmp_path / "question.txt").write_text("Changed source", encoding="utf-8")
    elif change == "proposal":
        proposals = [("sp1", _correction(rationale="Changed proposal"))]
    elif change == "owner":
        proposals = [("sp2", _correction())]
    else:
        permit.checks[0].rationale = "Changed semantic verdict"
    with pytest.raises(ValueError):
        resolve_problem_sources(spec, proposals, input_catalog=catalog,
                                evidence_profile=profile, work_dir=tmp_path, adjudication=permit)


@pytest.mark.parametrize("change", ["duplicate", "missing", "unknown", "quote", "no_evidence"])
def test_semantic_checks_require_exact_coverage_and_registered_quotes(tmp_path, change):
    from app.guidance.source_adjudication import SourceAdjudicationResult, bind_source_adjudication

    spec, catalog, profile = _case(tmp_path)
    proposals = [("sp1", _correction())]
    result = _result(proposals).model_dump(mode="json")
    if change == "duplicate":
        result["checks"].append(deepcopy(result["checks"][0]))
    elif change == "missing":
        result["checks"] = []
    elif change == "unknown":
        result["checks"][0]["correction_id"] = "unknown_correction"
    elif change == "quote":
        result["checks"][0]["evidence"][0]["quote"] = "Invented original quote"
    else:
        result["checks"][0]["evidence"] = []
    with pytest.raises(ValueError):
        bind_source_adjudication(problem_spec=spec, proposals=proposals, input_catalog=catalog,
                                 evidence_profile=profile, work_dir=tmp_path,
                                 result=SourceAdjudicationResult.model_validate(result))


def test_not_assessed_may_have_no_quote_but_cannot_authorize_application(tmp_path):
    from app.guidance.source_adjudication import SourceAdjudicationResult, bind_source_adjudication

    spec, catalog, profile = _case(tmp_path)
    proposals = [("sp1", _correction())]
    result = _result(proposals, "not_assessed").model_dump(mode="json")
    result["checks"][0]["evidence"] = []
    permit = bind_source_adjudication(
        problem_spec=spec, proposals=proposals, input_catalog=catalog, evidence_profile=profile,
        work_dir=tmp_path, result=SourceAdjudicationResult.model_validate(result),
    )
    assert permit.assessment_status == "reviewed"
    with pytest.raises(ValueError, match="supported|permission"):
        resolve_problem_sources(spec, proposals, input_catalog=catalog,
                                evidence_profile=profile, work_dir=tmp_path, adjudication=permit)


def test_old_resolution_record_without_semantic_permission_is_invalid(tmp_path):
    spec, catalog, profile = _case(tmp_path)
    proposals = [("sp1", _correction())]
    permit = _permit(spec, proposals, catalog, profile, tmp_path)
    _, record = resolve_problem_sources(spec, proposals, input_catalog=catalog,
                                        evidence_profile=profile, work_dir=tmp_path, adjudication=permit)
    old = record.model_dump(mode="json")
    del old["source_adjudication_sha256"]
    with pytest.raises(ValidationError, match="source_adjudication_sha256"):
        SourceResolutionRecord.model_validate(old)


class _Provider:
    max_tokens = 128

    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.requests = []

    async def chat(self, **kwargs):
        self.requests.append(kwargs)
        output = next(self.outputs)
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(output, ensure_ascii=False)), finish_reason="stop",
        )])


def test_adjudicator_uses_raw_sources_and_targets_with_bounded_semantic_retry(tmp_path):
    from app.guidance.source_adjudication import LLMSourceAdjudicator

    spec, catalog, profile = _case(tmp_path)
    proposals = [("sp1", _correction())]
    provider = _Provider([{"checks": []}, _result(proposals).model_dump(mode="json")])
    record = asyncio.run(LLMSourceAdjudicator(provider).adjudicate(
        problem_spec=spec, proposals=proposals, input_catalog=catalog,
        evidence_profile=profile, work_dir=tmp_path,
    ))
    assert record.assessment_status == "reviewed"
    assert len(provider.requests) == 2
    assert all(item["max_attempts"] == 1 and item["max_tokens"] == 128 for item in provider.requests)
    initial = json.loads(provider.requests[0]["history"][1]["content"])
    assert set(initial) == {"original_sources", "source_targets", "proposals"}
    assert initial["original_sources"][SOURCE]["content"].endswith(QUOTE)
    assert initial["source_targets"][0]["target_id"] == "UNC001"
    assert "model_ir" not in json.dumps(initial)
    assert not (tmp_path / "source_adjudication.json").exists()


def test_exhausted_request_returns_system_failure_without_invented_verdicts(tmp_path):
    from app.guidance.source_adjudication import LLMSourceAdjudicator, supported_source_proposals

    spec, catalog, profile = _case(tmp_path)
    proposals = [("sp1", _correction())]
    provider = _Provider([{"checks": []}, {"checks": []}])
    record = asyncio.run(LLMSourceAdjudicator(provider).adjudicate(
        problem_spec=spec, proposals=proposals, input_catalog=catalog,
        evidence_profile=profile, work_dir=tmp_path,
    ))
    assert len(provider.requests) == 2
    assert record.assessment_status == "not_assessed"
    assert record.checks == []
    assert "ContractGenerationError" in record.failure_reason
    assert supported_source_proposals(record) == []
    diagnostic = json.loads((tmp_path / "contract_diagnostics/GuidanceSourceAdjudicator.json").read_text(encoding="utf-8"))
    assert diagnostic["status"] == "failed"
    assert len(diagnostic["attempts"]) == 2
    with pytest.raises(ValueError, match="supported|permission"):
        resolve_problem_sources(spec, proposals, input_catalog=catalog,
                                evidence_profile=profile, work_dir=tmp_path, adjudication=record)


def test_supported_subset_can_apply_without_granting_permission_to_denied_proposals(tmp_path):
    from app.guidance.source_adjudication import SourceAdjudicationResult, bind_source_adjudication, supported_source_proposals

    spec, catalog, profile = _case(tmp_path)
    supported = _correction()
    denied = _correction(
        correction_id="wrong_cost", target_kind="locked_fact", target_id="F_COST",
        replacement_fact={**spec.locked_facts[0].model_dump(mode="json"), "value": 9},
        evidence=[{"source_ref": "cost.csv", "quote": "training\t8"}],
    )
    proposals = [("sp1", supported), ("sp2", denied)]
    result = _result(proposals).model_dump(mode="json")
    result["checks"][1]["status"] = "contradicted"
    permit = bind_source_adjudication(
        problem_spec=spec, proposals=proposals, input_catalog=catalog,
        evidence_profile=profile, work_dir=tmp_path, result=SourceAdjudicationResult.model_validate(result),
    )
    approved = supported_source_proposals(permit)
    assert approved == [("sp1", supported)]
    resolved, record = resolve_problem_sources(spec, approved, input_catalog=catalog,
                                               evidence_profile=profile, work_dir=tmp_path, adjudication=permit)
    assert resolved.locked_facts[0].value == 8
    validate_source_resolution(record, previous_problem_spec=spec, problem_spec=resolved,
                               input_catalog=catalog, evidence_profile=profile, work_dir=tmp_path,
                               adjudication=permit)
    with pytest.raises(ValueError, match="supported"):
        resolve_problem_sources(spec, proposals, input_catalog=catalog,
                                evidence_profile=profile, work_dir=tmp_path, adjudication=permit)


def test_adjudicator_binds_the_proposal_sent_before_awaiting_provider(tmp_path):
    from app.guidance.source_adjudication import LLMSourceAdjudicator

    spec, catalog, profile = _case(tmp_path)
    proposals = [("sp1", _correction())]
    original = proposals[0][1].model_copy(deep=True)

    class MutatingProvider(_Provider):
        async def chat(self, **kwargs):
            response = await super().chat(**kwargs)
            proposals[0][1].replacement_fact.value = "trainees_only"
            return response

    provider = MutatingProvider([_result(proposals).model_dump(mode="json")])
    permit = asyncio.run(LLMSourceAdjudicator(provider).adjudicate(
        problem_spec=spec, proposals=proposals, input_catalog=catalog,
        evidence_profile=profile, work_dir=tmp_path,
    ))
    assert permit.proposals[0].correction == original
    with pytest.raises(ValueError, match="supported"):
        resolve_problem_sources(spec, proposals, input_catalog=catalog,
                                evidence_profile=profile, work_dir=tmp_path, adjudication=permit)


def test_adjudicator_requires_a_proposal_before_making_requests(tmp_path):
    from app.guidance.source_adjudication import LLMSourceAdjudicator

    spec, catalog, profile = _case(tmp_path)
    provider = _Provider([])
    with pytest.raises(ValueError, match="proposal"):
        asyncio.run(LLMSourceAdjudicator(provider).adjudicate(
            problem_spec=spec, proposals=[], input_catalog=catalog,
            evidence_profile=profile, work_dir=tmp_path,
        ))
    assert provider.requests == []
