import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


class StubLLM:
    def __init__(self, payload: dict | list[dict | str]) -> None:
        self.responses = payload if isinstance(payload, list) else [payload]
        self.calls: list[dict] = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses[len(self.calls) - 1]
        content = response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


def _blocked_ir(model_id: str = "operator-gap") -> dict:
    return {
        "schema_version": "1.0",
        "model_id": model_id,
        "subproblems": [{
            "execution_status": "blocked",
            "subproblem_id": "problem1",
            "objective": "Estimate the requested model.",
            "blocking_reason": "No registered operator covers the requested method.",
            "missing_inputs": ["registered operator"],
            "recovery_action": "Register and validate the missing operator.",
            "expected_outputs": ["compiled model result"],
        }],
    }


def test_problem_decomposer_owns_task_summary_when_llm_omits_it() -> None:
    from app.guidance.agents import LLMProblemDecomposer

    llm = StubLLM({
        "subproblems": [{
            "id": "problem_1",
            "objective": "求解矩阵关系",
            "candidate_directions": ["线性代数方法"],
        }],
        "locked_facts": ["矩阵由题设给定"],
    })

    result = asyncio.run(LLMProblemDecomposer(llm).decompose(
        question="分析题目中的矩阵并完成各子问题。",
        data_files=[],
        task={},
    ))

    assert result.task_summary == "分析题目中的矩阵并完成各子问题。"
    assert result.subproblems[0]["id"] == "problem1"
    assert "目标 JSON Schema" in llm.calls[0]["history"][0]["content"]


def test_problem_decomposer_regenerates_complete_json_once_after_syntax_error() -> None:
    from app.guidance.agents import LLMProblemDecomposer

    llm = StubLLM([
        '{"subproblems": [{"id": "problem_1"} {"id": "problem_2"}]}',
        {"subproblems": [{"id": "problem_1"}, {"id": "problem_2"}]},
    ])

    result = asyncio.run(LLMProblemDecomposer(llm).decompose(
        question="完成两个子问题。",
        data_files=[],
        task={},
    ))

    assert [item["id"] for item in result.subproblems] == ["problem1", "problem2"]
    assert len(llm.calls) == 2
    assert "完整重新生成" in llm.calls[1]["history"][0]["content"]
    assert "JSONDecodeError" in llm.calls[1]["history"][1]["content"]


def test_contract_request_prompts_for_strict_json_without_markdown_fences() -> None:
    from app.guidance.agents import LLMProblemDecomposer

    llm = StubLLM([
        "```json\n{\"subproblems\": [{\"id\": \"problem1\"} {\"id\": \"problem2\"}]}\n```",
        {"subproblems": [{"id": "problem1"}, {"id": "problem2"}]},
    ])

    asyncio.run(LLMProblemDecomposer(llm).decompose(
        question="Generate a valid contract.",
        data_files=[],
        task={},
    ))

    for call in llm.calls:
        prompt = call["history"][0]["content"]
        assert "Do not wrap the JSON in Markdown code fences" in prompt
        assert "Escape every quote inside string values" in prompt


def test_modeler_requests_typed_model_ir_and_retries_complete_contract(tmp_path: Path) -> None:
    from app.guidance.agents import LLMModeler
    from app.guidance.contracts import ProblemSpec
    from app.guidance.model_ir import ModelIR

    llm = StubLLM([{"model_id": "incomplete"}, _blocked_ir()])
    result = asyncio.run(LLMModeler(llm).model(
        problem_spec=ProblemSpec(
            task_summary="Estimate the model.",
            subproblems=[{"id": "problem1"}],
        ),
        task={},
        work_dir=tmp_path,
    ))

    assert isinstance(result, ModelIR)
    assert result.subproblems[0].execution_status == "blocked"
    assert len(llm.calls) == 2
    assert "ModelIR" in llm.calls[0]["history"][0]["content"]
    assert "free-text equations" in llm.calls[0]["history"][0]["content"]
    diagnostic = json.loads(
        (tmp_path / "contract_diagnostics" / "GuidanceModeler.json").read_text(encoding="utf-8")
    )
    assert diagnostic["status"] == "recovered"


