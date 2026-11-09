import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_workflow_budget_exposes_ordered_guidance_stage_timeouts() -> None:
    from app.core.workflow_budget import resolve_workflow_budget

    fast = resolve_workflow_budget("fast")
    standard = resolve_workflow_budget("standard")
    strict = resolve_workflow_budget("strict")

    assert not hasattr(standard, "llm_timeout")
    for budget in (fast, standard, strict):
        guidance = budget.guidance
        assert all(value > 0 for value in (
            guidance.decomposition,
            guidance.architecture,
            guidance.indexed_core,
            guidance.indexed_constraints,
            guidance.review,
        ))
        assert guidance.indexed_constraints >= guidance.architecture

    assert standard.guidance.indexed_constraints > 120
    assert fast.guidance.indexed_constraints < standard.guidance.indexed_constraints
    assert standard.guidance.indexed_constraints < strict.guidance.indexed_constraints


@pytest.mark.parametrize(("stage", "observed_seconds"), [("decomposition", 133.828), ("review", 207.407)])
def test_standard_timeouts_cover_recorded_flash_facts_and_review(monkeypatch, stage, observed_seconds) -> None:
    from app.core.workflow_budget import resolve_workflow_budget, settings

    monkeypatch.setattr(settings, "LLM_REQUEST_TIMEOUT", 1)
    budget = resolve_workflow_budget("standard").guidance
    # R3事实提取133.828秒；R5.2漏费审核207.407秒，均取得有效完整响应。
    assert getattr(budget, stage) > observed_seconds


def test_default_guidance_pipeline_injects_each_stage_timeout(monkeypatch) -> None:
    from app.core.workflow_budget import resolve_workflow_budget
    from app.guidance import workflow

    class FakeLLM:
        def __init__(self, **kwargs) -> None:
            self.request_timeout = kwargs["request_timeout"]

    monkeypatch.setattr(workflow, "LLM", FakeLLM)
    budget = resolve_workflow_budget("standard").guidance

    pipeline = workflow.build_default_guidance_pipeline("standard")
    decomposer = pipeline._stages["decomposition"].decomposer
    modeler = pipeline._stages["modeling"].modeler
    reviewer = pipeline._stages["model_review"].reviewer

    assert decomposer.request_timeout == budget.decomposition
    assert modeler.architecture_timeout == budget.architecture
    assert modeler.indexed_core_timeout == budget.indexed_core
    assert modeler.indexed_constraints_timeout == budget.indexed_constraints
    assert reviewer.request_timeout == budget.review


def test_contract_diagnostic_records_explicit_stage_timeout(tmp_path: Path) -> None:
    from app.guidance.contract_request import (
        ContractRequestTimeout,
        StructuredContractRequester,
    )
    from app.guidance.contracts import ProblemSpec

    class HangingLLM:
        request_timeout = 99

        async def chat(self, **kwargs):
            await asyncio.sleep(60)

    requester = StructuredContractRequester(
        llm=HangingLLM(),
        model_type=ProblemSpec,
        system_prompt="decompose problem",
        agent_name="GuidanceDecomposer",
        artifact_dir=tmp_path,
        request_timeout=0.01,
    )

    with pytest.raises(ContractRequestTimeout, match="0.01s"):
        asyncio.run(requester.request({"question": "test"}))

    diagnostic = json.loads(
        (tmp_path / "contract_diagnostics" / "GuidanceDecomposer.json").read_text(
            encoding="utf-8"
        )
    )
    assert diagnostic["request_timeout_seconds"] == 0.01
    assert {
        attempt["request_timeout_seconds"]
        for attempt in diagnostic["attempts"]
    } == {0.01}


def test_successful_contract_request_publishes_stage_timeout_diagnostic(
    tmp_path: Path,
) -> None:
    from app.guidance.contract_request import StructuredContractRequester
    from app.guidance.contracts import ProblemSpec

    class SuccessfulLLM:
        request_timeout = 99

        async def chat(self, **kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content=json.dumps({
                    "task_summary": "Solve one problem.",
                    "subproblems": [{
                        "id": "problem1",
                        "required_fact_ids": [],
                        "required_uncertainty_ids": [],
                        "depends_on_subproblem_ids": [],
                    }],
                })
            ))])

    requester = StructuredContractRequester(
        llm=SuccessfulLLM(),
        model_type=ProblemSpec,
        system_prompt="decompose problem",
        agent_name="GuidanceDecomposer",
        artifact_dir=tmp_path,
        request_timeout=12,
    )

    result = asyncio.run(requester.request({"question": "test"}))

    assert result.task_summary == "Solve one problem."
    diagnostic = json.loads(
        (tmp_path / "contract_diagnostics" / "GuidanceDecomposer.json").read_text(
            encoding="utf-8"
        )
    )
    assert diagnostic["status"] == "valid"
    assert diagnostic["request_timeout_seconds"] == 12
    assert diagnostic["attempts"] == [{
        "attempt": 1,
        "transport_attempt": 1,
        "status": "valid",
        "request_timeout_seconds": 12,
        "request_max_tokens": None,
        "finish_reason": None,
        "usage": None,
    }]
