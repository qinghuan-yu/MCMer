import json
import re
from types import SimpleNamespace
from pathlib import Path

import pytest
from pydantic import ValidationError


def _structured_problem_spec_payload() -> dict:
    return {
        "task_summary": "Build an evidence-bound model.",
        "subproblems": [
            {
                "id": "Q1",
                "objective": "Minimize cost.",
                "required_fact_ids": ["fact.component_ratio"],
                "required_uncertainty_ids": [],
                "depends_on_subproblem_ids": [],
            },
            {
                "id": "Q2",
                "objective": "Minimize cost with losses.",
                "required_fact_ids": ["fact.component_ratio"],
                "required_uncertainty_ids": ["uncertainty.future_demand"],
                "depends_on_subproblem_ids": ["Q1"],
            },
        ],
        "locked_facts": [
            {
                "fact_id": "fact.component_ratio",
                "statement": "Each assembled unit requires four manipulators.",
                "source_refs": ["inputs/problem.docx#paragraph-2"],
                "subproblem_ids": ["Q1", "Q2"],
                "semantic_key": "assembly.manipulators_per_unit",
                "value": 4,
                "unit": "manipulator/unit",
            }
        ],
        "uncertainties": [
            {
                "uncertainty_id": "uncertainty.future_demand",
                "statement": "Future demand must be forecast.",
                "source_refs": ["inputs/problem.docx#paragraph-5"],
                "subproblem_ids": ["Q2"],
                "semantic_key": "demand.future",
                "candidate_values": [],
                "unit": "unit/week",
            }
        ],
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
    facts = {
        "locked_facts": payload.get("locked_facts", []),
        "uncertainties": payload.get("uncertainties", []),
    }
    return [structure, facts]


def _input_catalog() -> dict:
    return {
        "schema_version": "1.0",
        "source_file": "inputs/problem.docx",
        "source_sha256": "a" * 64,
        "blocks": [
            {
                "kind": "paragraph",
                "block_id": "paragraph-2",
                "source_ref": "inputs/problem.docx#paragraph-2",
                "sha256": "b" * 64,
                "text": "Each assembled unit requires four manipulators.",
                "rows": [],
            },
            {
                "kind": "paragraph",
                "block_id": "paragraph-5",
                "source_ref": "inputs/problem.docx#paragraph-5",
                "sha256": "c" * 64,
                "text": "Future demand must be forecast.",
                "rows": [],
            },
        ],
    }


class _StubLLM:
    def __init__(self, responses: dict | list[dict]) -> None:
        self.responses = responses if isinstance(responses, list) else [responses]
        self.calls: list[dict] = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.responses[len(self.calls) - 1]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps(payload, ensure_ascii=False)
        ))])


def test_problem_spec_accepts_structured_evidence_bound_facts() -> None:
    from app.guidance.contracts import ProblemSpec, SubproblemSpec

    problem_spec = ProblemSpec.model_validate(_structured_problem_spec_payload())

    assert isinstance(problem_spec.subproblems[0], SubproblemSpec)
    assert problem_spec.subproblems[1].depends_on_subproblem_ids == ["Q1"]
    assert problem_spec.locked_facts[0].fact_id == "fact.component_ratio"
    assert problem_spec.locked_facts[0].subproblem_ids == ["Q1", "Q2"]
    assert problem_spec.uncertainties[0].uncertainty_id == "uncertainty.future_demand"
    assert problem_spec.uncertainties[0].subproblem_ids == ["Q2"]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda payload: payload["subproblems"][0].update(
                required_fact_ids=[]
            ),
            "fact reference coverage mismatch",
        ),
        (
            lambda payload: payload["subproblems"][0][
                "required_fact_ids"
            ].append("fact.unknown"),
            "unknown required fact",
        ),
        (
            lambda payload: payload["subproblems"][0][
                "required_uncertainty_ids"
            ].append("uncertainty.future_demand"),
            "uncertainty reference scope mismatch",
        ),
        (
            lambda payload: payload["subproblems"][0][
                "depends_on_subproblem_ids"
            ].append("Q9"),
            "unknown subproblem dependency",
        ),
    ],
)
def test_problem_spec_rejects_invalid_subproblem_references(
    mutation,
    message: str,
) -> None:
    from app.guidance.contracts import ProblemSpec

    payload = _structured_problem_spec_payload()
    mutation(payload)

    with pytest.raises(ValidationError, match=message):
        ProblemSpec.model_validate(payload)