def test_modeler_uses_compact_problem_payload_without_losing_data_profile(tmp_path: Path) -> None:
    from app.guidance.agents import LLMModeler
    from app.guidance.contracts import ProblemSpec

    llm = StubLLM(_blocked_ir())
    asyncio.run(LLMModeler(llm).model(
        problem_spec=ProblemSpec(
            task_summary="A" * 2000,
            subproblems=[{"id": "problem1", "objective": "Estimate output."}],
        ),
        data_profile={"files": [{"path": "data/input.xlsx", "sheets": []}]},
        task={},
        work_dir=tmp_path,
    ))

    payload = json.loads(llm.calls[0]["history"][1]["content"])
    assert payload["task_summary"].startswith("Full task statement omitted")
    assert payload["subproblems"][0]["id"] == "problem1"
    assert payload["data_profile"]["files"][0]["path"] == "data/input.xlsx"


def test_model_reviewer_receives_problem_and_model_ir_contracts(tmp_path: Path) -> None:
    from app.guidance.agents import LLMModelReviewer
    from app.guidance.contracts import ProblemSpec
    from app.guidance.model_ir import ModelIR

    llm = StubLLM({
        "model_id": "operator-gap",
        "status": "approved",
        "identifiability": "blocked item is explicit",
        "dimensional_consistency": "not applicable",
        "data_sufficiency": "missing operator disclosed",
        "computational_feasibility": "blocked honestly",
    })
    review = asyncio.run(LLMModelReviewer(llm).review(
        problem_spec=ProblemSpec(
            task_summary="Estimate the model.",
            subproblems=[{"id": "problem1"}],
        ),
        model_ir=ModelIR.model_validate(_blocked_ir()),
        task={},
        work_dir=tmp_path,
    ))

    payload = json.loads(llm.calls[0]["history"][1]["content"])
    assert review.status == "approved"
    assert payload["problem_spec"]["task_summary"] == "Estimate the model."
    assert payload["model_ir"]["model_id"] == "operator-gap"
    assert "model_spec" not in payload


def test_contract_failure_stops_after_two_attempts_and_saves_diagnostics(tmp_path: Path) -> None:
    from app.guidance.agents import LLMProblemDecomposer
    from app.guidance.contract_request import ContractGenerationError

    llm = StubLLM(["{bad json", "{still bad json"])
    with pytest.raises(ContractGenerationError, match="after 2 complete attempts"):
        asyncio.run(LLMProblemDecomposer(llm).decompose(
            question="完成题目。",
            data_files=[],
            task={"work_dir": str(tmp_path)},
        ))

    diagnostic = json.loads(
        (tmp_path / "contract_diagnostics" / "GuidanceDecomposer.json").read_text(encoding="utf-8")
    )
    assert diagnostic["status"] == "failed"
    assert len(diagnostic["attempts"]) == 2
    assert diagnostic["attempts"][1]["error_type"] == "JSONDecodeError"


def test_contract_request_saves_diagnostic_when_llm_fails(tmp_path: Path) -> None:
    from app.guidance.contract_request import StructuredContractRequester
    from app.guidance.contracts import ProblemSpec

    class FailingLLM:
        async def chat(self, **kwargs):
            raise RuntimeError("GuidanceModeler: upstream request failed")

    requester = StructuredContractRequester(
        llm=FailingLLM(),
        model_type=ProblemSpec,
        system_prompt="decompose problem",
        agent_name="GuidanceModeler",
        artifact_dir=tmp_path,
    )
    with pytest.raises(RuntimeError, match="upstream request failed"):
        asyncio.run(requester.request({"question": "test"}))

    diagnostic = json.loads(
        (tmp_path / "contract_diagnostics" / "GuidanceModeler.json").read_text(encoding="utf-8")
    )
    assert diagnostic["status"] == "failed"
    assert diagnostic["attempts"][0]["error_type"] == "RuntimeError"


def test_contract_request_enforces_one_outer_timeout_budget(tmp_path: Path) -> None:
    from app.guidance.contract_request import StructuredContractRequester
    from app.guidance.contracts import ProblemSpec

    class HangingLLM:
        request_timeout = 0.01

        async def chat(self, **kwargs):
            await asyncio.sleep(60)

    requester = StructuredContractRequester(
        llm=HangingLLM(),
        model_type=ProblemSpec,
        system_prompt="decompose problem",
        agent_name="GuidanceModeler",
        artifact_dir=tmp_path,
    )
    started = time.perf_counter()
    with pytest.raises(RuntimeError, match="0.01s"):
        asyncio.run(requester.request({"question": "test"}))

    assert time.perf_counter() - started < 1.0
    diagnostic = json.loads(
        (tmp_path / "contract_diagnostics" / "GuidanceModeler.json").read_text(encoding="utf-8")
    )
    assert diagnostic["attempts"][0]["error_type"] == "RuntimeError"
