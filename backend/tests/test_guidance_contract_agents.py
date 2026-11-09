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


def _input_catalog(question: str) -> dict:
    return {
        "schema_version": "1.0",
        "source_file": "inputs/problem.txt",
        "source_sha256": "a" * 64,
        "blocks": [{
            "kind": "paragraph",
            "block_id": "paragraph-1",
            "source_ref": "inputs/problem.txt#paragraph-1",
            "sha256": "b" * 64,
            "text": question,
            "rows": [],
        }],
    }


def _subproblem(
    subproblem_id: str = "problem1",
    *,
    required_fact_ids: list[str] | None = None,
    required_uncertainty_ids: list[str] | None = None,
    depends_on_subproblem_ids: list[str] | None = None,
    **fields,
) -> dict:
    return {
        "id": subproblem_id,
        "required_fact_ids": required_fact_ids or [],
        "required_uncertainty_ids": required_uncertainty_ids or [],
        "depends_on_subproblem_ids": depends_on_subproblem_ids or [],
        **fields,
    }


def _split_decomposition_payload(payload: dict) -> list[dict]:
    structure = {
        key: value
        for key, value in payload.items()
        if key not in {"locked_facts", "uncertainties"}
    }
    structure["subproblems"] = [
        {
            key: value
            for key, value in subproblem.items()
            if key not in {
                "required_fact_ids",
                "required_uncertainty_ids",
            }
        }
        for subproblem in payload.get("subproblems", [])
    ]
    return [
        structure,
        {
            "locked_facts": payload.get("locked_facts", []),
            "uncertainties": payload.get("uncertainties", []),
        },
    ]


def _blocked_ir(
    model_id: str = "operator-gap",
    subproblem_id: str = "problem1",
) -> dict:
    return {
        "schema_version": "1.0",
        "model_id": model_id,
        "subproblems": [{
            "execution_status": "blocked",
            "subproblem_id": subproblem_id,
            "objective": "Estimate the requested model.",
            "blocking_reason": "No registered operator covers the requested method.",
            "missing_inputs": ["registered operator"],
            "recovery_action": "Register and validate the missing operator.",
            "expected_outputs": ["compiled model result"],
        }],
    }


def _revision_requirement(requirement_id: str, description: str) -> dict:
    return {
        "requirement_id": requirement_id,
        "description": description,
        "acceptance_criteria": f"The revised ModelIR satisfies: {description}",
    }


def _indexed_evidence(core: dict) -> dict:
    # 从固定模型的值构造证据表，再让生成草稿只声明列绑定。
    evidence = []
    for number, parameter in enumerate(core["parameters"], start=1):
        if parameter.get("source_kind") == "runtime_artifact":
            continue
        reference = f"inputs/inventory.docx#table-{number}"
        values = [item["value"] for item in parameter.pop("values")]
        parameter["source_kind"] = "static"
        parameter["binding"] = {
            "kind": "table_column", "source_ref": reference, "column": "value",
        }
        evidence.append({
            "evidence_id": f"question.table.{number}", "kind": "embedded_table",
            "source_ref": reference, "source_file": "inputs/inventory.docx",
            "labels": [], "sha256": "a" * 64, "size_bytes": 64,
            "rows": [["value"], *[[value] for value in values]], "profile": {},
        })
    return {"evidence": evidence}


def _prepare_indexed_draft(payload: dict) -> tuple[dict, dict, dict]:
    subproblem = payload["subproblems"][0]
    component = next(
        item
        for item in subproblem.pop("execution_components")
        if item["kind"] == "indexed_linear"
    )
    subproblem.pop("equations")
    subproblem.pop("constraints")
    subproblem.pop("fact_bindings")
    subproblem["variables"] = []
    subproblem["operators"] = []
    return subproblem, component["model"], component


def _operator_graph_draft() -> dict:
    fixture_path = Path(__file__).parent / "fixtures" / "model_ir" / "wuyi_anchor_bolt.json"
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    subproblem = payload["subproblems"][0]
    payload["subproblems"] = [subproblem]
    component = subproblem.pop("execution_components")[0]
    for field in ("equations", "constraints", "fact_bindings"):
        subproblem.pop(field)
    subproblem["execution_components"] = ["operator_graph"]
    subproblem["variables"] = component["variables"]
    subproblem["operators"] = component["operators"]
    return payload


def test_problem_decomposer_owns_task_summary_when_llm_omits_it() -> None:
    from app.guidance.decomposer import LLMProblemDecomposer

    llm = StubLLM(_split_decomposition_payload({
        "subproblems": [_subproblem(
            "problem_1",
            objective="求解矩阵关系",
            candidate_directions=["线性代数方法"],
            required_fact_ids=["fact.matrix_given"],
        )],
        "locked_facts": [{
            "fact_id": "fact.matrix_given",
            "statement": "矩阵由题设给定",
            "source_refs": ["inputs/problem.txt#paragraph-1"],
            "subproblem_ids": ["problem_1"],
            "semantic_key": "matrix.source",
            "value": "given",
            "unit": "",
        }],
    }))

    result = asyncio.run(LLMProblemDecomposer(llm).decompose(
        question="分析题目中的矩阵并完成各子问题。",
        evidence_profile={"evidence": []},
        input_catalog=_input_catalog("分析题目中的矩阵并完成各子问题。"),
        task={},
    ))

    assert result.task_summary == "分析题目中的矩阵并完成各子问题。"
    assert result.subproblems[0].id == "problem1"
    assert "目标 JSON Schema" in llm.calls[0]["history"][0]["content"]


def test_problem_decomposer_regenerates_complete_json_once_after_syntax_error() -> None:
    from app.guidance.decomposer import LLMProblemDecomposer

    llm = StubLLM([
        '{"subproblems": [{"id": "problem_1"} {"id": "problem_2"}]}',
        {
            "subproblems": [
                {
                    "id": "problem_1",
                    "depends_on_subproblem_ids": [],
                },
                {
                    "id": "problem_2",
                    "depends_on_subproblem_ids": [],
                },
            ],
        },
        {"locked_facts": [], "uncertainties": []},
    ])

    result = asyncio.run(LLMProblemDecomposer(llm).decompose(
        question="完成两个子问题。",
        evidence_profile={"evidence": []},
        input_catalog=_input_catalog("完成两个子问题。"),
        task={},
    ))

    assert [item.id for item in result.subproblems] == ["problem1", "problem2"]
    assert len(llm.calls) == 3
    assert "完整重新生成" in llm.calls[1]["history"][0]["content"]
    assert "JSONDecodeError" in llm.calls[1]["history"][1]["content"]
    retry_payload = json.loads(llm.calls[1]["history"][1]["content"])
    assert "previous_invalid_response" not in retry_payload


