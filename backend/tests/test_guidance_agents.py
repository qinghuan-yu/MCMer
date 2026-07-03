import asyncio
import json
import shutil
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
    assert "每个子问题最多 8 个核心参数" in prompt
    assert "用参数组" in prompt
    assert "浣犳槸" not in prompt


def test_modeler_regenerates_when_subproblem_lists_too_many_parameters(tmp_path) -> None:
    from app.guidance.agents import LLMModeler
    from app.guidance.contracts import ProblemSpec

    base_parameter = {
        "symbol": "p",
        "meaning": "model parameter",
        "unit": "unit",
        "source_type": "estimated",
        "source_detail": "attachment data",
        "estimation_method": "least squares",
        "constraints": ["p >= 0"],
    }
    verbose_model = {
        "model_id": "traffic-flow",
        "selected_method": "constrained regression",
        "subproblem_models": [{
            "subproblem_id": "problem1",
            "objective": "Estimate branch flow functions.",
            "selected_method": "piecewise regression",
            "rationale": "Fits aggregate observations.",
            "equations": ["Q(t)=q_1(t)+q_2(t)"],
            "parameters": [
                {**base_parameter, "parameter_id": f"p{i}", "symbol": f"p{i}"}
                for i in range(9)
            ],
            "constraints": [
                "flows are nonnegative",
                "identifiability constraint: fix one boundary value and check Jacobian rank",
            ],
            "solve_steps": ["Fit parameters.", "Check Jacobian rank."],
            "expected_results": ["branch flow functions"],
        }],
    }
    compact_model = {
        **verbose_model,
        "subproblem_models": [{
            **verbose_model["subproblem_models"][0],
            "parameters": [{
                **base_parameter,
                "parameter_id": "theta_problem1",
                "symbol": "theta_1",
                "meaning": "grouped branch-flow parameter vector",
            }],
        }],
    }
    llm = StubLLM([verbose_model, compact_model])
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

    assert result.subproblem_models[0].parameters[0].parameter_id == "theta_problem1"
    assert len(llm.calls) == 2
    assert "at most 8 items" in llm.calls[1]["history"][1]["content"]


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
    assert "可辨识性计划" in llm.calls[1]["history"][0]["content"]
    assert "subproblem_models 必须逐一覆盖" in llm.calls[1]["history"][0]["content"]
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
    assert "constraint_values 不能使用 residuals" in system_prompt
    assert "solver_results.evidence 不允许 kind=blocked" in system_prompt
    assert "placeholder/not implemented/would need to compute" in system_prompt
    assert "root filenames must be written exactly at the work directory root" in rules
    assert "Do not prefix root required outputs with output/, outputs/, results/, or result/" in rules
    assert "Figures may only link to verified_results IDs" in rules
    assert "If there are no verified_results, figure_registry.figures must be empty" in rules
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


