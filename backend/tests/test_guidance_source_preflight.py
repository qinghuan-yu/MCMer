"""来源前置完整覆盖、独立许可与应用边界。"""

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.guidance.recovery import canonical_digest
from app.guidance.source_resolution import (
    SourceAdjudicationResult, SourceInterpretationResult, SourcePreflightRecord,
    resolve_problem_sources, validate_source_preflight,
)

from test_guidance_source_resolution import SOURCE, QUOTE, _case


def _decisions(spec):
    return SourceInterpretationResult.model_validate({"decisions": [
        {"uncertainty_id": item.uncertainty_id, "subproblem_ids": item.subproblem_ids,
         "status": "specified" if item.uncertainty_id == "UNC001" else "ambiguous",
         "replacement_fact": {
             "fact_id": "F_CHARGE_BASIS", "statement": QUOTE, "source_refs": [SOURCE],
             "subproblem_ids": item.subproblem_ids, "semantic_key": item.semantic_key,
             "value": "trainees_and_instructors", "unit": "",
         } if item.uncertainty_id == "UNC001" else None,
         "evidence": [{"source_ref": SOURCE, "quote": QUOTE}],
         "rationale": "原文明确两类训练对象收费。" if item.uncertainty_id == "UNC001" else "原件只描述费用规则，没有未来到货数量。"}
        for item in spec.uncertainties
    ]})


def _checks(decisions, status="supported"):
    return SourceAdjudicationResult.model_validate({"checks": [
        {"uncertainty_id": item.uncertainty_id, "status": status,
         "evidence": [entry.model_dump(mode="json") for entry in item.evidence],
         "rationale": "独立核对原文的完整适用对象，或明确缺少未来到货数量。"}
        for item in decisions.decisions if item.status != "not_assessed"
    ]})


def _ready(spec, catalog, profile, tmp_path):
    decisions = _decisions(spec)
    return resolve_problem_sources(spec, interpretation=decisions, adjudication=_checks(decisions),
                                   input_catalog=catalog, evidence_profile=profile, work_dir=tmp_path)


def _resolve(spec, catalog, profile, tmp_path, decisions, checks):
    return resolve_problem_sources(spec, interpretation=decisions, adjudication=checks,
                                   input_catalog=catalog, evidence_profile=profile, work_dir=tmp_path)


def test_uncertainties_cannot_receive_permission_from_empty_optional_proposals(tmp_path):
    spec, catalog, profile = _case(tmp_path)
    with pytest.raises(ValueError, match="cover|uncertaint"):
        _resolve(spec, catalog, profile, tmp_path,
                 SourceInterpretationResult(decisions=[]), SourceAdjudicationResult(checks=[]))


def test_full_decisions_apply_shared_fact_once_and_preserve_genuine_ambiguity(tmp_path):
    spec, catalog, profile = _case(tmp_path)
    initial = spec.model_dump(mode="json")
    resolved, record = _ready(spec, catalog, profile, tmp_path)
    assert spec.model_dump(mode="json") == initial
    assert record.original_problem_spec == spec and record.original_problem_spec is not spec
    assert record.status == "ready" and record.blocked_subproblem_ids == []
    assert record.affected_subproblem_ids == ["sp1", "sp2", "downstream"]
    assert [item.uncertainty_id for item in resolved.uncertainties] == ["REAL_UNCERTAINTY"]
    assert [item.fact_id for item in resolved.locked_facts] == ["F_COST", "F_CHARGE_BASIS"]
    assert resolved.locked_facts[-1].subproblem_ids == ["sp1", "sp2"]
    assert resolved.subproblems[0].required_fact_ids == ["F_COST", "F_CHARGE_BASIS"]
    assert resolved.subproblems[0].required_uncertainty_ids == []
    assert record.resolved_problem_spec_sha256 == canonical_digest(resolved)
    validate_source_preflight(record, problem_spec=resolved, input_catalog=catalog,
                              evidence_profile=profile, work_dir=tmp_path)


@pytest.mark.parametrize("change", ["missing", "duplicate", "unknown", "order", "owner", "fact_owner", "semantic", "quote", "fact_id", "references"])
def test_incomplete_or_misattributed_source_interpretations_are_rejected(tmp_path, change):
    spec, catalog, profile = _case(tmp_path)
    data = _decisions(spec).model_dump(mode="json")
    decision = data["decisions"][0]
    if change == "missing":
        data["decisions"].pop()
    elif change == "duplicate":
        data["decisions"].append(deepcopy(decision))
    elif change == "unknown":
        decision["uncertainty_id"] = "UNKNOWN"
    elif change == "order":
        data["decisions"].reverse()
    elif change == "owner":
        decision["subproblem_ids"] = ["sp1"]
    elif change == "fact_owner":
        decision["replacement_fact"]["subproblem_ids"] = ["sp1"]
    elif change == "semantic":
        decision["replacement_fact"]["semantic_key"] = "different_quantity"
    elif change == "quote":
        decision["evidence"][0]["quote"] = "Invented source statement"
    elif change == "fact_id":
        decision["replacement_fact"]["fact_id"] = "F_COST"
    else:
        decision["replacement_fact"]["source_refs"] = ["cost.csv"]
    decisions = SourceInterpretationResult.model_validate(data)
    with pytest.raises(ValueError):
        _resolve(spec, catalog, profile, tmp_path, decisions, _checks(decisions))