def test_contract_request_prompts_for_strict_json_without_markdown_fences() -> None:
    from app.guidance.decomposer import LLMProblemDecomposer

    llm = StubLLM([
        "```json\n{\"subproblems\": [{\"id\": \"problem1\"} {\"id\": \"problem2\"}]}\n```",
        {
            "subproblems": [
                {
                    "id": "problem1",
                    "depends_on_subproblem_ids": [],
                },
                {
                    "id": "problem2",
                    "depends_on_subproblem_ids": [],
                },
            ],
        },
        {"locked_facts": [], "uncertainties": []},
    ])

    asyncio.run(LLMProblemDecomposer(llm).decompose(
        question="Generate a valid contract.",
        evidence_profile={"evidence": []},
        input_catalog=_input_catalog("Generate a valid contract."),
        task={},
    ))

    for call in llm.calls:
        prompt = call["history"][0]["content"]
        assert "Do not wrap the JSON in Markdown code fences" in prompt
        assert "Escape every quote inside string values" in prompt


def test_modeler_requests_typed_model_ir_and_retries_complete_contract(tmp_path: Path) -> None:
    from app.guidance.modeler import LLMModeler
    from app.guidance.contracts import ProblemSpec
    from app.guidance.model_ir import ModelIR

    llm = StubLLM([{"model_id": "incomplete"}, _blocked_ir()])
    result = asyncio.run(LLMModeler(llm).model(
        problem_spec=ProblemSpec(
            task_summary="Estimate the model.",
            subproblems=[_subproblem()],
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


@pytest.mark.parametrize("omit_variables", [False, True], ids=["empty", "omitted"])
def test_modeler_retries_operator_graph_architecture_without_variables(
    tmp_path: Path, omit_variables: bool,
) -> None:
    from copy import deepcopy

    from app.guidance.contracts import ProblemSpec
    from app.guidance.modeler import LLMModeler

    valid = _operator_graph_draft()
    subproblem = valid["subproblems"][0]
    invalid = deepcopy(valid)
    if omit_variables:
        invalid["subproblems"][0].pop("variables")
    else:
        invalid["subproblems"][0]["variables"] = []
    llm = StubLLM([invalid, valid])

    result = asyncio.run(LLMModeler(
        llm,
        available_operator_ids=sorted({item["operator_id"] for item in subproblem["operators"]}),
    ).model(
        problem_spec=ProblemSpec(
            task_summary="Estimate the grouped model.",
            subproblems=[_subproblem(subproblem["subproblem_id"])],
        ),
        task={},
        work_dir=tmp_path,
    ))

    assert len(llm.calls) == 2
    assert "operator_graph architecture requires variables" in llm.calls[1]["history"][1]["content"]
    assert result.subproblems[0].execution_status == "solvable"
    component = result.subproblems[0].execution_components[0]
    assert [item.model_dump() for item in component.variables] == subproblem["variables"]
    diagnostic = json.loads(
        (tmp_path / "contract_diagnostics" / "GuidanceModeler.json").read_text(encoding="utf-8")
    )
    assert diagnostic["status"] == "recovered"
    assert diagnostic["attempts"][0]["error_type"] == "ValidationError"


def test_hybrid_architecture_rejects_empty_operator_graph_variables() -> None:
    from pydantic import ValidationError

    from app.guidance.model_ir_drafts import ModelIRGenerationDraft

    payload = _operator_graph_draft()
    payload["subproblems"][0]["execution_components"].append("indexed_linear_model")
    payload["subproblems"][0]["variables"] = []

    with pytest.raises(ValidationError, match="operator_graph architecture requires variables"):
        ModelIRGenerationDraft.model_validate(payload)


def test_modeler_and_reviser_receive_the_same_architecture_contract(tmp_path: Path) -> None:
    from app.guidance.modeler import LLMModeler
    from app.guidance.contracts import ProblemSpec
    from app.guidance.model_ir import (ModelIR)
    from app.guidance.model_review_contracts import (ModelIRReview)

    problem_spec = ProblemSpec(
        task_summary="Build an evidence-bound model.",
        subproblems=[_subproblem(objective="Estimate the result.")],
    )
    initial_llm = StubLLM(_blocked_ir())
    asyncio.run(LLMModeler(initial_llm).model(
        problem_spec=problem_spec,
        evidence_profile={"evidence": []},
        task={},
        work_dir=tmp_path,
    ))

    previous_model = ModelIR.model_validate(_blocked_ir())
    review = ModelIRReview(
        model_id=previous_model.model_id,
        assumption_checks=[],
        subproblem_reviews=[{
            "subproblem_id": "problem1",
            "status": "revision_required",
            "required_revisions": [_revision_requirement(
                "correct-model-architecture",
                "Correct the model architecture.",
            )],
        }],
    )
    revision_llm = StubLLM(_blocked_ir())
    asyncio.run(LLMModeler(revision_llm).revise(
        problem_spec=problem_spec,
        previous_model_ir=previous_model,
        review=review,
        evidence_profile={"evidence": []},
        task={},
        work_dir=tmp_path,
    ))

    initial_prompt = initial_llm.calls[0]["history"][0]["content"]
    revision_prompt = revision_llm.calls[0]["history"][0]["content"]
    shared_rules = [
        "execution_components",
        "同时包含 operator_graph",
        "架构阶段禁止自行输出 optimization.milp",
        "problem_spec.json#uncertainties/<uncertainty_id>",
        "题面锁定事实不是假设",
        "每条 assumption 的 alternative_scenarios 和 sensitivity_checks 必须非空",
        "禁止使用数组索引或集合级简写",
        "evidence_profile 中登记的来源视为实际存在",
    ]
    for rule in shared_rules:
        assert rule in initial_prompt
        assert rule in revision_prompt
    revision_instruction = (
        "逐条处理 model_review.subproblem_reviews[0].required_revisions"
    )
    assert revision_instruction not in initial_prompt
    assert revision_instruction in revision_prompt


def test_modeler_preserves_long_problem_context_and_evidence_profile(tmp_path: Path) -> None:
    from app.guidance.modeler import LLMModeler
    from app.guidance.contracts import ProblemSpec

    task_summary = "common physical premise: four operators per robot. " + "A" * 2000
    llm = StubLLM(_blocked_ir())
    asyncio.run(LLMModeler(llm).model(
        problem_spec=ProblemSpec(
            task_summary=task_summary,
            subproblems=[_subproblem(objective="Estimate output.")],
        ),
        evidence_profile={"evidence": [{
            "evidence_id": "upload.file.1",
            "kind": "uploaded_file",
            "source_ref": "data/input.xlsx",
            "source_file": "data/input.xlsx",
            "labels": [],
            "sha256": "a" * 64,
            "size_bytes": 1,
        }]},
        task={},
        work_dir=tmp_path,
    ))

    payload = json.loads(llm.calls[0]["history"][1]["content"])
    assert payload["task_summary"] == task_summary
    assert payload["subproblems"][0]["id"] == "problem1"
    assert payload["evidence_profile"]["evidence"][0]["source_ref"] == "data/input.xlsx"


def test_model_reviser_uses_compact_architecture_payload(tmp_path: Path) -> None:
    from app.guidance.modeler import LLMModeler
    from app.guidance.contracts import ProblemSpec
    from app.guidance.model_ir import (ModelIR)
    from app.guidance.model_review_contracts import (ModelIRReview)

    fixture_path = (
        Path(__file__).parent
        / "fixtures"
        / "model_ir"
        / "generic_multiperiod_inventory.json"
    )
    previous_payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    previous_payload["subproblems"][0]["subproblem_id"] = "problem1"
    previous_model = ModelIR.model_validate(previous_payload)
    draft = json.loads(json.dumps(previous_payload))
    draft_subproblem, indexed, _ = _prepare_indexed_draft(draft)
    draft_subproblem["execution_components"] = ["indexed_linear_model"]
    for candidate in draft_subproblem["candidate_models"]:
        candidate["operator_node_ids"] = []
    core = {
        "index_sets": [
            item for item in indexed["index_sets"]
            if item["id"] != "transition_period"
        ],
        "parameters": indexed["parameters"],
    }
    constraints = {
        "variables": indexed["variables"],
        "objective": indexed["objective"],
        "additional_index_sets": [
            item for item in indexed["index_sets"]
            if item["id"] == "transition_period"
        ],
        "constraints": indexed["constraints"],
    }
    review = ModelIRReview(
        model_id=previous_model.model_id,
        identifiability="reviewed",
        dimensional_consistency="reviewed",
        data_sufficiency="reviewed",
        computational_feasibility="reviewed",
        assumption_checks=[],
        subproblem_reviews=[{
            "subproblem_id": "problem1",
            "status": "revision_required",
            "required_revisions": [_revision_requirement(
                "restore-physical-multiplier",
                "Restore the omitted physical multiplier.",
            )],
        }],
    )
    evidence_profile = _indexed_evidence(core)
    llm = StubLLM([draft, core, constraints])

    asyncio.run(LLMModeler(
        llm,
        available_operator_ids=["optimization.milp"],
    ).revise(
        problem_spec=ProblemSpec(
            task_summary="A shared physical multiplier applies to every subproblem.",
            subproblems=[_subproblem(objective="Revise the plan.")],
        ),
        previous_model_ir=previous_model,
        review=review,
        evidence_profile=evidence_profile,
        task={},
        work_dir=tmp_path,
    ))

    prompt = llm.calls[0]["history"][0]["content"]
    payload = json.loads(llm.calls[0]["history"][1]["content"])
    assert "完整 ModelIR" not in prompt
    assert "previous_model_ir" not in payload
    assert "indexed_linear_model" not in payload["previous_model_summary"]
    assert payload["previous_model_summary"]["execution_components"] == [
        "indexed_linear_model",
    ]
    assert payload["model_review"]["subproblem_reviews"][0][
        "required_revisions"
    ] == [_revision_requirement(
        "restore-physical-multiplier",
        "Restore the omitted physical multiplier.",
    )]
    for call in llm.calls[1:]:
        stage_payload = json.loads(call["history"][1]["content"])
        assert stage_payload["problem_context"].startswith(
            "A shared physical multiplier"
        )
        assert stage_payload["model_review"]["subproblem_reviews"][0][
            "required_revisions"
        ] == [_revision_requirement(
            "restore-physical-multiplier",
            "Restore the omitted physical multiplier.",
        )]



def test_modeler_scopes_review_payload_to_target_subproblem() -> None:
    from app.guidance.modeling_context import _subproblem_review_payload
    from app.guidance.model_review_contracts import (ModelIRReview)

    review = ModelIRReview(
        model_id="mixed-review",
        assumption_checks=[],
        subproblem_reviews=[
            {
                "subproblem_id": "approved_problem",
                "status": "approved",
            },
            {
                "subproblem_id": "target_problem",
                "status": "revision_required",
                "required_revisions": [_revision_requirement(
                    "repair-target",
                    "Repair only the target subproblem.",
                )],
            },
        ],
    )

    payload = _subproblem_review_payload(review, "target_problem")

    assert payload["status"] == "revision_required"
    assert payload["executable_subproblem_ids"] == []
    assert payload["subproblem_reviews"] == [
        {
            "subproblem_id": "target_problem",
            "status": "revision_required",
            "blocking_issues": [],
            "required_revisions": [_revision_requirement(
                "repair-target",
                "Repair only the target subproblem.",
            )],
            "revision_checks": [],
            "mathematical_checks": [],
        }
    ]


def test_modeler_rejects_milp_as_an_untyped_operator_graph() -> None:
    from app.guidance.generation_validation import _validate_generation_draft_slice
    from app.guidance.contracts import ProblemSpec
    from app.guidance.model_ir_drafts import (ModelIRGenerationDraft)

    fixture_path = (
        Path(__file__).parent
        / "fixtures"
        / "model_ir"
        / "generic_multiperiod_inventory.json"
    )
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    subproblem, _, component = _prepare_indexed_draft(payload)
    subproblem["subproblem_id"] = "problem1"
    subproblem["execution_components"] = ["operator_graph"]
    subproblem["variables"] = [{
        "id": "solution", "role": "derived", "symbol": "x", "unit": "unit", "shape": [],
    }]
    subproblem["operators"] = [{
        "node_id": component["solver_node_id"],
        "operator_id": "optimization.milp",
        "outputs": component["outputs"],
    }]
    draft = ModelIRGenerationDraft.model_validate(payload)

    with pytest.raises(ValueError, match="system-managed"):
        _validate_generation_draft_slice(
            draft,
            expected_id="problem1",
            available_operator_ids=["optimization.milp"],
            problem_spec=ProblemSpec(
                task_summary="Estimate the model.",
                subproblems=[_subproblem()],
            ),
        )


def test_combination_draft_preserves_forecast_when_adding_indexed_solver() -> None:
    from app.guidance.model_ir_drafts import (ModelIRGenerationDraft)

    fixture_path = (
        Path(__file__).parent
        / "fixtures"
        / "model_ir"
        / "generic_multiperiod_inventory.json"
    )
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    subproblem, _, _ = _prepare_indexed_draft(payload)
    subproblem["subproblem_id"] = "problem1"
    subproblem["execution_components"] = [
        "operator_graph",
        "indexed_linear_model",
    ]
    subproblem["variables"] = [{
        "id": "forecast_bundle", "role": "derived", "symbol": "f", "unit": "unit", "shape": [],
    }]
    subproblem["operators"] = [{
        "node_id": "forecast",
        "operator_id": "forecast.future_basis_rolling_origin",
        "inputs": {
            "time": "time",
            "target": "target",
        },
        "outputs": ["forecast_bundle"],
        "parameters": {
            "future_time": [105.0, 106.0],
            "candidate_periods": [52.0],
            "season_length": 52,
            "initial_train_size": 52,
            "interval_level": 0.9,
        },
    }]
    for candidate in subproblem["candidate_models"]:
        candidate["operator_node_ids"] = ["forecast"]

    draft = ModelIRGenerationDraft.model_validate(payload)
    normalized = draft.normalize_indexed_execution()
    architecture = normalized.subproblems[0]

    assert [item.node_id for item in architecture.operators] == ["forecast"]
    assert all(
        item.operator_node_ids == ["forecast", "solve_indexed_model"]
        for item in architecture.candidate_models
    )


def test_modeler_promotes_forecast_bound_indexed_model_into_ordered_plan(
    tmp_path: Path,
) -> None:
    from app.guidance.modeler import LLMModeler
    from app.guidance.contracts import ProblemSpec
    from app.guidance.model_ir_compiler import ModelIRCompiler
    from app.guidance.operator_catalog import build_default_operator_registry
    from app.guidance.operators import OperatorDefinition, OperatorPort

    fixture_path = (
        Path(__file__).parent
        / "fixtures"
        / "model_ir"
        / "generic_multiperiod_inventory.json"
    )
    canonical = json.loads(fixture_path.read_text(encoding="utf-8"))
    architecture = json.loads(json.dumps(canonical))
    draft_subproblem, indexed, _ = _prepare_indexed_draft(architecture)
    draft_subproblem["execution_components"] = [
        "operator_graph",
        "indexed_linear_model",
    ]
    draft_subproblem["variables"] = [{
        "id": "forecast_bundle",
        "role": "derived",
        "symbol": "F",
        "unit": "unspecified",
        "shape": ["artifact", "field", "value"],
    }]
    draft_subproblem["operators"] = [{
        "node_id": "forecast",
        "operator_id": "forecast.test",
        "inputs": {},
        "outputs": ["forecast_bundle"],
        "parameters": {},
    }]
    for candidate in draft_subproblem["candidate_models"]:
        candidate["operator_node_ids"] = ["forecast"]

    parameters = []
    for parameter in indexed["parameters"]:
        values = parameter["values"]
        if parameter["id"] == "demand":
            parameters.append({
                "source_kind": "runtime_artifact",
                "id": "demand",
                "index_sets": parameter["index_sets"],
                "runtime_source": {
                    "artifact_ref": "forecast_bundle",
                    "field_path": ["future_predictions"],
                },
            })
        else:
            parameters.append({
                "source_kind": "static",
                "id": parameter["id"],
                "index_sets": parameter["index_sets"],
                "values": values,
            })
    core = {
        "index_sets": [
            item for item in indexed["index_sets"]
            if item["id"] != "transition_period"
        ],
        "parameters": parameters,
    }
    constraints = {
        "variables": indexed["variables"],
        "objective": indexed["objective"],
        "additional_index_sets": [
            item for item in indexed["index_sets"]
            if item["id"] == "transition_period"
        ],
        "constraints": indexed["constraints"],
    }

    evidence_profile = _indexed_evidence(core)
    model_ir = asyncio.run(LLMModeler(
        StubLLM([architecture, core, constraints]),
        available_operator_ids=["forecast.test", "optimization.milp"],
    ).model(
        problem_spec=ProblemSpec(
            task_summary="Forecast demand before optimizing inventory.",
            subproblems=[_subproblem(
                "finite_inventory_policy",
                objective="Produce a forecast-bound policy.",
            )],
        ),
        evidence_profile=evidence_profile,
        task={},
        work_dir=tmp_path,
    ))

    registry = build_default_operator_registry()
    registry.register(OperatorDefinition(
        operator_id="forecast.test",
        version="1.0.0",
        inputs={},
        outputs={"forecast": OperatorPort(value_kind="table", unit="unspecified")},
        deterministic=True,
        validator_id="forecast.test.validator.v1",
    ))
    compilation = ModelIRCompiler(registry=registry).compile(model_ir)

    assert compilation.status == "compiled", compilation.diagnostics
    assert [
        node.node_id for node in compilation.execution_plan.graph.nodes
    ] == ["forecast", "solve_indexed_model"]
    solve = compilation.execution_plan.graph.nodes[1]
    assert solve.dependencies == ["forecast"]
    assert solve.inputs == {"demand": "forecast_bundle"}



def test_canonical_fact_binding_rejects_uri_fragment() -> None:
    from app.guidance.model_ir import FactBindingSpec

    with pytest.raises(ValueError, match="JSON pointers"):
        FactBindingSpec.model_validate({
            "fact_id": "fact.capacity",
            "model_paths": ["#/variables/0"],
        })


def test_indexed_core_draft_exposes_runtime_parameter_source_to_constraints() -> None:
    from app.guidance.model_ir_drafts import (FormulationContext, IndexedLinearCoreDraft)

    core = IndexedLinearCoreDraft.model_validate({
        "index_sets": [{"id": "period", "values": ["p1", "p2"]}],
        "parameters": [{
            "source_kind": "runtime_artifact",
            "id": "demand",
            "index_sets": ["period"],
            "runtime_source": {
                "artifact_ref": "forecast_bundle",
                "field_path": ["future_predictions"],
            },
        }],
    })

    parameter = core.promote_core().parameters[0]
    assert parameter.runtime_source.artifact_ref == "forecast_bundle"
    context = FormulationContext.from_core(core)
    assert context.parameters[0].source_ref == (
        "artifact:forecast_bundle#/future_predictions"
    )


def test_indexed_draft_allows_system_managed_candidate_node_refs() -> None:
    from app.guidance.model_ir_drafts import (ModelIRGenerationDraft)

    fixture_path = (
        Path(__file__).parent
        / "fixtures"
        / "model_ir"
        / "generic_multiperiod_inventory.json"
    )
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    subproblem, _, _ = _prepare_indexed_draft(payload)
    subproblem["subproblem_id"] = "problem1"
    subproblem["execution_components"] = ["indexed_linear_model"]
    for candidate in subproblem["candidate_models"]:
        candidate["operator_node_ids"] = []

    draft = ModelIRGenerationDraft.model_validate(payload)
    normalized = draft.normalize_indexed_execution()

    assert draft.subproblems[0].variables == []
    assert all(
        item.operator_node_ids == ["solve_indexed_model"]
        for item in normalized.subproblems[0].candidate_models
    )


def test_pure_indexed_draft_rejects_unmanaged_candidate_node_refs() -> None:
    from app.guidance.model_ir_drafts import (ModelIRGenerationDraft)

    fixture_path = (
        Path(__file__).parent
        / "fixtures"
        / "model_ir"
        / "generic_multiperiod_inventory.json"
    )
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    subproblem, _, _ = _prepare_indexed_draft(payload)
    subproblem["subproblem_id"] = "problem1"
    subproblem["execution_components"] = ["indexed_linear_model"]

    with pytest.raises(ValueError, match="operator_node_ids require operator_graph"):
        ModelIRGenerationDraft.model_validate(payload)


def test_modeler_receives_the_runtime_operator_catalog(tmp_path: Path) -> None:
    from app.guidance.modeler import LLMModeler
    from app.guidance.contracts import ProblemSpec

    llm = StubLLM(_blocked_ir())
    asyncio.run(LLMModeler(
        llm,
        available_operator_ids=[
            "forecast.future_basis_rolling_origin",
            "optimization.milp",
        ],
    ).model(
        problem_spec=ProblemSpec(
            task_summary="Build an executable model.",
            subproblems=[_subproblem(objective="Optimize the plan.")],
        ),
        evidence_profile={"evidence": []},
        task={},
        work_dir=tmp_path,
    ))

    payload = json.loads(llm.calls[0]["history"][1]["content"])
    assert payload["available_operator_ids"] == [
        "forecast.future_basis_rolling_origin",
        "optimization.milp",
    ]


def test_modeler_generates_each_subproblem_separately_and_merges_contracts(tmp_path: Path) -> None:
    from app.guidance.modeler import LLMModeler
    from app.guidance.contracts import ProblemSpec

    llm = StubLLM([
        _blocked_ir("slice-one", "problem1"),
        _blocked_ir("slice-two", "problem2"),
    ])
    result = asyncio.run(LLMModeler(llm).model(
        problem_spec=ProblemSpec(
            task_summary="Build two independent models.",
            subproblems=[
                _subproblem("problem1", objective="Estimate output one."),
                _subproblem("problem2", objective="Estimate output two."),
            ],
        ),
        evidence_profile={"evidence": []},
        task={},
        work_dir=tmp_path,
    ))

    assert [item.subproblem_id for item in result.subproblems] == ["problem1", "problem2"]
    assert len(llm.calls) == 2
    request_payloads = [json.loads(call["history"][1]["content"]) for call in llm.calls]
    assert [payload["target_subproblem_id"] for payload in request_payloads] == [
        "problem1",
        "problem2",
    ]
    assert all(len(payload["subproblems"]) == 1 for payload in request_payloads)


def test_modeler_isolates_subproblem_contract_failure_and_continues(
    tmp_path: Path,
) -> None:
    from app.guidance.modeler import LLMModeler
    from app.guidance.contracts import ProblemSpec

    llm = StubLLM([
        "{invalid problem1 attempt one",
        "{invalid problem1 attempt two",
        _blocked_ir("slice-two", "problem2"),
    ])
    result = asyncio.run(LLMModeler(llm).model(
        problem_spec=ProblemSpec(
            task_summary="Build two independent models.",
            subproblems=[
                _subproblem(
                    "problem1",
                    objective="Estimate output one.",
                    required_fact_ids=["fact.shared_input"],
                ),
                _subproblem(
                    "problem2",
                    objective="Estimate output two.",
                    required_fact_ids=["fact.shared_input"],
                ),
            ],
            locked_facts=[{
                "fact_id": "fact.shared_input",
                "statement": "The shared input equals one.",
                "source_refs": ["inputs/problem.txt#paragraph-1"],
                "subproblem_ids": ["problem1", "problem2"],
                "semantic_key": "shared.input",
                "value": 1,
                "unit": "unit",
            }],
        ),
        evidence_profile={"evidence": []},
        task={},
        work_dir=tmp_path,
    ))

    assert [item.subproblem_id for item in result.subproblems] == ["problem1", "problem2"]
    assert result.subproblems[0].execution_status == "blocked"
    assert "modeling_failure_problem1.json" in result.subproblems[0].blocking_reason
    assert result.subproblems[1].blocking_reason == (
        "No registered operator covers the requested method."
    )
    failure = json.loads(
        (tmp_path / "modeling_failure_problem1.json").read_text(encoding="utf-8")
    )
    assert failure["subproblem_id"] == "problem1"
    assert "after 2 complete attempts" in failure["error"]
    assert len(llm.calls) == 3
    first_payload = json.loads(llm.calls[0]["history"][1]["content"])
    second_payload = json.loads(llm.calls[2]["history"][1]["content"])
    assert first_payload["required_fact_ids"] == ["fact.shared_input"]
    assert second_payload["required_fact_ids"] == ["fact.shared_input"]


@pytest.mark.parametrize("retry_wrong_family", [False, True])
def test_modeler_promotes_indexed_generation_draft_without_duplicate_ast(
    tmp_path: Path, retry_wrong_family: bool,
) -> None:
    from app.guidance.modeler import LLMModeler
    from app.guidance.contracts import ProblemSpec

    fixture_path = (
        Path(__file__).parent
        / "fixtures"
        / "model_ir"
        / "generic_multiperiod_inventory.json"
    )
    draft = json.loads(fixture_path.read_text(encoding="utf-8"))
    draft_subproblem, indexed, _ = _prepare_indexed_draft(draft)
    draft_subproblem["subproblem_id"] = "problem1"
    draft_subproblem["execution_components"] = ["indexed_linear_model"]
    for candidate in draft_subproblem["candidate_models"]:
        candidate["operator_node_ids"] = []
    core = {
        "index_sets": [
            item for item in indexed["index_sets"]
            if item["id"] != "transition_period"
        ],
        "parameters": indexed["parameters"],
    }
    constraints = {
        "variables": indexed["variables"],
        "objective": indexed["objective"],
        "additional_index_sets": [
            item for item in indexed["index_sets"]
            if item["id"] == "transition_period"
        ],
        "constraints": indexed["constraints"],
    }
    evidence_profile = _indexed_evidence(core)
    responses = [draft, core, constraints]
    if retry_wrong_family:
        invalid = json.loads(json.dumps(draft))
        invalid["subproblems"][0]["validation"]["model_families"].insert(0, "optimization")
        responses.insert(0, invalid)
    llm = StubLLM(responses)

    result = asyncio.run(LLMModeler(
        llm,
        available_operator_ids=["optimization.milp"],
    ).model(
        problem_spec=ProblemSpec(
            task_summary="Optimize a generic finite inventory system.",
            subproblems=[_subproblem(objective="Minimize total cost.")],
        ),
        evidence_profile=evidence_profile,
        task={},
        work_dir=tmp_path,
    ))

    result_subproblem = result.subproblems[0]
    indexed_component = next(
        component
        for component in result_subproblem.execution_components
        if component.kind == "indexed_linear"
    )
    assert indexed_component.model is not None
    assert result_subproblem.equations == []
    assert result_subproblem.constraints == []
    assert '"EquationSpec"' not in llm.calls[0]["history"][0]["content"]
    assert len(llm.calls) == 3 + int(retry_wrong_family)
    assert result_subproblem.validation.model_families == ["mixed_integer_linear_optimization"]
    if retry_wrong_family:
        assert "optimization" in invalid["subproblems"][0]["validation"]["model_families"]
        feedback = llm.calls[1]["history"][1]["content"]
        assert "validation_family_method_mismatch" in feedback
        assert "optimization.constrained_quadratic_multistart" in feedback
    assert indexed_component.solver_node_id == "solve_indexed_model"
    assert indexed_component.outputs == ["solution"]
    assert all(
        candidate.operator_node_ids == [indexed_component.solver_node_id]
        for candidate in result_subproblem.candidate_models
    )
    assert all(
        answer.value_ref == "solution"
        for answer in result.subproblems[0].final_answers
    )
    constraint_prompt = llm.calls[-1]["history"][0]["content"]
    assert "非零 offset 必须绑定到排除越界端点的 auxiliary index set" in (
        constraint_prompt
    )
    assert "首期或末期边界条件必须使用独立约束族" in constraint_prompt
    constraint_payload = json.loads(llm.calls[-1]["history"][1]["content"])
    assert "indexed_core" not in constraint_payload
    constraint_context = constraint_payload["constraint_context"]
    assert "dense_values" not in json.dumps(constraint_context)
    assert "objective" not in constraint_context
    assert len(json.dumps(constraint_context)) < len(json.dumps(core))
    assert constraint_context["index_sets"] == core["index_sets"]
    assert constraint_context["parameters"] == [
        {
            "id": parameter["id"],
            "index_sets": parameter["index_sets"],
            "source_ref": parameter["binding"]["source_ref"],
        }
        for parameter in core["parameters"]
    ]
    assert "variables" not in constraint_context



def test_indexed_constraints_reject_symbols_missing_from_validated_core() -> None:
    from pydantic import ValidationError

    from app.guidance.generation_validation import _validate_indexed_parts
    from app.guidance.model_ir_drafts import (FormulationContext, IndexedLinearFormulationDraft, IndexedLinearCoreDraft)

    core = IndexedLinearCoreDraft.model_validate({
        "index_sets": [{"id": "period", "values": [1, 2]}],
        "parameters": [],
    })
    constraints = IndexedLinearFormulationDraft.model_validate({
        "variables": [{
            "id": "inventory",
            "index_sets": ["period"],
            "domain": "continuous",
        }],
        "objective": {
            "sense": "minimize",
            "terms": [{
                "for_each": {"t": "period"},
                "coefficient": {"kind": "constant", "value": 1.0},
                "variable": {
                    "variable_id": "inventory",
                    "selectors": [{"binder": "t"}],
                },
            }],
        },
        "constraints": [{
            "id": "balance",
            "for_each": {"t": "period"},
            "terms": [{
                "coefficient": {"kind": "constant", "value": 1.0},
                "variable": {
                    "variable_id": "inventory",
                    "selectors": [{"binder": "t"}],
                },
            }],
            "relation": "greater_equal",
            "rhs": {
                "kind": "parameter",
                "parameter_id": "undeclared_demand",
                "selectors": [{"binder": "t"}],
            },
        }],
    })

    context = FormulationContext.from_core(core)
    with pytest.raises(ValidationError, match="Instance is frozen"):
        context.index_sets = ()
    with pytest.raises(ValidationError, match="Instance is frozen"):
        context.index_sets[0].values = (3,)

    with pytest.raises(ValueError, match="unknown parameter 'undeclared_demand'") as exc_info:
        _validate_indexed_parts(core.promote_core(), constraints, context)
    assert "allowed parameter ids=[]" in str(exc_info.value)
    assert "allowed variable ids=['inventory']" in str(exc_info.value)


def test_indexed_formulation_rejects_large_literal_period_unrolling() -> None:
    from copy import deepcopy
    from app.guidance.generation_validation import _validate_indexed_formulation_compactness
    from app.guidance.model_ir_drafts import (IndexedLinearFormulationDraft, IndexedLinearCoreDraft)

    core = IndexedLinearCoreDraft.model_validate({
        "index_sets": [{"id": "week", "values": list(range(1, 105))}],
        "parameters": [],
    }).promote_core()
    compact = {
        "variables": [{
            "id": "purchase",
            "index_sets": ["week"],
            "domain": "integer",
            "lower_bound": 0,
        }],
        "objective": {
            "sense": "minimize",
            "terms": [{
                "for_each": {"t": "week"},
                "coefficient": {"kind": "constant", "value": 1.0},
                "variable": {
                    "variable_id": "purchase",
                    "selectors": [{"binder": "t"}],
                },
            }],
        },
        "constraints": [{
            "id": "capacity",
            "for_each": {"t": "week"},
            "terms": [{
                "coefficient": {"kind": "constant", "value": 1.0},
                "variable": {
                    "variable_id": "purchase",
                    "selectors": [{"binder": "t"}],
                },
            }],
            "relation": "greater_equal",
            "rhs": {"kind": "constant", "value": 0.0},
        }],
    }
    _validate_indexed_formulation_compactness(core, IndexedLinearFormulationDraft.model_validate(compact))
    expanded_terms = [{
        "coefficient": {"kind": "constant", "value": 1},
        "variable": {"variable_id": "purchase", "selectors": [{"value": week}]},
    } for week in range(1, 13)]
    for area in ("objective", "constraints"):
        expanded = deepcopy(compact)
        if area == "objective":
            expanded["objective"]["terms"] = expanded_terms
        else:
            expanded["constraints"] = [{
                "id": f"capacity_{week}", "terms": [term],
                "relation": "greater_equal", "rhs": {"kind": "constant", "value": 0},
            } for week, term in enumerate(expanded_terms)]
        with pytest.raises(ValueError, match="for_each.*binder"):
            _validate_indexed_formulation_compactness(core, IndexedLinearFormulationDraft.model_validate(expanded))


def test_indexed_generation_drafts_limit_model_family_count() -> None:
    from pydantic import ValidationError

    from app.guidance.model_ir_drafts import (IndexedLinearFormulationDraft, IndexedLinearCoreDraft)

    term = {
        "coefficient": {"kind": "constant", "value": 1.0},
        "variable": {"variable_id": "x", "selectors": []},
    }
    with pytest.raises(ValidationError, match="objective.terms"):
        IndexedLinearFormulationDraft.model_validate({
            "variables": [{"id": "x", "domain": "continuous"}],
            "constraints": [{"id": "capacity", "terms": [term], "relation": "less_equal", "rhs": {"kind": "constant", "value": 1}}],
            "objective": {
                "sense": "minimize",
                "terms": [term for _ in range(51)],
            },
        })

    constraint = {
        "id": "capacity",
        "terms": [term],
        "relation": "less_equal",
        "rhs": {"kind": "constant", "value": 1.0},
    }
    with pytest.raises(ValidationError, match="constraints"):
        IndexedLinearFormulationDraft.model_validate({
            "additional_index_sets": [],
            "variables": [{"id": "x", "domain": "continuous"}],
            "objective": {"sense": "minimize", "terms": [term]},
            "constraints": [
                {**constraint, "id": f"capacity_{index}"}
                for index in range(51)
            ],
        })


def test_indexed_core_semantics_retry_before_constraints_generation(tmp_path: Path) -> None:
    from copy import deepcopy

    from app.guidance.modeler import LLMModeler
    from app.guidance.contracts import ProblemSpec

    fixture_path = (
        Path(__file__).parent
        / "fixtures"
        / "model_ir"
        / "generic_multiperiod_inventory.json"
    )
    draft = json.loads(fixture_path.read_text(encoding="utf-8"))
    draft_subproblem, indexed, _ = _prepare_indexed_draft(draft)
    draft_subproblem["subproblem_id"] = "problem1"
    draft_subproblem["execution_components"] = ["indexed_linear_model"]
    for candidate in draft_subproblem["candidate_models"]:
        candidate["operator_node_ids"] = []
    core = {
        "index_sets": [
            item for item in indexed["index_sets"]
            if item["id"] != "transition_period"
        ],
        "parameters": indexed["parameters"],
    }
    constraints = {
        "variables": indexed["variables"],
        "objective": indexed["objective"],
        "additional_index_sets": [
            item for item in indexed["index_sets"]
            if item["id"] == "transition_period"
        ],
        "constraints": indexed["constraints"],
    }
    evidence_profile = _indexed_evidence(core)
    invalid_core = deepcopy(core)
    invalid_core["parameters"][0]["index_sets"] = ["unknown_period"]
    llm = StubLLM([draft, invalid_core, core, constraints])

    result = asyncio.run(LLMModeler(
        llm,
        available_operator_ids=["optimization.milp"],
    ).model(
        problem_spec=ProblemSpec(
            task_summary="Optimize a generic finite inventory system.",
            subproblems=[_subproblem(objective="Minimize total cost.")],
        ),
        evidence_profile=evidence_profile,
        task={},
        work_dir=tmp_path,
    ))

    assert any(
        component.kind == "indexed_linear"
        for component in result.subproblems[0].execution_components
    )
    assert len(llm.calls) == 4
    assert "IndexedLinearCoreDraft" in llm.calls[2]["history"][0]["content"]
    assert "unknown parameter index sets" in llm.calls[2]["history"][1]["content"]



def test_indexed_constraint_draft_normalizes_null_without_weakening_canonical() -> None:
    from pydantic import ValidationError

    from app.guidance.model_ir import (IndexedLinearConstraint)
    from app.guidance.model_ir_drafts import (IndexedLinearFormulationDraft)

    constraint_payload = {
        "id": "capacity",
        "for_each": None,
        "terms": [{
            "for_each": None,
            "coefficient": {"kind": "constant", "value": 1.0},
            "variable": {
                "variable_id": "purchase",
                "selectors": [{"binder": None, "value": 1, "offset": 0}],
            },
        }],
        "relation": "greater_equal",
        "rhs": {"kind": "constant", "value": 0.0},
    }
    draft = IndexedLinearFormulationDraft.model_validate({
        "additional_index_sets": [],
        "variables": [{"id": "purchase", "index_sets": ["period"], "domain": "integer"}],
        "objective": {"sense": "minimize", "terms": constraint_payload["terms"]},
        "constraints": [constraint_payload],
    })
    promoted = draft.promote_constraints()[0]

    assert promoted.for_each == {}
    assert promoted.terms[0].for_each == {}
    assert promoted.terms[0].variable.selectors[0].value == 1
    schema = IndexedLinearFormulationDraft.model_json_schema()
    constraint_for_each = schema["$defs"]["IndexedLinearConstraintDraft"]["properties"]["for_each"]
    term_for_each = schema["$defs"]["IndexedLinearTermDraft"]["properties"]["for_each"]
    assert constraint_for_each["anyOf"][0]["maxProperties"] == 4
    assert term_for_each["anyOf"][0]["maxProperties"] == 4
    selector_items = schema["$defs"]["IndexedVariableReferenceDraft"]["properties"]["selectors"]["items"]
    assert selector_items["anyOf"] == [
        {"$ref": "#/$defs/BinderIndexSelectorDraft"},
        {"$ref": "#/$defs/LiteralIndexSelectorDraft"},
    ]
    binder_schema = schema["$defs"]["BinderIndexSelectorDraft"]
    literal_schema = schema["$defs"]["LiteralIndexSelectorDraft"]
    assert "binder" in binder_schema["required"]
    assert binder_schema["properties"]["value"]["const"] is None
    assert "value" in literal_schema["required"]
    assert literal_schema["properties"]["binder"]["const"] is None
    with pytest.raises(ValidationError, match="for_each"):
        IndexedLinearConstraint.model_validate(constraint_payload)


def test_model_reviewer_receives_problem_and_model_ir_contracts(tmp_path: Path) -> None:
    from app.guidance.reviewer import LLMModelReviewer
    from app.guidance.contracts import ProblemSpec
    from app.guidance.model_ir import ModelIR

    llm = StubLLM({
        "model_id": "operator-gap",
        "identifiability": "blocked item is explicit",
        "dimensional_consistency": "not applicable",
        "data_sufficiency": "missing operator disclosed",
        "computational_feasibility": "blocked honestly",
        "assumption_checks": [],
        "subproblem_reviews": [{
            "subproblem_id": "problem1",
            "status": "blocked",
            "blocking_issues": [
                "The compiler operator is not registered yet."
            ],
        }],
    })
    review = asyncio.run(LLMModelReviewer(llm).review(
        problem_spec=ProblemSpec(
            task_summary="Estimate the model.",
            subproblems=[_subproblem()],
        ),
        model_ir=ModelIR.model_validate(_blocked_ir()),
        evidence_profile={"evidence": []},
        task={},
        work_dir=tmp_path,
    ))

    payload = json.loads(llm.calls[0]["history"][1]["content"])
    assert review.status == "blocked"
    assert payload["problem_spec"]["task_summary"] == "Estimate the model."
    assert payload["model_ir"]["model_id"] == "operator-gap"
    assert "model_spec" not in payload


def test_model_reviewer_rejects_runtime_parameter_without_producing_operator(
    tmp_path: Path,
) -> None:
    from app.guidance.reviewer import LLMModelReviewer
    from app.guidance.contracts import ProblemSpec
    from app.guidance.model_ir import ModelIR

    fixture_path = (
        Path(__file__).parent
        / "fixtures"
        / "model_ir"
        / "generic_multiperiod_inventory.json"
    )
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    indexed_component = next(
        component
        for component in payload["subproblems"][0]["execution_components"]
        if component["kind"] == "indexed_linear"
    )
    demand = indexed_component["model"]["parameters"][0]
    demand.pop("values")
    demand["runtime_source"] = {
        "artifact_ref": "missing_forecast_bundle",
        "field_path": ["future_predictions"],
    }
    model_ir = ModelIR.model_validate(payload)
    llm = StubLLM({
        "model_id": model_ir.model_id,
        "status": "approved",
        "identifiability": "reviewed",
        "dimensional_consistency": "reviewed",
        "data_sufficiency": "reviewed",
        "computational_feasibility": "reviewed",
        "assumption_checks": [],
    })

    with pytest.raises(ValueError, match="indexed_runtime_source_unknown_artifact"):
        asyncio.run(LLMModelReviewer(llm).review(
            problem_spec=ProblemSpec(
                task_summary="Forecast demand before optimizing inventory.",
                subproblems=[_subproblem(
                    "finite_inventory_policy",
                    objective="Produce a forecast-bound policy.",
                )],
            ),
            model_ir=model_ir,
            evidence_profile={"evidence": []},
            task={},
            work_dir=tmp_path,
        ))

    assert llm.calls == []


def test_contract_failure_stops_after_two_attempts_and_saves_diagnostics(tmp_path: Path) -> None:
    from app.guidance.decomposer import LLMProblemDecomposer
    from app.guidance.contract_request import ContractGenerationError

    llm = StubLLM(["{bad json", "{still bad json"])
    with pytest.raises(ContractGenerationError, match="after 2 complete attempts"):
        asyncio.run(LLMProblemDecomposer(llm).decompose(
            question="完成题目。",
            evidence_profile={"evidence": []},
            input_catalog=_input_catalog("完成题目。"),
            task={"work_dir": str(tmp_path)},
        ))

    diagnostic = json.loads(
        (
            tmp_path
            / "contract_diagnostics"
            / "GuidanceDecomposerStructure.json"
        ).read_text(encoding="utf-8")
    )
    assert diagnostic["status"] == "failed"
    assert len(diagnostic["attempts"]) == 2
    assert diagnostic["attempts"][1]["error_type"] == "JSONDecodeError"


def test_contract_request_saves_diagnostic_when_llm_fails(tmp_path: Path) -> None:
    from app.guidance.contract_request import StructuredContractRequester
    from app.guidance.contracts import ProblemSpec

    class FailingLLM:
        def __init__(self) -> None:
            self.call_count = 0

        async def chat(self, **kwargs):
            self.call_count += 1
            raise RuntimeError("GuidanceModeler: upstream request failed")

    llm = FailingLLM()
    requester = StructuredContractRequester(
        llm=llm,
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
    assert llm.call_count == 1
    assert diagnostic["attempts"][0]["error_type"] == "RuntimeError"
    assert diagnostic["attempts"][0]["transport_attempt"] == 1


def test_contract_request_retries_same_content_once_after_project_timeout(
    tmp_path: Path,
) -> None:
    from app.guidance.contract_request import StructuredContractRequester
    from app.guidance.contracts import ProblemSpec

    class FlakyTimeoutLLM:
        request_timeout = 0.01

        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def chat(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                await asyncio.sleep(60)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content=json.dumps({
                    "task_summary": "Solve the supplied problem.",
                    "subproblems": [_subproblem()],
                })
            ))])

    llm = FlakyTimeoutLLM()
    requester = StructuredContractRequester(
        llm=llm,
        model_type=ProblemSpec,
        system_prompt="decompose problem",
        agent_name="GuidanceModeler",
        artifact_dir=tmp_path,
    )

    result = asyncio.run(requester.request({"question": "test"}))

    assert result.task_summary == "Solve the supplied problem."
    assert len(llm.calls) == 2
    assert llm.calls[0]["history"] == llm.calls[1]["history"]
    diagnostic = json.loads(
        (tmp_path / "contract_diagnostics" / "GuidanceModeler.json").read_text(
            encoding="utf-8"
        )
    )
    assert diagnostic["status"] == "recovered"
    assert [attempt["transport_attempt"] for attempt in diagnostic["attempts"]] == [1, 2]


def test_contract_request_stops_after_two_project_timeout_attempts(tmp_path: Path) -> None:
    from app.guidance.contract_request import StructuredContractRequester
    from app.guidance.contracts import ProblemSpec

    class HangingLLM:
        request_timeout = 0.01

        def __init__(self) -> None:
            self.call_count = 0

        async def chat(self, **kwargs):
            self.call_count += 1
            await asyncio.sleep(60)

    llm = HangingLLM()
    requester = StructuredContractRequester(
        llm=llm,
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
    assert diagnostic["status"] == "failed"
    assert llm.call_count == 2
    assert [attempt["attempt"] for attempt in diagnostic["attempts"]] == [1, 1]
    assert [attempt["transport_attempt"] for attempt in diagnostic["attempts"]] == [1, 2]
    assert {
        attempt["error_type"] for attempt in diagnostic["attempts"]
    } == {"ContractRequestTimeout"}
