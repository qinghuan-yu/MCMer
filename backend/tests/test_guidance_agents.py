import asyncio
import json
import time
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
    assert result.subproblems[0]["id"] == "problem1"
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

    assert [item["id"] for item in result.subproblems] == ["problem1", "problem2"]
    assert len(llm.calls) == 2
    retry_prompt = llm.calls[1]["history"]
    assert "完整重新生成" in retry_prompt[0]["content"]
    assert "JSONDecodeError" in retry_prompt[1]["content"]
    assert "subproblems" in retry_prompt[1]["content"]


def test_contract_request_prompts_for_strict_json_without_markdown_fences() -> None:
    from app.guidance.agents import LLMProblemDecomposer

    llm = StubLLM(
        [
            "```json\n{\"subproblems\": [{\"id\": \"problem1\"} {\"id\": \"problem2\"}]}\n```",
            {"subproblems": [{"id": "problem1"}, {"id": "problem2"}]},
        ]
    )
    decomposer = LLMProblemDecomposer(llm)

    asyncio.run(
        decomposer.decompose(
            question="Generate a valid contract.",
            data_files=[],
            task={},
        )
    )

    first_system_prompt = llm.calls[0]["history"][0]["content"]
    retry_system_prompt = llm.calls[1]["history"][0]["content"]
    assert "Do not wrap the JSON in Markdown code fences" in first_system_prompt
    assert "Escape every quote inside string values" in first_system_prompt
    assert "Do not wrap the JSON in Markdown code fences" in retry_system_prompt
    assert "Escape every quote inside string values" in retry_system_prompt


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
                    "constraints": [
                        "flows are nonnegative",
                        "identifiability constraint: fix the branch intercept from boundary data",
                    ],
                    "solve_steps": [
                        "Fit the constrained model.",
                        "Check Jacobian rank to confirm the branch slope is uniquely identifiable.",
                    ],
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


def test_modeler_sends_compact_problem_payload_to_avoid_real_prompt_timeout(tmp_path) -> None:
    from app.guidance.agents import LLMModeler
    from app.guidance.contracts import ProblemSpec

    llm = StubLLM(
        {
            "model_id": "traffic-flow",
            "selected_method": "constrained piecewise regression",
            "subproblem_models": [{
                "subproblem_id": "problem1",
                "objective": "Estimate two branch flow functions.",
                "selected_method": "constrained piecewise regression",
                "rationale": "The aggregate observation and monotonic shape constraints define a reproducible fit.",
                "equations": ["Q_3(t)=q_1(t)+q_2(t)"],
                "parameters": [{
                    "parameter_id": "p1_slope",
                    "symbol": "a_1",
                    "meaning": "slope of branch 1",
                    "unit": "standard vehicles per 2 minutes",
                    "source_type": "estimated",
                    "source_detail": "Attachment Table 1 aggregate flow",
                    "estimation_method": "bounded least squares",
                    "constraints": ["a_1 >= 0"],
                }],
                "constraints": [
                    "q_1(t) >= 0",
                    "identifiability constraint: fix one branch intercept using the stated boundary condition",
                ],
                "solve_steps": [
                    "Fit bounded least-squares parameters from Table 1.",
                    "Check Jacobian rank to confirm the branch parameters are uniquely identifiable.",
                ],
                "expected_results": ["branch flow function table"],
            }],
        }
    )
    modeler = LLMModeler(llm)
    long_question = "真实题面" + ("很长的道路交通背景。" * 1000)

    asyncio.run(
        modeler.model(
            problem_spec=ProblemSpec(
                task_summary=long_question,
                subproblems=[{
                    "id": "problem1",
                    "description": "Y-shaped road branch decomposition.",
                    "goals": ["estimate branch flows", "fill function table"],
                    "inputs": ["Attachment Table 1"],
                    "outputs": ["function expressions"],
                    "assumptions": ["branch 1 increasing", "branch 2 increases then decreases"],
                }],
                data_sources=[{"name": "Attachment.xlsx", "tables": ["Table 1"]}],
                locked_facts=["main flow equals branch-flow sum"],
                uncertainties=["turning point is unknown"],
            ),
            task={},
            work_dir=tmp_path,
        )
    )

    payload = json.loads(llm.calls[0]["history"][1]["content"])
    encoded = json.dumps(payload, ensure_ascii=False)
    assert len(encoded) < 3500
    assert "很长的道路交通背景。" not in encoded
    assert payload["subproblems"][0]["id"] == "problem1"
    assert payload["subproblems"][0]["goals"] == ["estimate branch flows", "fill function table"]
    assert payload["data_sources"][0]["name"] == "Attachment.xlsx"


