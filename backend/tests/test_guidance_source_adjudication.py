"""来源解释与独立复核的请求边界与失败语义。"""

from copy import deepcopy
import json

import pytest

from app.guidance.source_resolution import SourceAdjudicationResult, SourceInterpretationResult, resolve_problem_sources
from test_guidance_source_preflight import _Provider, _checks, _decisions, _prepare
from test_guidance_source_resolution import SOURCE, QUOTE, _case


def test_interpretation_receives_existing_fact_ids_to_avoid_accidental_overwrite(tmp_path):
    spec, catalog, profile = _case(tmp_path)
    decisions = _decisions(spec)
    provider = _Provider([decisions.model_dump(mode="json"), _checks(decisions).model_dump(mode="json")])
    _prepare(provider, spec, catalog, profile, tmp_path)
    payload = json.loads(provider.requests[0]["history"][1]["content"])
    assert payload.get("reserved_fact_ids") == ["F_COST"]


def test_raw_semantic_contradiction_blocks_false_ambiguity_without_retrying_a_new_proposal(tmp_path):
    spec, catalog, profile = _case(tmp_path)
    decisions = _decisions(spec)
    decisions.decisions[0].status = "ambiguous"
    decisions.decisions[0].replacement_fact = None
    decisions.decisions[0].rationale = "拆题列出了三个收费口径，因此保留三个情景。"
    verdicts = _checks(decisions)
    verdicts.checks[0].status = "contradicted"
    verdicts.checks[0].rationale = "原文同时对新模块和指导者收费，候选列表不能制造真实歧义。"
    provider = _Provider([decisions.model_dump(mode="json"), verdicts.model_dump(mode="json")])
    resolved, record = _prepare(provider, spec, catalog, profile, tmp_path)
    assert resolved == spec
    assert record.blocked_subproblem_ids == ["sp1", "sp2", "downstream"]
    assert len(provider.requests) == 2
    assert record.adjudication.checks[0].status == "contradicted"


def test_genuine_quote_with_false_exemption_is_rejected_by_independent_raw_result(tmp_path):
    spec, catalog, profile = _case(tmp_path)
    decisions = _decisions(spec)
    decisions.decisions[0].replacement_fact.statement = "仅对新模块收费，指导者不另收训练费。"
    decisions.decisions[0].replacement_fact.value = "trainees_only"
    verdicts = _checks(decisions)
    verdicts.checks[0].status = "contradicted"
    verdicts.checks[0].rationale = "免保养费不等于免训练费，完整引文与新事实冲突。"
    provider = _Provider([decisions.model_dump(mode="json"), verdicts.model_dump(mode="json")])
    resolved, record = _prepare(provider, spec, catalog, profile, tmp_path)
    assert resolved == spec and record.status == "partial"
    payload = json.loads(provider.requests[1]["history"][1]["content"])
    assert payload["decisions"][0]["replacement_fact"]["value"] == "trainees_only"
    assert payload["original_sources"][SOURCE]["content"].endswith(QUOTE)


def test_all_not_assessed_results_do_not_make_an_empty_semantic_request(tmp_path):
    spec, catalog, profile = _case(tmp_path)
    payload = _decisions(spec).model_dump(mode="json")
    for item in payload["decisions"]:
        item.update(status="not_assessed", replacement_fact=None, evidence=[], rationale="未能完成语义判断。")
    provider = _Provider([payload])
    resolved, record = _prepare(provider, spec, catalog, profile, tmp_path)
    assert len(provider.requests) == 1
    assert resolved == spec and record.status == "blocked"
    assert record.adjudication.checks == []


def test_not_assessed_cannot_be_upgraded_by_an_extra_semantic_check(tmp_path):
    spec, catalog, profile = _case(tmp_path)
    decisions = _decisions(spec)
    checks = _checks(decisions)
    decisions.decisions[0].status = "not_assessed"
    decisions.decisions[0].replacement_fact = None
    with pytest.raises(ValueError, match="cover"):
        resolve_problem_sources(spec, interpretation=decisions, adjudication=checks,
                                input_catalog=catalog, evidence_profile=profile, work_dir=tmp_path)


@pytest.mark.parametrize("failure", [None, "Failed request"])
def test_failed_interpretation_cannot_invent_a_semantic_result(tmp_path, failure):
    spec, catalog, profile = _case(tmp_path)
    with pytest.raises(ValueError, match="failure|adjudication"):
        resolve_problem_sources(spec, interpretation=None, adjudication=SourceAdjudicationResult(checks=[]),
                                interpretation_failure_reason=failure,
                                input_catalog=catalog, evidence_profile=profile, work_dir=tmp_path)


def test_missing_adjudication_cannot_silently_authorize_interpretation(tmp_path):
    spec, catalog, profile = _case(tmp_path)
    with pytest.raises(ValueError, match="independent|permission"):
        resolve_problem_sources(spec, interpretation=_decisions(spec), adjudication=None,
                                input_catalog=catalog, evidence_profile=profile, work_dir=tmp_path)


@pytest.mark.parametrize("status,replacement,evidence", [
    ("specified", None, [{"source_ref": SOURCE, "quote": QUOTE}]),
    ("ambiguous", "fact", [{"source_ref": SOURCE, "quote": QUOTE}]),
    ("ambiguous", None, []),
    ("not_assessed", "fact", []),
])
def test_source_decisions_preserve_distinct_content_requirements(tmp_path, status, replacement, evidence):
    spec, _, _ = _case(tmp_path)
    data = _decisions(spec).model_dump(mode="json")
    fact = deepcopy(data["decisions"][0]["replacement_fact"])
    data["decisions"][0].update(status=status, replacement_fact=fact if replacement else None, evidence=evidence)
    with pytest.raises(ValueError):
        SourceInterpretationResult.model_validate(data)