def test_problem_spec_rejects_circular_subproblem_dependencies() -> None:
    from app.guidance.contracts import ProblemSpec

    payload = _structured_problem_spec_payload()
    payload["subproblems"][0]["depends_on_subproblem_ids"] = ["Q2"]

    with pytest.raises(ValidationError, match="subproblem dependency cycle"):
        ProblemSpec.model_validate(payload)


def test_problem_spec_rejects_legacy_string_facts() -> None:
    from app.guidance.contracts import ProblemSpec

    payload = _structured_problem_spec_payload()
    payload["locked_facts"] = ["Each assembled unit requires four manipulators."]

    with pytest.raises(ValidationError):
        ProblemSpec.model_validate(payload)


def test_problem_spec_requires_data_sources_to_enter_the_fact_graph() -> None:
    from app.guidance.contracts import ProblemDataSourceSpec, ProblemSpec

    payload = _structured_problem_spec_payload()
    payload["data_sources"] = [{
        "id": "future_demand",
        "description": "Historical demand used to forecast future demand.",
        "source_ref": "inputs/problem.docx#paragraph-5",
    }]

    problem_spec = ProblemSpec.model_validate(payload)

    assert isinstance(problem_spec.data_sources[0], ProblemDataSourceSpec)

    payload["data_sources"][0]["source_ref"] = "inputs/problem.docx#table-9"
    with pytest.raises(
        ValidationError,
        match="data source evidence coverage mismatch",
    ):
        ProblemSpec.model_validate(payload)


def test_problem_spec_rejects_unknown_scope_and_conflicting_values() -> None:
    from app.guidance.contracts import ProblemSpec

    unknown_scope = _structured_problem_spec_payload()
    unknown_scope["locked_facts"][0]["subproblem_ids"] = ["Q3"]
    with pytest.raises(ValidationError, match="unknown subproblem"):
        ProblemSpec.model_validate(unknown_scope)

    conflicting = _structured_problem_spec_payload()
    conflicting["locked_facts"].append({
        **conflicting["locked_facts"][0],
        "fact_id": "fact.component_ratio_conflict",
        "subproblem_ids": ["Q2"],
        "value": 1,
    })
    conflicting["subproblems"][1]["required_fact_ids"].append(
        "fact.component_ratio_conflict"
    )
    with pytest.raises(ValidationError, match="conflicting locked facts"):
        ProblemSpec.model_validate(conflicting)


def test_problem_fact_sources_must_exist_in_input_catalog() -> None:
    from app.guidance.contracts import ProblemSpec
    from app.guidance.evidence_consistency import validate_problem_evidence_consistency

    problem_spec = ProblemSpec.model_validate(_structured_problem_spec_payload())
    validate_problem_evidence_consistency(problem_spec, {"evidence": []}, _input_catalog())

    problem_spec.locked_facts[0].source_refs = ["inputs/problem.docx#paragraph-999"]
    with pytest.raises(ValueError, match="unknown registered evidence source_ref"):
        validate_problem_evidence_consistency(problem_spec, {"evidence": []}, _input_catalog())


