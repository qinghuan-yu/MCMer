import asyncio
import json
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
        message = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_problem_decomposer_owns_task_summary_when_llm_omits_it() -> None:
    from app.guidance.agents import LLMProblemDecomposer

    llm = StubLLM(
            {
                "subproblems": [
                    {
                        "id": "problem_1",
                        "objective": "求解矩阵关系",
                        "candidate_directions": ["线性代数方法"],
                    }
                ],
                "locked_facts": ["矩阵由题设给定"],
            }
    )
    decomposer = LLMProblemDecomposer(llm)

    result = asyncio.run(
        decomposer.decompose(
            question="分析题目中的矩阵并完成各子问题。",
            data_files=[],
            task={},
        )
    )

    assert result.task_summary == "分析题目中的矩阵并完成各子问题。"
    assert result.subproblems[0]["id"] == "problem_1"
    initial_system_prompt = llm.calls[0]["history"][0]["content"]
    assert "目标 JSON Schema" in initial_system_prompt
    assert '"properties"' in initial_system_prompt


def test_problem_decomposer_regenerates_complete_json_once_after_syntax_error() -> None:
    from app.guidance.agents import LLMProblemDecomposer

    llm = StubLLM(
        [
            '{"subproblems": [{"id": "problem_1"} {"id": "problem_2"}]}',
            {"subproblems": [{"id": "problem_1"}, {"id": "problem_2"}]},
        ]
    )
    decomposer = LLMProblemDecomposer(llm)

    result = asyncio.run(
        decomposer.decompose(
            question="完成两个子问题。",
            data_files=[],
            task={},
        )
    )

    assert [item["id"] for item in result.subproblems] == ["problem_1", "problem_2"]
    assert len(llm.calls) == 2
    retry_prompt = llm.calls[1]["history"]
    assert "完整重新生成" in retry_prompt[0]["content"]
    assert "JSONDecodeError" in retry_prompt[1]["content"]
    assert "subproblems" in retry_prompt[1]["content"]


def test_modeler_regenerates_after_schema_validation_error(tmp_path) -> None:
    from app.guidance.agents import LLMModeler
    from app.guidance.contracts import ProblemSpec

    llm = StubLLM(
        [
            {"model_id": "traffic-flow"},
            {
                "model_id": "traffic-flow",
                "selected_method": "constrained least squares",
                "subproblem_models": [{
                    "subproblem_id": "problem_1",
                    "objective": "Estimate branch flows.",
                    "selected_method": "constrained least squares",
                    "rationale": "The constraints identify the decomposition.",
                    "equations": ["q_main = q_1 + q_2"],
                    "parameters": [{
                        "parameter_id": "slope",
                        "symbol": "a",
                        "meaning": "branch slope",
                        "unit": "flow/minute",
                        "source_type": "estimated",
                        "source_detail": "attachment table",
                        "estimation_method": "least squares",
                        "constraints": ["a >= 0"],
                    }],
                    "constraints": ["flows are nonnegative"],
                    "solve_steps": ["Fit the constrained model."],
                    "expected_results": ["branch functions"],
                }],
            },
        ]
    )
    modeler = LLMModeler(llm)

    result = asyncio.run(
        modeler.model(
            problem_spec=ProblemSpec(task_summary="推测支路流量"),
            task={},
            work_dir=tmp_path,
        )
    )

    assert result.selected_method == "constrained least squares"
    assert len(llm.calls) == 2
    assert "ValidationError" in llm.calls[1]["history"][1]["content"]


def test_model_reviewer_receives_problem_and_model_contracts(tmp_path) -> None:
    from app.guidance.agents import LLMModelReviewer
    from app.guidance.contracts import ModelSpec, ProblemSpec

    llm = StubLLM({"model_id": "traffic-flow", "status": "approved"})
    reviewer = LLMModelReviewer(llm)

    asyncio.run(
        reviewer.review(
            problem_spec=ProblemSpec(task_summary="推测全部支路流量"),
            model_spec=ModelSpec(
                model_id="traffic-flow",
                selected_method="least squares",
                subproblem_models=[{
                    "subproblem_id": "problem_1",
                    "objective": "Estimate branch flows.",
                    "selected_method": "least squares",
                    "rationale": "Uses observed aggregate flow.",
                    "equations": ["q_main = q_1 + q_2"],
                    "parameters": [{
                        "parameter_id": "slope",
                        "symbol": "a",
                        "meaning": "branch slope",
                        "unit": "flow/minute",
                        "source_type": "estimated",
                        "source_detail": "attachment table",
                        "estimation_method": "least squares",
                        "constraints": ["a >= 0"],
                    }],
                    "constraints": ["flows are nonnegative"],
                    "solve_steps": ["Fit the model."],
                    "expected_results": ["branch functions"],
                }],
            ),
            task={},
            work_dir=tmp_path,
        )
    )

    payload = json.loads(llm.calls[0]["history"][1]["content"])
    assert payload["problem_spec"]["task_summary"] == "推测全部支路流量"
    assert payload["model_spec"]["model_id"] == "traffic-flow"


def test_contract_failure_stops_after_two_attempts_and_saves_diagnostics(tmp_path) -> None:
    from app.guidance.agents import LLMProblemDecomposer
    from app.guidance.contract_request import ContractGenerationError

    llm = StubLLM(["{bad json", "{still bad json"])
    decomposer = LLMProblemDecomposer(llm)

    with pytest.raises(ContractGenerationError, match="after 2 complete attempts"):
        asyncio.run(
            decomposer.decompose(
                question="完成题目。",
                data_files=[],
                task={"work_dir": str(tmp_path)},
            )
        )

    diagnostic_path = tmp_path / "contract_diagnostics" / "GuidanceDecomposer.json"
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    assert len(llm.calls) == 2
    assert diagnostic["status"] == "failed"
    assert len(diagnostic["attempts"]) == 2
    assert diagnostic["attempts"][0]["raw_response"] == "{bad json"
    assert diagnostic["attempts"][1]["error_type"] == "JSONDecodeError"


def test_contract_request_saves_diagnostic_when_llm_times_out(tmp_path) -> None:
    from app.guidance.contract_request import StructuredContractRequester
    from app.guidance.contracts import GeneratedProgram

    class TimeoutLLM:
        async def chat(self, **kwargs):
            raise RuntimeError("GuidanceProgramGenerator: 请求超时，超过 240s")

    requester = StructuredContractRequester(
        llm=TimeoutLLM(),
        model_type=GeneratedProgram,
        system_prompt="generate solver",
        agent_name="GuidanceProgramGenerator",
        artifact_dir=tmp_path,
    )

    with pytest.raises(RuntimeError, match="请求超时"):
        asyncio.run(requester.request({"approved_model": {"model_id": "traffic"}}))

    diagnostic = json.loads(
        (tmp_path / "contract_diagnostics" / "GuidanceProgramGenerator.json").read_text(
            encoding="utf-8"
        )
    )
    assert diagnostic["status"] == "failed"
    assert diagnostic["attempts"][0]["error_type"] == "RuntimeError"
    assert "240s" in diagnostic["attempts"][0]["error"]