@pytest.mark.parametrize("change", ["missing", "duplicate", "unknown", "order", "quote", "no_evidence"])
def test_adjudication_must_cover_both_specified_and_ambiguous_decisions(tmp_path, change):
    spec, catalog, profile = _case(tmp_path)
    decisions = _decisions(spec)
    data = _checks(decisions).model_dump(mode="json")
    if change == "missing":
        data["checks"].pop()
    elif change == "duplicate":
        data["checks"].append(deepcopy(data["checks"][0]))
    elif change == "unknown":
        data["checks"][0]["uncertainty_id"] = "UNKNOWN"
    elif change == "order":
        data["checks"].reverse()
    elif change == "quote":
        data["checks"][0]["evidence"][0]["quote"] = "Invented source statement"
    else:
        data["checks"][0]["evidence"] = []
    with pytest.raises(ValueError):
        _resolve(spec, catalog, profile, tmp_path, decisions, SourceAdjudicationResult.model_validate(data))


@pytest.mark.parametrize("wrong_decision", ["false_ambiguity", "false_exemption"])
@pytest.mark.parametrize("verdict", ["contradicted", "not_assessed"])
def test_false_ambiguity_or_genuine_quote_with_wrong_fact_cannot_be_applied(tmp_path, wrong_decision, verdict):
    spec, catalog, profile = _case(tmp_path)
    decisions = _decisions(spec)
    if wrong_decision == "false_ambiguity":
        decisions.decisions[0].status = "ambiguous"
        decisions.decisions[0].replacement_fact = None
        decisions.decisions[0].rationale = "即使正文明确双对象收费，也沿用拆题的三种候选。"
    else:
        decisions.decisions[0].replacement_fact.statement = "指导者不收取训练费。"
        decisions.decisions[0].replacement_fact.value = "trainees_only"
    checks = _checks(decisions)
    checks.checks[0].status = verdict
    checks.checks[0].rationale = "原文明确训练成本同时对新模块和指导者计收，待审决定与之冲突。"
    resolved, record = _resolve(spec, catalog, profile, tmp_path, decisions, checks)
    assert resolved == spec
    assert record.status == "partial"
    assert record.blocked_subproblem_ids == ["sp1", "sp2", "downstream"]
    assert record.affected_subproblem_ids == []
    assert "independent" not in record.blocked_subproblem_ids


def test_unassessed_decision_stays_blocked_without_synthetic_adjudication(tmp_path):
    spec, catalog, profile = _case(tmp_path)
    decisions = _decisions(spec)
    decisions.decisions[0].status = "not_assessed"
    decisions.decisions[0].replacement_fact = None
    decisions.decisions[0].evidence = []
    resolved, record = _resolve(spec, catalog, profile, tmp_path, decisions, _checks(decisions))
    assert resolved == spec
    assert [item.uncertainty_id for item in record.adjudication.checks] == ["REAL_UNCERTAINTY"]
    assert record.blocked_subproblem_ids == ["sp1", "sp2", "downstream"]


@pytest.mark.parametrize("change", ["original", "resolved", "catalog", "profile", "source", "decision", "owner", "check", "blocked"])
def test_bound_source_record_rejects_changed_inputs_or_decisions(tmp_path, change):
    spec, catalog, profile = _case(tmp_path)
    resolved, record = _ready(spec, catalog, profile, tmp_path)
    if change == "original":
        record.original_problem_spec.task_summary = "Changed original"
    elif change == "resolved":
        resolved.task_summary = "Changed resolved"
    elif change == "catalog":
        catalog["blocks"][0]["block_id"] = "paragraph-99"
    elif change == "profile":
        profile["evidence"][0]["size_bytes"] += 1
    elif change == "source":
        (tmp_path / "question.txt").write_text("Changed source", encoding="utf-8")
    elif change == "decision":
        record.interpretation.decisions[0].replacement_fact.value = "trainees_only"
    elif change == "owner":
        record.interpretation.decisions[0].subproblem_ids = ["sp1"]
    elif change == "check":
        record.adjudication.checks[0].status = "contradicted"
    else:
        record.blocked_subproblem_ids = ["independent"]
    with pytest.raises(ValueError):
        validate_source_preflight(record, problem_spec=resolved, input_catalog=catalog,
                                  evidence_profile=profile, work_dir=tmp_path)


def test_old_post_review_permission_snapshot_is_invalid():
    with pytest.raises(ValidationError):
        SourcePreflightRecord.model_validate({"proposals": [], "checks": [], "assessment_status": "reviewed"})