def test_program_generator_regenerates_when_constraint_values_reuse_residuals(tmp_path) -> None:
    from app.guidance.agents import LLMProgramGenerator
    from app.guidance.contracts import ApprovedModelSpec

    llm = StubLLM(
        [
            {
                "language": "python",
                "entrypoint": "solve.py",
                "source_code": (
                    "residuals = [1.0, -2.0]\n"
                    "solver_results = {'evidence': [{'kind': 'regression', "
                    "'subproblem_id': 'problem1', 'result_id': 'result1', "
                    "'observed': [3.0, 4.0], 'predicted': [2.0, 6.0], "
                    "'residuals': residuals, 'constraint_values': residuals, "
                    "'parameter_stability': {'method': 'loo', 'max_relative_change': 0.0, "
                    "'threshold': 0.1}}]}\n"
                ),
                "declared_outputs": ["solver_results.json"],
            },
            {
                "language": "python",
                "entrypoint": "solve.py",
                "source_code": (
                    "constraint_margins = [3.0, 4.0]\n"
                    "solver_results = {'evidence': [{'kind': 'regression', "
                    "'subproblem_id': 'problem1', 'result_id': 'result1', "
                    "'observed': [3.0, 4.0], 'predicted': [3.0, 4.0], "
                    "'residuals': [0.0, 0.0], 'constraint_values': constraint_margins, "
                    "'parameter_stability': {'method': 'loo', 'max_relative_change': 0.0, "
                    "'threshold': 0.1}}]}\n"
                ),
                "declared_outputs": ["solver_results.json"],
            },
        ]
    )
    generator = LLMProgramGenerator(llm)
    approved_model = ApprovedModelSpec(
        review_status="approved",
        model_id="residual-constraint-values",
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

    assert "'constraint_values': residuals" not in result.source_code
    assert len(llm.calls) == 2
    assert "constraint_values" in llm.calls[1]["history"][1]["content"]
    assert "residual" in llm.calls[1]["history"][1]["content"]


def test_program_generator_regenerates_when_regression_evidence_has_empty_series(tmp_path) -> None:
    from app.guidance.agents import LLMProgramGenerator
    from app.guidance.contracts import ApprovedModelSpec

    llm = StubLLM(
        [
            {
                "language": "python",
                "entrypoint": "solve.py",
                "source_code": (
                    "solver_results = {'evidence': [{'kind': 'regression', "
                    "'subproblem_id': 'problem2', 'result_id': 'problem2', "
                    "'observed': [1.0], 'predicted': [], 'residuals': [], "
                    "'constraint_values': [1.0], "
                    "'parameter_stability': {'method': 'loo', 'max_relative_change': 0.0, "
                    "'threshold': 0.1}}]}\n"
                ),
                "declared_outputs": ["solver_results.json"],
            },
            {
                "language": "python",
                "entrypoint": "solve.py",
                "source_code": (
                    "solver_results = {'evidence': [{'kind': 'regression', "
                    "'subproblem_id': 'problem2', 'result_id': 'problem2', "
                    "'observed': [1.0], 'predicted': [1.0], 'residuals': [0.0], "
                    "'constraint_values': [1.0], "
                    "'parameter_stability': {'method': 'loo', 'max_relative_change': 0.0, "
                    "'threshold': 0.1}}]}\n"
                ),
                "declared_outputs": ["solver_results.json"],
            },
        ]
    )
    generator = LLMProgramGenerator(llm)
    approved_model = ApprovedModelSpec(
        review_status="approved",
        model_id="regression-empty-series",
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

    assert "'predicted': []" not in result.source_code
    assert len(llm.calls) == 2
    assert "predicted" in llm.calls[1]["history"][1]["content"]


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


def test_program_generator_regenerates_when_source_writes_blocked_solver_evidence(tmp_path) -> None:
    from app.guidance.agents import LLMProgramGenerator
    from app.guidance.contracts import ApprovedModelSpec

    llm = StubLLM(
        [
            {
                "language": "python",
                "entrypoint": "solve.py",
                "source_code": (
                    "import json\n"
                    "solver_results = {'evidence': [{'kind': 'blocked', 'result_id': 'problem1'}]}\n"
                    "open('solver_results.json', 'w').write(json.dumps(solver_results))\n"
                ),
                "declared_outputs": ["solver_results.json"],
            },
            {
                "language": "python",
                "entrypoint": "solve.py",
                "source_code": (
                    "import json\n"
                    "solver_results = {'evidence': [{'kind': 'regression', 'result_id': 'result1', "
                    "'subproblem_id': 'problem1', 'observed': [1.0], 'predicted': [1.0], "
                    "'residuals': [0.0], 'constraint_values': [0.0], "
                    "'parameter_stability': {'method': 'loo', 'max_relative_change': 0.0, "
                    "'threshold': 0.1}}]}\n"
                    "open('solver_results.json', 'w').write(json.dumps(solver_results))\n"
                ),
                "declared_outputs": ["solver_results.json"],
            },
        ]
    )
    generator = LLMProgramGenerator(llm)
    approved_model = ApprovedModelSpec(
        review_status="approved",
        model_id="blocked-evidence",
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

    assert "'kind': 'blocked'" not in result.source_code
    assert len(llm.calls) == 2
    assert "kind=blocked" in llm.calls[1]["history"][1]["content"]


def test_program_generator_uses_deterministic_wuyi_traffic_template_for_real_workbook(tmp_path) -> None:
    from app.guidance.agents import LLMProgramGenerator
    from app.guidance.contracts import ApprovedModelSpec
    from app.guidance.execution import LocalProgramExecutor
    from app.guidance.stages import GuidanceStage, ResultVerificationStage

    source = (
        Path(__file__).parents[1]
        / "project"
        / "work_dir"
        / "phase1-wuyi-real-20260619"
        / "data"
        / "attachment.xlsx"
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    shutil.copyfile(source, data_dir / "attachment.xlsx")
    approved_model = ApprovedModelSpec(
        review_status="approved",
        model_id="traffic_flow_multi_problem_template",
        required_outputs=[
            "solver_results.json",
            "parameter_registry.json",
            "result_registry.json",
            "figure_registry.json",
        ],
        approved_model={
            "selected_method": "traffic flow piecewise regression",
            "assumptions": ["为增强可辨识性，部分参数被固定为合理值。"],
            "subproblem_models": [
                {
                    "subproblem_id": f"problem{i}",
                    "objective": f"Estimate traffic-flow structure for problem {i}.",
                    "selected_method": "piecewise least squares",
                    "rationale": (
                        "通过固定一个截距为0来增强参数可辨识性。"
                        if i == 1
                        else "The workbook provides observed aggregate flow series for reproducible fitting."
                    ),
                    "equations": (
                        ["F(t) = f1(t-2) + f2(t-2) + f3(t) + f4(t) (延迟补偿)"]
                        if i == 2
                        else ["F(t) = f1(t-2) + f2(t-2) + f3(t) (延迟补偿)"]
                        if i == 3
                        else ["F_obs(t) = F_true(t) + ε(t), where F_true(t) = f1(t-2) + f2(t-2) + f3(t)"]
                        if i == 4
                        else [f"Q_{i}(t)=X_{i}(t) theta_{i}"]
                    ),
                    "parameters": [{
                        "parameter_id": f"theta_problem{i}",
                        "symbol": f"theta_{i}",
                        "meaning": "grouped regression coefficient vector",
                        "unit": "vehicles per 2 minutes",
                        "source_type": "estimated",
                        "source_detail": f"Attachment sheet {i}",
                        "estimation_method": "least squares",
                        "constraints": ["identified by full-rank design matrix"],
                    }],
                    "constraints": (
                        ["固定b1=0或b2=0以确保参数唯一识别（若数据不足）"]
                        if i == 1
                        else ["nonnegative modeled flow", "full-rank design matrix check"]
                    ),
                    "solve_steps": ["Read the workbook sheet.", "Fit the design matrix and verify residual metrics."],
                    "expected_results": ["parameter registry and verified result"],
                }
                for i in range(1, 6)
            ],
        },
    )
    (tmp_path / "approved_model_spec.json").write_text(
        json.dumps(approved_model.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (tmp_path / "model_review.json").write_text(
        json.dumps(
            {
                "model_id": approved_model.model_id,
                "status": "approved",
                "dimensional_consistency": "consistent; units are vehicles per 2 minutes",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    data_profile = {
        "files": [{
            "path": "data/attachment.xlsx",
            "suffix": ".xlsx",
            "workbook": {
                "sheets": [
                    {"name": "表1 (Table 1)", "columns": ["时间 t (Time t)", "主路3的车流量 (Traffic flow on the Main road 3)"]},
                    {"name": "表2 (Table 2)", "columns": ["时间 t (Time t)", "主路5的车流量 (Traffic flow on the Main road 5)"]},
                    {"name": "表3 (Table 3)", "columns": ["时间 t (Time t)", "主路4的车流量 (Traffic flow on the Main road 4)"]},
                    {"name": "表4 (Table 4)", "columns": ["时间 t (Time t)", "主路4的车流量 (Traffic flow on the Main road 4)"]},
                ],
            },
        }],
    }
    llm = StubLLM({
        "source_code": "raise AssertionError('LLM should not be called')",
        "declared_outputs": [],
    })
    generator = LLMProgramGenerator(llm)

    program = asyncio.run(
        generator.generate(
            approved_model=approved_model,
            task={"work_dir": str(tmp_path)},
            work_dir=tmp_path,
            data_profile=data_profile,
        )
    )
    manifest = asyncio.run(
        LocalProgramExecutor().execute(
            task_id="wuyi-template",
            work_dir=tmp_path,
            approved_model=approved_model,
            program=program,
        )
    )
    report_path = asyncio.run(
        ResultVerificationStage().run(
            task_id="wuyi-template",
            task={"work_dir": str(tmp_path)},
            input_path=tmp_path / "execution_manifest.json",
            output_path=tmp_path / "verification_report.json",
        )
    )
    asyncio.run(
        GuidanceStage().run(
            task_id="wuyi-template",
            task={"work_dir": str(tmp_path)},
            input_path=report_path,
            output_path=tmp_path / "guidance.md",
        )
    )

    results = json.loads((tmp_path / "result_registry.json").read_text(encoding="utf-8"))
    verification = json.loads((tmp_path / "verification_report.json").read_text(encoding="utf-8"))
    audit = json.loads((tmp_path / "guidance_audit_report.json").read_text(encoding="utf-8"))
    guidance = (tmp_path / "guidance.md").read_text(encoding="utf-8")

    assert llm.calls == []
    assert manifest.status == "passed"
    assert len(results["verified_results"]) == 5
    assert {item["id"] for item in results["verified_results"]} == {
        "result_problem1_traffic_fit",
        "result_problem2_traffic_fit",
        "result_problem3_signal_fit",
        "result_problem4_noisy_signal_fit",
        "result_problem5_minimum_monitoring",
    }
    assert verification["status"] == "verified"
    assert audit["status"] == "PASS"
    assert "result_problem5_minimum_monitoring" in guidance
    assert "param_problem4_green_start" in guidance
    assert "不可唯一识别" in guidance
    assert "规范化估计" in guidance
    assert "样本序号 k" in guidance
    assert "2 分钟延迟 = 1 个采样步" in guidance
    assert "0, 1, 3, 7, 10, 12, 16, 21, 24, 25, 30, 34, 37, 39, 43, 45, 48, 52, 57, 59" in guidance
    assert "未证明全局最少" in guidance
    assert "增强可辨识性" not in guidance
    assert "增强参数可辨识性" not in guidance
    assert "不应写成唯一真实支路流量" in guidance
    assert "规范化约束" in guidance
    assert "## 结论状态登记" in guidance
    assert "chosen_under_prior" in guidance
    assert "not_uniquely_identifiable" in guidance
    assert "result_problem1_traffic_fit" in guidance
    assert "## 可辨识性分析" in guidance
    assert "观测量：主路总流量 F(k)" in guidance
    assert "未知量：支路流量 f_1(k), f_2(k)" in guidance
    assert "F(k)=f_1(k)+f_2(k)" in guidance
    assert "rank(A)<dim(theta)" in guidance
    assert "lambda" in guidance
    assert "a_1=lambda" in guidance
    assert "a_2=1.5-lambda" in guidance
    assert "rank(A_S)=dim(theta)" in guidance
    assert "固定b1=0或b2=0以确保参数唯一识别" not in guidance
    assert "只能作为规范化约束，不能作为数据唯一识别证据" in guidance
    assert "F(t) = f1(t-2) + f2(t-2) + f3(t) + f4(t)" not in guidance
    assert "F(t) = f1(t-2) + f2(t-2) + f3(t)" not in guidance
    assert "F_true(t) = f1(t-2) + f2(t-2) + f3(t)" not in guidance
    assert "F(k)=f_1(k-1)+f_2(k-1)+f_3(k)+f_4(k)" in guidance
    assert "F(k)=f_1(k-1)+f_2(k-1)+f_3(k)" in guidance
    assert "F_obs(k)=F_true(k)+epsilon(k)" in guidance
    assert "## 最终答案与应填表格" in guidance
    assert "问题1规范化代表解" in guidance
    assert "f_1(k)=7+0.5k" in guidance
    assert "f_2(k)=k, 0<=k<=30; f_2(k)=60-k, 30<k<=59" in guidance
    assert "chosen_under_prior" in guidance
    assert "问题5候选观测时刻" in guidance
    assert "0, 1, 3, 7, 10, 12, 16, 21, 24, 25, 30, 34, 37, 39, 43, 45, 48, 52, 57, 59" in guidance
    assert "result_problem5_minimum_monitoring" in guidance
    assert "问题2-4拟合模型填表" in guidance
    assert "问题2" in guidance
    assert "延迟基函数回归：F_2(k)=X_2(k) theta_2" in guidance
    assert "theta_2_0..theta_2_6" in guidance
    assert "result_problem2_traffic_fit" in guidance
    assert "问题3" in guidance
    assert "信号相位基函数回归：F_3(k)=X_3(k; g_3) theta_3" in guidance
    assert "result_problem3_signal_fit" in guidance
    assert "问题4" in guidance
    assert "含噪信号相位基函数回归：F_4(k)=X_4(k; g_4) theta_4" in guidance
    assert "result_problem4_noisy_signal_fit" in guidance
    assert "evidence_verified" in guidance
    assert "模型形式不确定，不能解释为唯一支路恢复" in guidance
    assert "问题2-4指定时刻拟合值" in guidance
    assert "7:30 (k=16)" in guidance
    assert "8:30 (k=46)" in guidance
    assert "param_problem2_k16_fitted_main_flow" in guidance
    assert "param_problem4_k46_fitted_main_flow" in guidance
    assert "不能作为唯一支路流量" in guidance
    assert "## 建模路线与读者决策" in guidance
    assert "观测量/未知量/时间口径" in guidance
    assert "只观测主路聚合流量" in guidance
    assert "缺少支路传感器、边界流量或额外先验" in guidance
    assert "先做可辨识性分析" in guidance
    assert "规范化代表解而非唯一真值" in guidance
    assert "给定基函数回归与残差披露" in guidance
    assert "结论来源分层" in guidance
    assert "问题5秩诊断与最小性边界" in guidance
    assert "设计矩阵 A_S" in guidance
    assert "dim(theta)" in guidance
    assert "rank(A_S)" in guidance
    assert "param_problem5_design_rank" in guidance
    assert "param_problem5_parameter_dimension" in guidance
    assert "若 A_S 列相关，仅靠主路设备无法唯一恢复" in guidance
    assert "问题2-4基函数与参数字典" in guidance
    assert "theta_2_0 * 1 + theta_2_1 * max(k-1,0)" in guidance
    assert "theta_3_5 * I_green(k; g_3)" in guidance
    assert "theta_4_5 * I_green(k; g_4)" in guidance
    assert "I_green(k; g)=1 当 (k-g) mod 9 落在 [0,5)" in guidance