def test_modeler_prompt_contains_readable_identifiability_requirements(tmp_path) -> None:
    from app.guidance.agents import LLMModeler
    from app.guidance.contracts import ProblemSpec

    llm = StubLLM(
        {
            "model_id": "traffic-flow",
            "selected_method": "约束最小二乘",
            "subproblem_models": [{
                "subproblem_id": "problem1",
                "objective": "推测支路车流量。",
                "selected_method": "约束最小二乘",
                "rationale": "聚合流量和趋势约束可形成可复现拟合。",
                "equations": ["Q(t)=q_1(t)+q_2(t)"],
                "parameters": [{
                    "parameter_id": "p1_slope",
                    "symbol": "a_1",
                    "meaning": "支路1斜率",
                    "unit": "标准车/2分钟",
                    "source_type": "estimated",
                    "source_detail": "附件表1",
                    "estimation_method": "约束最小二乘",
                    "constraints": ["a_1 >= 0"],
                }],
                "constraints": ["可辨识性计划：固定边界截距后参数唯一确定。"],
                "solve_steps": ["检查设计矩阵满秩，确认参数唯一确定。"],
                "expected_results": ["支路函数表"],
            }],
        }
    )
    modeler = LLMModeler(llm)

    asyncio.run(
        modeler.model(
            problem_spec=ProblemSpec(
                task_summary="推测支路流量",
                subproblems=[{"id": "problem1"}],
            ),
            task={},
            work_dir=tmp_path,
        )
    )

    prompt = llm.calls[0]["history"][0]["content"]
    assert "你是数学建模方案专家" in prompt
    assert "可辨识性计划" in prompt
    assert "设计矩阵满秩" in prompt
    assert "浣犳槸" not in prompt


def test_modeler_accepts_chinese_identifiability_plan_keywords(tmp_path) -> None:
    from app.guidance.agents import LLMModeler
    from app.guidance.contracts import ProblemSpec

    llm = StubLLM(
        {
            "model_id": "traffic-flow",
            "selected_method": "约束最小二乘",
            "subproblem_models": [{
                "subproblem_id": "problem1",
                "objective": "推测支路车流量。",
                "selected_method": "约束最小二乘",
                "rationale": "聚合流量和趋势约束可形成可复现拟合。",
                "equations": ["Q(t)=q_1(t)+q_2(t)"],
                "parameters": [{
                    "parameter_id": "p1_slope",
                    "symbol": "a_1",
                    "meaning": "支路1斜率",
                    "unit": "标准车/2分钟",
                    "source_type": "estimated",
                    "source_detail": "附件表1",
                    "estimation_method": "约束最小二乘",
                    "constraints": ["a_1 >= 0"],
                }],
                "constraints": ["固定支路1初值作为识别约束，使参数唯一确定。"],
                "solve_steps": ["构建设计矩阵并检查满秩后再求解。"],
                "expected_results": ["支路函数表"],
            }],
        }
    )
    modeler = LLMModeler(llm)

    result = asyncio.run(
        modeler.model(
            problem_spec=ProblemSpec(
                task_summary="推测支路流量",
                subproblems=[{"id": "problem1"}],
            ),
            task={},
            work_dir=tmp_path,
        )
    )

    assert result.subproblem_models[0].subproblem_id == "problem1"
    assert len(llm.calls) == 1