def test_decomposer_receives_input_catalog_as_fact_source_contract() -> None:
    from app.guidance.decomposer import LLMProblemDecomposer

    llm = _StubLLM(_split_decomposition_payload(
        _structured_problem_spec_payload()
    ))
    result = __import__("asyncio").run(LLMProblemDecomposer(llm).decompose(
        question="Build an evidence-bound model.",
        evidence_profile={"evidence": []},
        input_catalog=_input_catalog(),
        task={},
    ))

    request_payload = json.loads(llm.calls[1]["history"][1]["content"])
    structure_prompt = llm.calls[0]["history"][0]["content"]
    facts_prompt = llm.calls[1]["history"][0]["content"]
    assert request_payload["input_catalog"]["blocks"][0]["source_ref"].endswith(
        "#paragraph-2"
    )
    assert result.locked_facts[0].source_refs == ["inputs/problem.docx#paragraph-2"]
    assert "depends_on_subproblem_ids" in structure_prompt
    assert "系统会根据 subproblem_ids 确定性投影" in facts_prompt
    assert "data_sources 的每个 source_ref" in facts_prompt
    assert "题面正文和短参数表中的每个数值条件" in facts_prompt
    assert "input_catalog.blocks 或 evidence_profile.evidence" in facts_prompt


def test_decomposer_splits_structure_and_facts_then_projects_references() -> None:
    from app.guidance.decomposer import LLMProblemDecomposer

    payload = _structured_problem_spec_payload()
    llm = _StubLLM(_split_decomposition_payload(payload))

    result = __import__("asyncio").run(LLMProblemDecomposer(llm).decompose(
        question="Build an evidence-bound model.",
        evidence_profile={"evidence": []},
        input_catalog=_input_catalog(),
        task={},
    ))

    assert len(llm.calls) == 2
    assert "题目结构" in llm.calls[0]["history"][0]["content"]
    assert "事实与歧义" in llm.calls[1]["history"][0]["content"]
    fact_request = json.loads(llm.calls[1]["history"][1]["content"])
    assert [item["id"] for item in fact_request["subproblems"]] == ["Q1", "Q2"]
    assert result.subproblems[0].required_fact_ids == ["fact.component_ratio"]
    assert result.subproblems[1].required_fact_ids == ["fact.component_ratio"]
    assert result.subproblems[1].required_uncertainty_ids == [
        "uncertainty.future_demand"
    ]


def test_decomposer_fact_request_compacts_large_registered_tables() -> None:
    from app.guidance.decomposer import LLMProblemDecomposer

    source_ref = "inputs/problem.docx#table-1"
    rows = [
        [f"row-{row_index}", *[str(value) for value in range(8)]]
        for row_index in range(13)
    ]
    input_catalog = _input_catalog()
    input_catalog["blocks"].append({
        "kind": "table",
        "block_id": "table-1",
        "source_ref": source_ref,
        "sha256": "d" * 64,
        "text": "",
        "rows": rows,
    })
    evidence_profile = {
        "evidence": [{
            "evidence_id": "question.table.1",
            "kind": "embedded_table",
            "source_ref": source_ref,
            "source_file": "inputs/problem.docx",
            "labels": ["Appendix 1"],
            "sha256": "d" * 64,
            "size_bytes": 1000,
            "rows": rows,
            "profile": {"row_count": 13, "column_count": 9},
        }]
    }
    payload = {
        "task_summary": "Optimize from a registered demand dataset.",
        "subproblems": [{
            "id": "Q1",
            "objective": "Minimize cost.",
            "required_fact_ids": ["fact.demand_dataset"],
            "required_uncertainty_ids": [],
            "depends_on_subproblem_ids": [],
        }],
        "data_sources": [{
            "id": "demand_dataset",
            "description": "Registered weekly demand observations.",
            "source_ref": source_ref,
        }],
        "source_requirements": [],
        "locked_facts": [{
            "fact_id": "fact.demand_dataset",
            "statement": "Weekly demand is supplied by the registered table.",
            "source_refs": [source_ref],
            "subproblem_ids": ["Q1"],
            "semantic_key": "demand.observed_dataset",
            "value": "registered_dataset",
            "unit": "unit/week",
        }],
        "uncertainties": [],
    }
    llm = _StubLLM(_split_decomposition_payload(payload))

    __import__("asyncio").run(LLMProblemDecomposer(llm).decompose(
        question=payload["task_summary"],
        evidence_profile=evidence_profile,
        input_catalog=input_catalog,
        task={},
    ))

    fact_request = json.loads(llm.calls[1]["history"][1]["content"])
    assert "question" not in fact_request
    catalog_table = fact_request["input_catalog"]["blocks"][-1]
    evidence_table = fact_request["evidence_profile"]["evidence"][0]
    assert catalog_table["rows"] == []
    assert catalog_table["profile"] == {"row_count": 13, "column_count": 9}
    assert catalog_table["content_omitted"] is True
    assert evidence_table["rows"] == []
    assert evidence_table["content_omitted"] is True
    assert "不得逐行或逐单元格展开" in llm.calls[1]["history"][0]["content"]