class _Provider:
    max_tokens = 128

    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.requests = []

    async def chat(self, **kwargs):
        self.requests.append(deepcopy(kwargs))
        output = next(self.outputs)
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(output, ensure_ascii=False)), finish_reason="stop",
        )])


def _prepare(provider, spec, catalog, profile, tmp_path):
    from app.guidance.source_adjudication import LLMSourceAdjudicator

    return asyncio.run(LLMSourceAdjudicator(provider).prepare_sources(
        problem_spec=spec, input_catalog=catalog, evidence_profile=profile, work_dir=tmp_path,
    ))


def test_two_independent_requests_have_full_sources_without_model_or_answer(tmp_path):
    spec, catalog, profile = _case(tmp_path)
    decisions = _decisions(spec)
    provider = _Provider([decisions.model_dump(mode="json"), _checks(decisions).model_dump(mode="json")])
    _, record = _prepare(provider, spec, catalog, profile, tmp_path)
    assert record.status == "ready"
    assert len(provider.requests) == 2
    assert [item["agent_name"] for item in provider.requests] == ["GuidanceSourceInterpreter", "GuidanceSourceAdjudicator"]
    payloads = [json.loads(item["history"][1]["content"]) for item in provider.requests]
    assert set(payloads[0]) == {"original_sources", "source_targets", "reserved_fact_ids"}
    assert set(payloads[1]) == {"original_sources", "source_targets", "reserved_fact_ids", "decisions"}
    assert payloads[0]["original_sources"] == payloads[1]["original_sources"]
    assert payloads[0]["original_sources"][SOURCE]["content"].endswith(QUOTE)
    assert payloads[1]["decisions"] == decisions.model_dump(mode="json")["decisions"]
    assert all(item["max_attempts"] == 1 and item["max_tokens"] == 128 for item in provider.requests)


def test_missing_interpretation_item_retries_once_with_same_budget(tmp_path):
    spec, catalog, profile = _case(tmp_path)
    decisions = _decisions(spec)
    provider = _Provider([{"decisions": []}, decisions.model_dump(mode="json"), _checks(decisions).model_dump(mode="json")])
    _, record = _prepare(provider, spec, catalog, profile, tmp_path)
    assert record.status == "ready" and len(provider.requests) == 3
    assert [item["agent_name"] for item in provider.requests].count("GuidanceSourceInterpreter") == 2
    assert all(item["max_tokens"] == 128 for item in provider.requests)


@pytest.mark.parametrize("phase", ["interpretation", "adjudication"])
def test_request_failure_records_no_invented_verdict_and_blocks_all_uncertainty_owners(tmp_path, phase):
    spec, catalog, profile = _case(tmp_path)
    decisions = _decisions(spec)
    outputs = [{"decisions": []}, {"decisions": []}] if phase == "interpretation" else [
        decisions.model_dump(mode="json"), {"checks": []}, {"checks": []}]
    provider = _Provider(outputs)
    resolved, record = _prepare(provider, spec, catalog, profile, tmp_path)
    assert resolved == spec and record.status == "blocked"
    assert record.blocked_subproblem_ids == ["sp1", "sp2", "downstream", "independent"]
    assert getattr(record, f"{phase}_failure_reason")
    assert getattr(record, phase) is None
    assert len(provider.requests) == (2 if phase == "interpretation" else 3)
    validate_source_preflight(record, problem_spec=resolved, input_catalog=catalog,
                              evidence_profile=profile, work_dir=tmp_path)


def test_no_uncertainties_produces_complete_empty_record_without_provider_call(tmp_path):
    spec, catalog, profile = _case(tmp_path)
    data = spec.model_dump(mode="json")
    data["uncertainties"] = []
    for item in data["subproblems"]:
        item["required_uncertainty_ids"] = []
    spec = type(spec).model_validate(data)
    provider = _Provider([])
    resolved, record = _prepare(provider, spec, catalog, profile, tmp_path)
    assert resolved == spec and record.status == "ready" and provider.requests == []
    assert record.interpretation.decisions == record.adjudication.checks == []


def test_async_requests_bind_immutable_original_inputs(tmp_path):
    spec, catalog, profile = _case(tmp_path)
    original = spec.model_copy(deep=True)
    decisions = _decisions(spec)

    class MutatingProvider(_Provider):
        async def chat(self, **kwargs):
            output = await super().chat(**kwargs)
            spec.uncertainties[0].statement = "Changed while awaiting"
            catalog["blocks"][0]["text"] = "Changed caller catalog"
            return output

    provider = MutatingProvider([decisions.model_dump(mode="json"), _checks(decisions).model_dump(mode="json")])
    _, record = _prepare(provider, spec, catalog, profile, tmp_path)
    assert record.status == "ready" and record.original_problem_spec == original
    payload = json.loads(provider.requests[1]["history"][1]["content"])
    assert payload["source_targets"][0]["statement"] == original.uncertainties[0].statement