def test_modeler_regenerates_when_subproblem_lacks_identifiability_plan(tmp_path) -> None:
    from app.guidance.agents import LLMModeler
    from app.guidance.contracts import ProblemSpec

    weak_model = {
        "model_id": "traffic-flow",
        "selected_method": "constrained piecewise regression",
        "subproblem_models": [{
            "subproblem_id": "problem1",
            "objective": "Estimate branch flow functions.",
            "selected_method": "piecewise regression",
            "rationale": "Fits aggregate observations.",
            "equations": ["Q(t)=q_1(t)+q_2(t)"],
            "parameters": [{
                "parameter_id": "p1_slope",
                "symbol": "a_1",
                "meaning": "branch slope",
                "unit": "standard vehicles per minute",
                "source_type": "estimated",
                "source_detail": "Attachment table",
                "estimation_method": "least squares",
                "constraints": ["a_1 >= 0"],
            }],
            "constraints": ["flows are nonnegative"],
            "solve_steps": ["Fit parameters from observed aggregate flow."],
            "expected_results": ["branch flow functions"],
        }],
    }
    repaired_model = {
        **weak_model,
        "subproblem_models": [{
            **weak_model["subproblem_models"][0],
            "constraints": [
                "flows are nonnegative",
                "identifiability constraint: fix one branch intercept from the stated boundary condition",
            ],
            "solve_steps": [
                "Fit parameters from observed aggregate flow.",
                "Check the Jacobian rank to confirm the parameter vector is uniquely identifiable.",
            ],
        }],
    }
    llm = StubLLM([weak_model, repaired_model])
    modeler = LLMModeler(llm)

    result = asyncio.run(
        modeler.model(
            problem_spec=ProblemSpec(
                task_summary="Estimate branch flows.",
                subproblems=[{"id": "problem1"}],
            ),
            task={},
            work_dir=tmp_path,
        )
    )

    assert result.subproblem_models[0].constraints[-1].startswith("identifiability")
    assert len(llm.calls) == 2
    assert "identifiability" in llm.calls[1]["history"][1]["content"]


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


def test_contract_request_enforces_one_outer_timeout_budget(tmp_path) -> None:
    from app.guidance.contract_request import StructuredContractRequester
    from app.guidance.contracts import GeneratedProgram

    class HangingLLM:
        request_timeout = 0.01

        async def chat(self, **kwargs):
            await asyncio.sleep(60)

    requester = StructuredContractRequester(
        llm=HangingLLM(),
        model_type=GeneratedProgram,
        system_prompt="generate solver",
        agent_name="GuidanceProgramGenerator",
        artifact_dir=tmp_path,
    )

    started = time.perf_counter()
    with pytest.raises(RuntimeError, match="0.01s"):
        asyncio.run(requester.request({"approved_model": {"model_id": "traffic"}}))

    assert time.perf_counter() - started < 1.0
    diagnostic = json.loads(
        (tmp_path / "contract_diagnostics" / "GuidanceProgramGenerator.json").read_text(
            encoding="utf-8"
        )
    )
    assert diagnostic["status"] == "failed"
    assert diagnostic["attempts"][0]["error_type"] == "RuntimeError"