def test_decomposer_keeps_only_registered_dataset_sources() -> None:
    from app.guidance.decomposer import LLMProblemDecomposer

    table_ref = "inputs/problem.docx#table-1"
    input_catalog = _input_catalog()
    input_catalog["blocks"].append({
        "kind": "table",
        "block_id": "table-1",
        "source_ref": table_ref,
        "sha256": "d" * 64,
        "text": "",
        "rows": [["week", "demand"], ["1", "10"]],
    })
    evidence_profile = {
        "evidence": [{
            "evidence_id": "question.table.1",
            "kind": "embedded_table",
            "source_ref": table_ref,
            "source_file": "inputs/problem.docx",
            "labels": [],
            "sha256": "d" * 64,
            "size_bytes": 20,
            "rows": [["week", "demand"], ["1", "10"]],
            "profile": {"row_count": 2, "column_count": 2},
        }]
    }
    structure = {
        "task_summary": "Optimize from registered demand.",
        "subproblems": [{
            "id": "Q1",
            "objective": "Minimize cost.",
            "depends_on_subproblem_ids": [],
        }],
        "data_sources": [
            {
                "id": "question_text",
                "description": "The prose problem statement.",
                "source_ref": "inputs/problem.docx#paragraph-2",
            },
            {
                "id": "demand_dataset",
                "description": "Observed demand.",
                "source_ref": table_ref,
            },
        ],
        "source_requirements": [],
    }
    facts = {
        "locked_facts": [{
            "fact_id": "fact.demand_dataset",
            "statement": "Demand is supplied by the registered table.",
            "source_refs": [table_ref],
            "subproblem_ids": ["Q1"],
            "semantic_key": "demand.observed_dataset",
            "value": "registered_dataset",
            "unit": "unit/week",
        }],
        "uncertainties": [],
    }
    llm = _StubLLM([structure, facts, facts])

    result = __import__("asyncio").run(LLMProblemDecomposer(llm).decompose(
        question=structure["task_summary"],
        evidence_profile=evidence_profile,
        input_catalog=input_catalog,
        task={},
    ))

    assert [item.id for item in result.data_sources] == ["demand_dataset"]
    fact_request = json.loads(llm.calls[1]["history"][1]["content"])
    assert [item["id"] for item in fact_request["data_sources"]] == [
        "demand_dataset"
    ]


