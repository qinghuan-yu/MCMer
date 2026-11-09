import asyncio
import json
from pathlib import Path

import pytest
from docx import Document


def _embedded_attachment_problem(path: Path) -> None:
    document = Document()
    document.add_paragraph("求解多期采购问题。")
    document.add_paragraph("附件1 血管机器人相关成本")
    cost = document.add_table(rows=1, cols=2)
    cost.rows[0].cells[0].text = "类别"
    cost.rows[0].cells[1].text = "价格"
    for name, value in [("容器艇", "200"), ("操作手", "100")]:
        cells = cost.add_row().cells
        cells[0].text = name
        cells[1].text = value
    document.add_paragraph("附件2 第1-4周使用数量")
    demand = document.add_table(rows=1, cols=4)
    for index, value in enumerate([11, 5, 4, 7]):
        demand.rows[0].cells[index].text = str(value)
    document.save(path)


class EmbeddedEvidenceDecomposer:
    async def decompose(
        self, *, question: str, evidence_profile: dict, input_catalog: dict, task: dict
    ):
        from app.guidance.contracts import ProblemSpec

        table_refs = [
            block["source_ref"]
            for block in input_catalog["blocks"]
            if block["kind"] == "table"
        ]

        return ProblemSpec(
            task_summary="多期采购与需求规划。",
            subproblems=[{
                "id": "problem1",
                "objective": "最小化总成本",
                "required_fact_ids": ["fact.cost_table", "fact.demand_table"],
                "required_uncertainty_ids": [],
                "depends_on_subproblem_ids": [],
            }],
            data_sources=[
                {
                    "id": "attachment1",
                    "description": "成本表",
                    "source_ref": table_refs[0],
                },
                {
                    "id": "attachment2",
                    "description": "逐周需求表",
                    "source_ref": table_refs[1],
                },
            ],
            locked_facts=[
                {
                    "fact_id": "fact.cost_table",
                    "statement": "附件1给出成本。",
                    "source_refs": [table_refs[0]],
                    "subproblem_ids": ["problem1"],
                    "semantic_key": "cost.source",
                    "value": "attachment1",
                    "unit": "",
                },
                {
                    "fact_id": "fact.demand_table",
                    "statement": "附件2给出逐周需求。",
                    "source_refs": [table_refs[1]],
                    "subproblem_ids": ["problem1"],
                    "semantic_key": "demand.source",
                    "value": "attachment2",
                    "unit": "",
                },
            ],
        )


class MissingAvailableEvidenceModeler:
    async def model(self, *, problem_spec, evidence_profile, task, work_dir):
        from app.guidance.model_ir import ModelIR

        return ModelIR.model_validate({
            "model_id": "contradictory-evidence-model",
            "subproblems": [{
                "execution_status": "blocked",
                "subproblem_id": "problem1",
                "objective": "最小化总成本",
                "blocking_reason": "附件1未提供。",
                "missing_inputs": ["附件1"],
                "recovery_action": "重新上传附件1。",
                "expected_outputs": ["最优成本"],
            }],
        })


def test_docx_embedded_tables_become_ordered_available_evidence(tmp_path: Path) -> None:
    from app.guidance.stages import DecompositionStage
    from app.services.source_ingest import ingest_question_source

    source_dir = tmp_path / "inputs" / "question"
    source_dir.mkdir(parents=True)
    source = source_dir / "problem.docx"
    _embedded_attachment_problem(source)
    task = {
        "task_id": "task-embedded-docx",
        "work_dir": str(tmp_path),
        "question": "",
        "source_question": "",
        "source_question_file": "",
        "source_question_text": "",
    }

    task.update(ingest_question_source(str(source), task))

    assert task["input_catalog_ref"] == "input_catalog.json"
    catalog = json.loads((tmp_path / "input_catalog.json").read_text(encoding="utf-8"))
    assert [block["kind"] for block in catalog["blocks"]] == [
        "paragraph",
        "paragraph",
        "table",
        "paragraph",
        "table",
    ]
    tables = [block for block in catalog["blocks"] if block["kind"] == "table"]
    assert tables[0]["source_ref"].endswith("inputs/question/problem.docx#table-1")
    assert tables[0]["rows"] == [
        ["类别", "价格"],
        ["容器艇", "200"],
        ["操作手", "100"],
    ]
    assert tables[1]["source_ref"].endswith("inputs/question/problem.docx#table-2")

    output = asyncio.run(DecompositionStage(decomposer=EmbeddedEvidenceDecomposer()).run(
        task_id="task-embedded-docx",
        task=task,
        input_path=None,
        output_path=tmp_path / "problem_spec.json",
    ))

    profile = json.loads((tmp_path / "evidence_profile.json").read_text(encoding="utf-8"))
    embedded = [item for item in profile["evidence"] if item["kind"] == "embedded_table"]
    assert [item["labels"] for item in embedded] == [["附件1"], ["附件2"]]
    assert all(len(item["sha256"]) == 64 for item in embedded)
    requirements = {
        item["reference"]: item
        for item in json.loads(output.read_text(encoding="utf-8"))["source_requirements"]
    }
    assert requirements["附件1"]["status"] == "available"
    assert requirements["附件2"]["status"] == "available"


def test_modeling_rejects_missing_claim_for_available_evidence(tmp_path: Path) -> None:
    from app.artifacts.evidence_profile import build_evidence_profile
    from app.artifacts.input_catalog import build_input_catalog
    from app.guidance.contracts import ProblemSpec
    from app.guidance.stages import ModelingStage
    from source_preflight_fixtures import write_source_preflight

    (tmp_path / "problem_spec.json").write_text(json.dumps({
        "task_summary": "附件1已提供成本表。",
        "subproblems": [{
            "id": "problem1",
            "objective": "最小化总成本",
            "required_fact_ids": ["fact.cost_table"],
            "required_uncertainty_ids": [],
            "depends_on_subproblem_ids": [],
        }],
        "data_sources": [{
            "id": "attachment1",
            "description": "附件1成本表",
            "source_ref": "inputs/question/problem.docx#table-1",
        }],
        "locked_facts": [{
            "fact_id": "fact.cost_table",
            "statement": "附件1给出成本表。",
            "source_refs": ["inputs/question/problem.docx#table-1"],
            "subproblem_ids": ["problem1"],
            "semantic_key": "cost.source",
            "value": "attachment1",
            "unit": "",
        }],
        "source_requirements": [{
            "reference": "附件1",
            "status": "available",
            "matched_files": ["inputs/question/problem.docx#table-1"],
            "note": "Matched embedded table evidence.",
        }],
    }, ensure_ascii=False), encoding="utf-8")
    source = tmp_path / "inputs/question/problem.docx"
    source.parent.mkdir(parents=True)
    _embedded_attachment_problem(source)
    build_input_catalog(source, work_dir=tmp_path)
    profile = build_evidence_profile(tmp_path).model_dump(mode="json")
    spec = ProblemSpec.model_validate_json((tmp_path / "problem_spec.json").read_text(encoding="utf-8"))
    write_source_preflight(tmp_path, spec, profile=profile)

    with pytest.raises(ValueError, match="available evidence.*附件1"):
        asyncio.run(ModelingStage(modeler=MissingAvailableEvidenceModeler()).run(
            task_id="task-evidence-conflict",
            task={"work_dir": str(tmp_path)},
            input_path=tmp_path / "problem_spec.json",
            output_path=tmp_path / "model_ir.json",
        ))