def test_program_generator_prompts_for_typed_registries_and_verification_metrics(tmp_path) -> None:
    from app.guidance.agents import LLMProgramGenerator
    from app.guidance.contracts import ApprovedModelSpec

    llm = StubLLM(
        {
            "language": "python",
            "entrypoint": "solve.py",
            "source_code": "from pathlib import Path\nPath('solver_results.json').write_text('{}')\n",
            "declared_outputs": [
                "solver_results.json",
                "parameter_registry.json",
                "result_registry.json",
                "figure_registry.json",
            ],
        }
    )
    generator = LLMProgramGenerator(llm)
    approved_model = ApprovedModelSpec(
        review_status="approved",
        model_id="typed-artifacts",
        required_outputs=[
            "solver_results.json",
            "parameter_registry.json",
            "result_registry.json",
            "figure_registry.json",
        ],
    )

    asyncio.run(
        generator.generate(
            approved_model=approved_model,
            task={},
            work_dir=tmp_path,
            data_profile={},
        )
    )

    system_prompt = llm.calls[0]["history"][0]["content"]
    payload = json.loads(llm.calls[0]["history"][1]["content"])
    rules = "\n".join(payload["program_generation_rules"])
    assert "summary 必须是 JSON object" in system_prompt
    assert "rmse" in system_prompt
    assert "max_abs_residual" in system_prompt
    assert "constraint_values 必须是非空数字数组" in system_prompt
    assert "root filenames must be written exactly at the work directory root" in rules
    assert "Do not prefix root required outputs with output/, outputs/, results/, or result/" in rules
    assert "Do not put planned-but-unwritten figures in figure_registry" in rules
    assert "If a verified result cannot supply typed solver evidence" in rules


def test_program_generator_regenerates_when_source_uses_empty_constraint_values(tmp_path) -> None:
    from app.guidance.agents import LLMProgramGenerator
    from app.guidance.contracts import ApprovedModelSpec

    llm = StubLLM(
        [
            {
                "language": "python",
                "entrypoint": "solve.py",
                "source_code": "constraint_values = []\nprint('bad evidence')\n",
                "declared_outputs": ["solver_results.json"],
            },
            {
                "language": "python",
                "entrypoint": "solve.py",
                "source_code": "constraint_values = [0.0]\nprint('valid evidence')\n",
                "declared_outputs": ["solver_results.json"],
            },
        ]
    )
    generator = LLMProgramGenerator(llm)
    approved_model = ApprovedModelSpec(
        review_status="approved",
        model_id="semantic-program-validation",
        required_outputs=["solver_results.json"],
    )

    result = asyncio.run(
        generator.generate(
            approved_model=approved_model,
            task={},
            work_dir=tmp_path,
            data_profile={},
        )
    )

    assert "valid evidence" in result.source_code
    assert len(llm.calls) == 2
    assert "UnsafeGeneratedProgramError" in llm.calls[1]["history"][1]["content"]
    assert "constraint_values" in llm.calls[1]["history"][1]["content"]


def test_program_generator_regenerates_when_source_writes_declared_outputs_under_output_dir(tmp_path) -> None:
    from app.guidance.agents import LLMProgramGenerator
    from app.guidance.contracts import ApprovedModelSpec

    llm = StubLLM(
        [
            {
                "language": "python",
                "entrypoint": "solve.py",
                "source_code": (
                    "from pathlib import Path\n"
                    "Path('output').mkdir(exist_ok=True)\n"
                    "Path('output/solver_results.json').write_text('{}')\n"
                ),
                "declared_outputs": ["solver_results.json"],
            },
            {
                "language": "python",
                "entrypoint": "solve.py",
                "source_code": "from pathlib import Path\nPath('solver_results.json').write_text('{}')\n",
                "declared_outputs": ["solver_results.json"],
            },
        ]
    )
    generator = LLMProgramGenerator(llm)
    approved_model = ApprovedModelSpec(
        review_status="approved",
        model_id="declared-output-location",
        required_outputs=["solver_results.json"],
    )

    result = asyncio.run(
        generator.generate(
            approved_model=approved_model,
            task={},
            work_dir=tmp_path,
            data_profile={},
        )
    )

    assert "output/solver_results.json" not in result.source_code
    assert len(llm.calls) == 2
    assert "declared output" in llm.calls[1]["history"][1]["content"]