def test_decomposer_promotes_unicode_identifiers_and_synchronizes_scopes() -> None:
    from app.guidance.decomposer import LLMProblemDecomposer

    payload = _structured_problem_spec_payload()
    payload["subproblems"] = [{
        "id": "P1",
        "objective": "Minimize cost.",
        "required_fact_ids": ["container艇.purchase.price"],
        "required_uncertainty_ids": ["未来需求.uncertainty"],
        "depends_on_subproblem_ids": [],
    }]
    payload["locked_facts"] = [{
        **payload["locked_facts"][0],
        "fact_id": "container艇.purchase.price",
        "subproblem_ids": ["P1"],
        "semantic_key": "container艇.purchase.price",
    }]
    payload["uncertainties"] = [{
        **payload["uncertainties"][0],
        "uncertainty_id": "未来需求.uncertainty",
        "subproblem_ids": ["P1"],
        "semantic_key": "未来需求.weekly",
    }]

    results = []
    for _ in range(2):
        llm = _StubLLM(_split_decomposition_payload(payload))
        results.append(__import__("asyncio").run(
            LLMProblemDecomposer(llm).decompose(
                question="Build an evidence-bound model.",
                evidence_profile={"evidence": []},
                input_catalog=_input_catalog(),
                task={},
            )
        ))

    first, second = results
    assert first.locked_facts[0].fact_id == second.locked_facts[0].fact_id
    assert first.uncertainties[0].uncertainty_id == second.uncertainties[0].uncertainty_id
    assert re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", first.locked_facts[0].fact_id)
    assert re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_.-]*",
        first.uncertainties[0].uncertainty_id,
    )
    assert re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_.-]*",
        first.locked_facts[0].semantic_key,
    )
    assert first.locked_facts[0].subproblem_ids == ["problem1"]
    assert first.uncertainties[0].subproblem_ids == ["problem1"]
    assert first.subproblems[0].required_fact_ids == [
        first.locked_facts[0].fact_id
    ]
    assert first.subproblems[0].required_uncertainty_ids == [
        first.uncertainties[0].uncertainty_id
    ]


def test_problem_structure_draft_excludes_projected_fact_references() -> None:
    from app.guidance.contracts import ProblemStructureGenerationDraft

    schema = ProblemStructureGenerationDraft.model_json_schema()
    subproblem_schema = schema["$defs"]["SubproblemStructureGenerationDraft"]

    assert "depends_on_subproblem_ids" in subproblem_schema["required"]
    assert "required_fact_ids" not in subproblem_schema["properties"]
    assert "required_uncertainty_ids" not in subproblem_schema["properties"]


def test_problem_structure_and_facts_promote_dependency_references() -> None:
    from app.guidance.contracts import (
        ProblemFactsGenerationDraft,
        ProblemStructureGenerationDraft,
    )

    payload = {
        "task_summary": "Solve two dependent subproblems.",
        "subproblems": [
            {
                "id": "P1",
                "objective": "Estimate the state.",
                "depends_on_subproblem_ids": [],
            },
            {
                "id": "P2",
                "objective": "Optimize from the estimated state.",
                "depends_on_subproblem_ids": ["P1"],
            },
        ],
    }

    promoted = ProblemStructureGenerationDraft.model_validate(payload).promote(
        ProblemFactsGenerationDraft()
    )

    assert [item.id for item in promoted.subproblems] == ["problem1", "problem2"]
    assert promoted.subproblems[1].depends_on_subproblem_ids == ["problem1"]


def test_decomposer_rejects_identifier_normalization_collisions() -> None:
    from app.guidance.decomposer import LLMProblemDecomposer
    from app.guidance.contract_request import ContractGenerationError

    payload = _structured_problem_spec_payload()
    payload["subproblems"] = [{
        "id": "P1",
        "objective": "Minimize cost.",
        "required_fact_ids": [
            "Fact Purchase Price",
            "fact.purchase.price",
        ],
        "required_uncertainty_ids": [],
        "depends_on_subproblem_ids": [],
    }]
    payload["locked_facts"] = [
        {
            **payload["locked_facts"][0],
            "fact_id": "Fact Purchase Price",
            "subproblem_ids": ["P1"],
        },
        {
            **payload["locked_facts"][0],
            "fact_id": "fact.purchase.price",
            "subproblem_ids": ["P1"],
        },
    ]
    payload["uncertainties"] = []
    structure, facts = _split_decomposition_payload(payload)
    llm = _StubLLM([structure, facts, facts])

    with pytest.raises(
        ContractGenerationError,
        match="fact_id normalization collision",
    ):
        __import__("asyncio").run(LLMProblemDecomposer(llm).decompose(
            question="Build an evidence-bound model.",
            evidence_profile={"evidence": []},
            input_catalog=_input_catalog(),
            task={},
        ))


