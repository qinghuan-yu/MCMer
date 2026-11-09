import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.guidance.contract_request import ContractGenerationError, StructuredContractRequester


class Answer(BaseModel):
    value: int


def _response(content='{"value": 7}', *, finish="stop", completion=30, reasoning=20):
    return SimpleNamespace(
        model="deepseek-flash",
        choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=completion, total_tokens=10 + completion,
                              completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning)),
    )


class ScriptedLLM:
    model = "deepseek/deepseek-flash"
    max_tokens = 100

    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def _request(root, llm, **kwargs):
    return asyncio.run(StructuredContractRequester(
        llm=llm, model_type=Answer, system_prompt="Return a complete answer.",
        agent_name="BudgetTest", artifact_dir=root, **kwargs,
    ).request({"question": "test"}))


def _diagnostic(root):
    return json.loads((root / "contract_diagnostics/BudgetTest.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("content", ['', '{"value": 7}'])
def test_length_never_accepts_or_retries_at_fixed_token_ceiling(tmp_path, content):
    llm = ScriptedLLM([_response(content, finish="length", completion=100, reasoning=100), _response()])
    with pytest.raises(ContractGenerationError, match="truncat"):
        _request(tmp_path, llm)
    assert len(llm.calls) == 1
    diagnostic = _diagnostic(tmp_path)
    assert diagnostic["attempts"][0]["failure_kind"] == "truncation"
    assert diagnostic["attempts"][0]["finish_reason"] == "length"
    assert not list((tmp_path / ".recovery/contracts").glob("*.json"))


@pytest.mark.parametrize(("content", "failure_kind"), [
    ('{"value":', "syntax"), ('{"value": "wrong"}', "schema"), ('{"value": -1}', "semantic"),
])
def test_recovery_records_failure_class_and_usage_without_double_counting_reasoning(tmp_path, content, failure_kind):
    llm = ScriptedLLM([_response(content), _response()])

    def validate(answer):
        if answer.value < 0:
            raise ValueError("value must be positive")

    assert _request(tmp_path, llm, semantic_validator=validate).value == 7
    diagnostic = _diagnostic(tmp_path)
    assert diagnostic["attempts"][0]["failure_kind"] == failure_kind
    assert diagnostic["usage"]["completion_tokens"] == 60
    assert diagnostic["usage"]["reasoning_tokens"] == 40
    assert diagnostic["usage"]["total_tokens"] == 80
    assert diagnostic["provider_calls"] == 2
    correction = json.loads(llm.calls[1]["history"][1]["content"])
    assert correction["failure_kind"] == failure_kind


def test_timeout_then_bad_contract_cannot_start_a_third_provider_call(tmp_path):
    llm = ScriptedLLM([asyncio.TimeoutError(), _response('{bad'), _response()])
    with pytest.raises(ContractGenerationError):
        _request(tmp_path, llm, request_timeout=1)
    assert len(llm.calls) == 2
    diagnostic = _diagnostic(tmp_path)
    assert [item["failure_kind"] for item in diagnostic["attempts"]] == ["timeout", "syntax"]
    assert diagnostic["usage_complete"] is False
    assert diagnostic["reserved_completion_tokens"] == 200


def test_core_transport_does_not_hide_retries_from_guidance_budget(tmp_path, monkeypatch):
    import app.core.llm.llm as llm_module
    from litellm.exceptions import APIError

    calls = []

    async def unavailable(**kwargs):
        calls.append(kwargs)
        raise APIError(status_code=503, message="unavailable", llm_provider="deepseek", model="deepseek-flash")

    async def no_wait(seconds):
        pass

    monkeypatch.setattr(llm_module, "acompletion", unavailable)
    monkeypatch.setattr(llm_module.asyncio, "sleep", no_wait)
    llm = llm_module.LLM(model="deepseek/deepseek-flash", api_key="test", base_url="https://example.invalid",
                         max_tokens=100, request_timeout=1)
    with pytest.raises(APIError):
        _request(tmp_path, llm)
    assert len(calls) == 1
    assert calls[0]["num_retries"] == 0
    assert _diagnostic(tmp_path)["attempts"][0]["failure_kind"] == "transport"


def test_truncation_uses_only_the_explicit_recovery_allowance(tmp_path):
    from app.core.workflow_budget import ContractTokenBudget

    llm = ScriptedLLM([_response('', finish="length", completion=30, reasoning=30), _response()])
    assert _request(tmp_path, llm, token_budget=ContractTokenBudget(30, 100)).value == 7
    assert [call["max_tokens"] for call in llm.calls] == [30, 100]
    assert llm.calls[0]["history"] == llm.calls[1]["history"]
    assert llm.max_tokens == 100
    diagnostic = _diagnostic(tmp_path)
    assert diagnostic["status"] == "recovered"
    assert diagnostic["reserved_completion_tokens"] == 130
    assert diagnostic["max_reserved_completion_tokens"] == 130
    assert diagnostic["attempts"][0]["truncation_kind"] == "reasoning_exhausted"


def test_repeated_length_stops_at_two_calls_without_caching(tmp_path):
    from app.core.workflow_budget import ContractTokenBudget

    llm = ScriptedLLM([_response('{"value":', finish="length"), _response('', finish="length")])
    with pytest.raises(ContractGenerationError, match="truncat"):
        _request(tmp_path, llm, token_budget=ContractTokenBudget(30, 100))
    assert len(llm.calls) == 2
    assert _diagnostic(tmp_path)["attempts"][0]["truncation_kind"] == "output_truncated"
    assert not list((tmp_path / ".recovery/contracts").glob("*.json"))


def test_syntax_recovery_does_not_increase_tokens_and_policy_change_invalidates_cache(tmp_path):
    from app.core.workflow_budget import ContractTokenBudget

    llm = ScriptedLLM([_response('{bad'), _response(), _response()])
    _request(tmp_path, llm, token_budget=ContractTokenBudget(30, 100))
    assert [call["max_tokens"] for call in llm.calls] == [30, 30]
    _request(tmp_path, llm, token_budget=ContractTokenBudget(30, 100))
    assert len(llm.calls) == 2
    _request(tmp_path, llm, token_budget=ContractTokenBudget(50, 100))
    assert len(llm.calls) == 3


def test_absent_usage_is_reported_as_unknown(tmp_path):
    response = _response()
    del response.usage
    _request(tmp_path, ScriptedLLM([response]))
    diagnostic = _diagnostic(tmp_path)
    assert diagnostic["usage_complete"] is False
    assert diagnostic["usage"]["completion_tokens"] is None


def test_pipeline_uses_stage_specific_evidence_based_token_budgets(monkeypatch):
    from app.core.workflow_budget import resolve_workflow_budget
    from app.guidance import workflow

    monkeypatch.setattr(workflow, "LLM", lambda **kwargs: SimpleNamespace(**kwargs))
    pipeline = workflow.build_default_guidance_pipeline()
    budget = resolve_workflow_budget("standard").guidance
    decomposer = pipeline._stages["decomposition"].decomposer
    modeler = pipeline._stages["modeling"].modeler
    reviewer = pipeline._stages["model_review"].reviewer
    assert decomposer.structure_token_budget == budget.structure_tokens
    assert decomposer.facts_token_budget == budget.facts_tokens
    assert modeler.architecture_token_budget == budget.architecture_tokens
    assert modeler.indexed_core_token_budget == budget.indexed_core_tokens
    assert modeler.indexed_constraints_token_budget == budget.indexed_constraints_tokens
    assert reviewer.token_budget == budget.review_tokens
    assert budget.structure_tokens.initial >= 8692
    assert budget.facts_tokens.initial >= 24243
    assert budget.review_tokens.initial >= 26208
    assert budget.indexed_constraints_tokens.initial >= 30670
    assert budget.indexed_core_tokens.initial < budget.architecture_tokens.initial < budget.indexed_constraints_tokens.initial
    assert all(item.ceiling <= 32768 for item in [budget.structure_tokens, budget.facts_tokens,
               budget.architecture_tokens, budget.indexed_core_tokens, budget.indexed_constraints_tokens, budget.review_tokens])


def test_per_request_token_limit_reaches_provider_without_mutating_llm(monkeypatch):
    import app.core.llm.llm as llm_module

    requests = []

    async def complete(**kwargs):
        requests.append(kwargs)
        return _response()

    monkeypatch.setattr(llm_module, "acompletion", complete)
    llm = llm_module.LLM(model="deepseek/deepseek-flash", api_key="test", base_url="https://example.invalid",
                         max_tokens=100, request_timeout=1)
    asyncio.run(llm.chat(history=[], max_tokens=37, max_attempts=1))
    assert requests[0]["max_tokens"] == 37
    assert requests[0]["num_retries"] == 0
    assert llm.max_tokens == 100


def test_shared_llm_callers_keep_default_transport_retries(monkeypatch):
    import app.core.llm.llm as llm_module
    from litellm.exceptions import APIError

    calls = []

    async def transient(**kwargs):
        calls.append(kwargs["max_tokens"])
        if len(calls) < 3:
            raise APIError(status_code=503, message="unavailable", llm_provider="deepseek", model="deepseek-flash")
        return _response()

    async def no_wait(seconds):
        pass

    monkeypatch.setattr(llm_module, "acompletion", transient)
    monkeypatch.setattr(llm_module.asyncio, "sleep", no_wait)
    llm = llm_module.LLM(model="deepseek/deepseek-flash", api_key="test", base_url="https://example.invalid",
                         max_tokens=100, request_timeout=1)
    result = asyncio.run(llm.chat(history=[]))
    assert result.choices[0].message.content == '{"value": 7}'
    assert calls == [100, 100, 100]
