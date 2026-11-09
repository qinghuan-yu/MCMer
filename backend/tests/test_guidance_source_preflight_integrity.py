"""跨模块独立审计：请求使用的来源必须与磁盘许可相同且不可被await改写。"""

import asyncio
from copy import deepcopy
import json

import pytest

from app.guidance.model_ir import BlockedModelIR, ModelIR
from app.guidance.modeler import LLMModeler
from app.guidance.reviewer import LLMModelReviewer
from test_guidance_source_reviewer import SourceReviewLLM, _prepare


@pytest.mark.parametrize("source", ["catalog", "profile"])
def test_math_reviewer_rejects_in_memory_source_that_differs_from_permission(tmp_path, source):
    spec, model, _, catalog, profile = _prepare(tmp_path)
    if source == "catalog":
        catalog["blocks"][0]["text"] = "训练费仅按新模块收取，指导者免费。"
    else:
        profile["evidence"][0]["rows"][1][1] = "999"
    llm = SourceReviewLLM()
    try:
        review = asyncio.run(LLMModelReviewer(llm).review(
            problem_spec=spec, model_ir=model, input_catalog=catalog, evidence_profile=profile,
            task={}, work_dir=tmp_path,
        ))
    except ValueError:
        review = None
    assert llm.payloads == [], "unbound in-memory originals must not reach the reviewer"
    assert review is None or review.executable_subproblem_ids == []


def _blocked(target):
    return ModelIR(model_id="independent-audit", subproblems=[BlockedModelIR(
        execution_status="blocked", subproblem_id=target, objective="Audit only",
        blocking_reason="No numerical generation needed for input integrity audit.",
        missing_inputs=["unused"], recovery_action="Audit source input first.", expected_outputs=["model"],
    )])


def test_modeler_rejects_in_memory_profile_that_differs_from_permission(tmp_path, monkeypatch):
    spec, _, _, _, profile = _prepare(tmp_path)
    profile["evidence"][0]["rows"][1][1] = "999"
    calls = []

    async def capture(**kwargs):
        calls.append(deepcopy(kwargs["evidence_profile"]))
        return _blocked(kwargs["subproblem"]["id"])

    monkeypatch.setattr("app.guidance.modeler._request_single_subproblem_model", capture)
    try:
        result = asyncio.run(LLMModeler(object()).model(
            problem_spec=spec, evidence_profile=profile, task={}, work_dir=tmp_path,
        ))
    except ValueError:
        result = None
    assert calls == [], "unbound in-memory table must not reach mathematical generation"
    assert result is None or all(item.execution_status == "blocked" for item in result.subproblems)


@pytest.mark.parametrize("source", ["catalog", "profile", "spec"])
def test_reviewer_await_does_not_replace_the_source_used_by_later_subproblems(tmp_path, source):
    spec, model, _, catalog, profile = _prepare(tmp_path)
    original_spec = spec.model_dump(mode="json")
    original_catalog, original_profile = deepcopy(catalog), deepcopy(profile)

    class MutatingProvider(SourceReviewLLM):
        async def chat(self, **kwargs):
            response = await super().chat(**kwargs)
            if source == "catalog":
                catalog["blocks"][0]["text"] = "Changed caller source while awaiting"
            elif source == "profile":
                profile["evidence"][0]["rows"][1][1] = "999"
            else:
                spec.locked_facts[0].value = 999
            return response

    llm = MutatingProvider()
    asyncio.run(LLMModelReviewer(llm).review(
        problem_spec=spec, model_ir=model, input_catalog=catalog, evidence_profile=profile,
        task={}, work_dir=tmp_path,
    ))
    assert len(llm.payloads) == 4
    assert all(item["input_catalog"] == original_catalog for item in llm.payloads)
    assert all(item["evidence_profile"] == original_profile for item in llm.payloads)
    assert all(item["problem_spec"] == original_spec for item in llm.payloads)


@pytest.mark.parametrize("source", ["profile", "spec"])
def test_modeler_await_does_not_replace_the_source_used_by_independent_subproblems(tmp_path, monkeypatch, source):
    spec, _, _, _, profile = _prepare(tmp_path)
    original_spec = spec.model_dump(mode="json")
    original_profile = deepcopy(profile)
    calls = []

    async def capture(**kwargs):
        calls.append((kwargs["problem_spec"].model_dump(mode="json"), deepcopy(kwargs["evidence_profile"])))
        if source == "profile":
            profile["evidence"][0]["rows"][1][1] = "999"
        else:
            spec.locked_facts[0].value = 999
        return _blocked(kwargs["subproblem"]["id"])

    monkeypatch.setattr("app.guidance.modeler._request_single_subproblem_model", capture)
    asyncio.run(LLMModeler(object()).model(
        problem_spec=spec, evidence_profile=profile, task={}, work_dir=tmp_path,
    ))
    assert len(calls) == 2
    assert all(item == (original_spec, original_profile) for item in calls)


def test_concurrent_source_stage_cannot_apply_permission_twice_when_problem_is_unchanged(tmp_path):
    from app.guidance.source_resolution import resolve_problem_sources
    from app.guidance.stage_io import _write_model
    from app.guidance.stages import SourceStage
    from test_guidance_source_preflight import _checks, _decisions
    from test_guidance_source_resolution import _case

    spec, catalog, profile = _case(tmp_path)
    data = spec.model_dump(mode="json")
    data["uncertainties"] = [data["uncertainties"][1]]
    for subproblem in data["subproblems"]:
        subproblem["required_uncertainty_ids"] = [
            value for value in subproblem["required_uncertainty_ids"] if value != "UNC001"
        ]
    spec = type(spec).model_validate(data)
    # 原件确实欠定的到货数量保留歧义，已裁决ProblemSpec字节不变。
    path = tmp_path / "problem_spec.json"
    _write_model(path, spec)
    for name, value in (("input_catalog.json", catalog), ("evidence_profile.json", profile)):
        (tmp_path / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8", newline="\n")

    async def run_both():
        entered = asyncio.Event()

        class ConcurrentSource:
            calls = 0

            async def prepare_sources(self, **kwargs):
                self.calls += 1
                if self.calls == 2:
                    entered.set()
                await entered.wait()
                decisions = _decisions(kwargs["problem_spec"])
                return resolve_problem_sources(
                    kwargs["problem_spec"], interpretation=decisions, adjudication=_checks(decisions),
                    input_catalog=kwargs["input_catalog"], evidence_profile=kwargs["evidence_profile"], work_dir=tmp_path,
                )

        stage = SourceStage(source_adjudicator=ConcurrentSource())
        return await asyncio.gather(*[
            stage.run(task_id=f"concurrent-{index}", task={}, input_path=path, output_path=path)
            for index in range(2)
        ], return_exceptions=True)

    results = asyncio.run(run_both())
    assert sum(isinstance(item, ValueError) for item in results) == 1
    assert sum(item == path for item in results) == 1