def test_decomposer_rejects_subproblem_identifier_collisions() -> None:
    from app.guidance.decomposer import LLMProblemDecomposer
    from app.guidance.contract_request import ContractGenerationError

    payload = _structured_problem_spec_payload()
    payload["subproblems"] = [
        {
            "id": "P1",
            "objective": "First formulation.",
            "required_fact_ids": [],
            "required_uncertainty_ids": [],
            "depends_on_subproblem_ids": [],
        },
        {
            "id": "problem_1",
            "objective": "Second formulation.",
            "required_fact_ids": [],
            "required_uncertainty_ids": [],
            "depends_on_subproblem_ids": [],
        },
    ]
    payload["locked_facts"] = []
    payload["uncertainties"] = []
    structure, facts = _split_decomposition_payload(payload)
    llm = _StubLLM([structure, facts, facts])

    with pytest.raises(
        ContractGenerationError,
        match="subproblem_id normalization collision",
    ):
        __import__("asyncio").run(LLMProblemDecomposer(llm).decompose(
            question="Build an evidence-bound model.",
            evidence_profile={"evidence": []},
            input_catalog=_input_catalog(),
            task={},
        ))


def _fact_coverage_problem_spec_payload() -> dict:
    payload = _structured_problem_spec_payload()
    payload["subproblems"] = [{
        "id": "problem1",
        "objective": "Minimize cost.",
        "required_fact_ids": ["fact.objective_sense"],
        "required_uncertainty_ids": [],
        "depends_on_subproblem_ids": [],
    }]
    payload["locked_facts"] = [{
        "fact_id": "fact.objective_sense",
        "statement": "The objective must be minimized.",
        "source_refs": ["inputs/problem.docx#paragraph-2"],
        "subproblem_ids": ["problem1"],
        "semantic_key": "objective.sense",
        "value": "minimize",
        "unit": "",
    }]
    payload["uncertainties"] = []
    return payload


def _fact_coverage_model_payload() -> dict:
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "model_ir"
        / "generic_multiperiod_inventory.json"
    )
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    payload["subproblems"][0]["subproblem_id"] = "problem1"
    payload["subproblems"][0]["fact_bindings"] = [{
        "fact_id": "fact.objective_sense",
        "model_paths": ["/execution_components/0/model/objective/sense"],
    }]
    return payload


def _blocked_model(subproblem_id: str) -> dict:
    return {
        "model_id": f"blocked-{subproblem_id}",
        "subproblems": [{
            "execution_status": "blocked",
            "subproblem_id": subproblem_id,
            "objective": "Resolve the requested model.",
            "blocking_reason": "A required operator is unavailable.",
            "missing_inputs": ["registered operator"],
            "recovery_action": "Register and verify the required operator.",
            "expected_outputs": ["verified result"],
        }],
    }


def test_modeler_slices_structured_facts_by_target_subproblem(tmp_path: Path) -> None:
    import asyncio

    from app.guidance.modeler import LLMModeler
    from app.guidance.contracts import ProblemSpec

    payload = _structured_problem_spec_payload()
    payload["locked_facts"].append({
        "fact_id": "fact.loss_rate",
        "statement": "The second model uses a fixed loss rate.",
        "source_refs": ["inputs/problem.docx#paragraph-5"],
        "subproblem_ids": ["Q2"],
        "semantic_key": "process.loss_rate",
        "value": 0.2,
        "unit": "fraction/week",
    })
    payload["subproblems"][1]["required_fact_ids"].append("fact.loss_rate")
    payload["data_sources"] = [{
        "id": "future_demand",
        "description": "Historical demand used to forecast future demand.",
        "source_ref": "inputs/problem.docx#paragraph-5",
    }]
    llm = _StubLLM([_blocked_model("Q1"), _blocked_model("Q2")])

    asyncio.run(LLMModeler(llm).model(
        problem_spec=ProblemSpec.model_validate(payload),
        evidence_profile={"evidence": []},
        task={},
        work_dir=tmp_path,
    ))

    first_payload = json.loads(llm.calls[0]["history"][1]["content"])
    second_payload = json.loads(llm.calls[1]["history"][1]["content"])
    assert [item["fact_id"] for item in first_payload["locked_facts"]] == [
        "fact.component_ratio"
    ]
    assert [item["fact_id"] for item in second_payload["locked_facts"]] == [
        "fact.component_ratio",
        "fact.loss_rate",
    ]
    assert first_payload["required_fact_ids"] == ["fact.component_ratio"]
    assert second_payload["required_fact_ids"] == [
        "fact.component_ratio",
        "fact.loss_rate",
    ]
    assert second_payload["subproblems"][0]["depends_on_subproblem_ids"] == ["Q1"]
    assert second_payload["dependency_subproblems"] == [
        first_payload["subproblems"][0]
    ]
    assert second_payload["data_sources"] == payload["data_sources"]


class _MissingFactBindingModeler:
    async def model(self, **kwargs):
        from app.guidance.model_ir import ModelIR

        payload = _fact_coverage_model_payload()
        payload["subproblems"][0]["fact_bindings"] = []
        return ModelIR.model_validate(payload)


def test_modeling_stage_rejects_missing_fact_coverage(tmp_path: Path) -> None:
    import asyncio

    from app.guidance.contracts import ProblemSpec
    from app.guidance.stages import ModelingStage

    problem_spec = ProblemSpec.model_validate(_fact_coverage_problem_spec_payload())
    (tmp_path / "problem_spec.json").write_text(
        problem_spec.model_dump_json(indent=2),
        encoding="utf-8",
    )
    (tmp_path / "evidence_profile.json").write_text(
        json.dumps({"schema_version": "1.0", "evidence": []}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="fact coverage mismatch"):
        asyncio.run(ModelingStage(modeler=_MissingFactBindingModeler()).run(
            task_id="fact-coverage",
            task={"work_dir": str(tmp_path)},
            input_path=tmp_path / "problem_spec.json",
            output_path=tmp_path / "model_ir.json",
        ))


def test_solvable_model_ir_covers_every_applicable_locked_fact() -> None:
    from app.guidance.contracts import ProblemSpec
    from app.guidance.model_ir import ModelIR
    from app.guidance.model_ir_semantics import validate_model_fact_coverage

    problem_spec = ProblemSpec.model_validate(_fact_coverage_problem_spec_payload())
    model_ir = ModelIR.model_validate(_fact_coverage_model_payload())

    validate_model_fact_coverage(problem_spec, model_ir)


@pytest.mark.parametrize(
    ("bindings", "message"),
    [
        ([], "fact coverage mismatch"),
        (
            [
                {
                    "fact_id": "fact.objective_sense",
                    "model_paths": ["/execution_components/0/model/objective/sense"],
                },
                {
                    "fact_id": "fact.objective_sense",
                    "model_paths": ["/execution_components/0/model/objective/sense"],
                },
            ],
            "duplicate fact bindings",
        ),
        (
            [{"fact_id": "fact.unknown", "model_paths": ["/objective"]}],
            "unknown locked fact",
        ),
        (
            [{"fact_id": "fact.objective_sense", "model_paths": ["/missing/0"]}],
            "model path does not exist",
        ),
    ],
)
def test_model_fact_coverage_rejects_incomplete_or_invalid_bindings(
    bindings: list[dict],
    message: str,
) -> None:
    from app.guidance.contracts import ProblemSpec
    from app.guidance.model_ir import ModelIR
    from app.guidance.model_ir_semantics import validate_model_fact_coverage

    problem_spec = ProblemSpec.model_validate(_fact_coverage_problem_spec_payload())
    payload = _fact_coverage_model_payload()
    payload["subproblems"][0]["fact_bindings"] = bindings
    model_ir = ModelIR.model_validate(payload)

    with pytest.raises(ValueError, match=message):
        validate_model_fact_coverage(problem_spec, model_ir)
