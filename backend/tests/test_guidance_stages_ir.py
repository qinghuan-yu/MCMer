import asyncio
import json
from pathlib import Path


def _write_input_catalog(root: Path, question: str) -> None:
    (root / "input_catalog.json").write_text(json.dumps({
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
    }, ensure_ascii=False), encoding="utf-8")


class FakeDecomposer:
    async def decompose(
        self, *, question: str, evidence_profile: dict, input_catalog: dict, task: dict
    ):
        from app.guidance.contracts import ProblemSpec

        return ProblemSpec(
            task_summary=question,
            subproblems=[{
                "id": "problem_1",
                "objective": "fit branch flows",
                "required_fact_ids": ["fact.observations"],
                "required_uncertainty_ids": [],
                "depends_on_subproblem_ids": [],
            }],
            data_sources=[{
                "id": "observations",
                "description": "Uploaded branch-flow observations.",
                "source_ref": evidence_profile["evidence"][0]["source_ref"],
            }],
            locked_facts=[{
                "fact_id": "fact.observations",
                "statement": "The uploaded workbook contains branch-flow observations.",
                "source_refs": [evidence_profile["evidence"][0]["source_ref"]],
                "subproblem_ids": ["problem_1"],
                "semantic_key": "observations.source",
                "value": "observations",
                "unit": "",
            }],
        )


def test_decomposition_stage_saves_problem_spec_with_uploaded_files(tmp_path: Path) -> None:
    from app.guidance.stages import DecompositionStage

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.active.append(["x", "y"])
    workbook.save(data_dir / "attachment.xlsx")
    _write_input_catalog(tmp_path, "Fit branch flows.")
    output = asyncio.run(DecompositionStage(decomposer=FakeDecomposer()).run(
        task_id="task-fit",
        task={"work_dir": str(tmp_path), "question": "Fit branch flows."},
        input_path=None,
        output_path=tmp_path / "problem_spec.json",
    ))

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["subproblems"][0]["id"] == "problem1"
    assert payload["data_sources"] == [{
        "id": "observations",
        "description": "Uploaded branch-flow observations.",
        "source_ref": "data/attachment.xlsx",
    }]


def test_decomposition_stage_marks_referenced_but_unuploaded_appendices_missing(tmp_path: Path) -> None:
    from app.guidance.contracts import ProblemSpec
    from app.guidance.stages import DecompositionStage

    class MisleadingDecomposer:
        async def decompose(
            self, *, question: str, evidence_profile: dict, input_catalog: dict, task: dict
        ):
            return ProblemSpec(
                task_summary=question,
                subproblems=[
                    {
                        "id": "problem1",
                        "required_fact_ids": [
                            "fact.appendix2_load_model",
                            "fact.main_attachment_observation",
                        ],
                        "required_uncertainty_ids": ["missing_appendix2"],
                        "depends_on_subproblem_ids": [],
                    },
                    {
                        "id": "problem2",
                        "required_fact_ids": [
                            "fact.appendix3_wear_parameters",
                            "fact.main_attachment_observation",
                        ],
                        "required_uncertainty_ids": ["missing_appendix3"],
                        "depends_on_subproblem_ids": [],
                    },
                ],
                data_sources=[{
                    "id": "main_attachment",
                    "description": "该文件包含表1-4以及附录2和附录3。",
                    "source_ref": "data/A-附件.xlsx",
                }],
                locked_facts=[
                    {
                        "fact_id": "fact.appendix2_load_model",
                        "statement": "附录2提供刀盘载荷模型。",
                        "source_refs": ["inputs/problem.txt#paragraph-1"],
                        "subproblem_ids": ["problem1"],
                        "semantic_key": "load_model.source",
                        "value": "appendix2",
                        "unit": "",
                    },
                    {
                        "fact_id": "fact.appendix3_wear_parameters",
                        "statement": "附录3提供滚刀磨损参数。",
                        "source_refs": ["inputs/problem.txt#paragraph-1"],
                        "subproblem_ids": ["problem2"],
                        "semantic_key": "wear_parameters.source",
                        "value": "appendix3",
                        "unit": "",
                    },
                    {
                        "fact_id": "fact.main_attachment_observation",
                        "statement": "主附件提供预紧力观测。",
                        "source_refs": ["data/A-附件.xlsx"],
                        "subproblem_ids": ["problem1", "problem2"],
                        "semantic_key": "preload_observation.source",
                        "value": "main_attachment",
                        "unit": "",
                    },
                ],
                uncertainties=[
                    {
                        "uncertainty_id": "missing_appendix2",
                        "statement": "附录2未上传，刀盘载荷模型待确认。",
                        "source_refs": ["inputs/problem.txt#paragraph-1"],
                        "subproblem_ids": ["problem1"],
                        "semantic_key": "load_model.availability",
                        "candidate_values": ["available", "missing"],
                        "unit": "",
                    },
                    {
                        "uncertainty_id": "missing_appendix3",
                        "statement": "附录3未上传，滚刀磨损参数待确认。",
                        "source_refs": ["inputs/problem.txt#paragraph-1"],
                        "subproblem_ids": ["problem2"],
                        "semantic_key": "wear_parameters.availability",
                        "candidate_values": ["available", "missing"],
                        "unit": "",
                    },
                ],
            )

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.active.append(["载荷", "预紧力"])
    workbook.save(data_dir / "A-附件.xlsx")
    _write_input_catalog(
        tmp_path,
        "问题2基于附录2计算载荷；问题3根据附录3分析磨损。",
    )
    output = asyncio.run(DecompositionStage(decomposer=MisleadingDecomposer()).run(
        task_id="task-missing-appendices",
        task={
            "work_dir": str(tmp_path),
            "question": "问题2基于附录2计算载荷；问题3根据附录3分析磨损。",
        },
        input_path=None,
        output_path=tmp_path / "problem_spec.json",
    ))

    payload = json.loads(output.read_text(encoding="utf-8"))
    requirements = {item["reference"]: item for item in payload["source_requirements"]}
    assert requirements["附录2"]["status"] == "missing"
    assert requirements["附录3"]["status"] == "missing"
    assert all(
        "附录2" not in fact["statement"] and "附录3" not in fact["statement"]
        for fact in payload["locked_facts"]
    )
    assert payload["subproblems"][0]["required_fact_ids"] == [
        "fact.main_attachment_observation"
    ]
    assert payload["subproblems"][1]["required_fact_ids"] == [
        "fact.main_attachment_observation"
    ]
    assert payload["data_sources"][0]["id"] == "main_attachment"
    assert payload["data_sources"][0]["source_ref"] == (
        payload["locked_facts"][0]["source_refs"][0]
    )
    assert all(
        reference not in payload["data_sources"][0]["description"]
        for reference in requirements
    )
    assert any(
        "附录2" in item["statement"] and "未上传" in item["statement"]
        for item in payload["uncertainties"]
    )


def test_workbook_profile_normalizes_merged_groups_and_trailing_empty_columns(tmp_path: Path) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import PatternFill

    from app.artifacts.evidence_profile import _profile_workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "标准实验数据"
    sheet.append(["滚刀直径", "破岩力"])
    sheet.merge_cells("A2:A4")
    sheet["A2"] = 17
    sheet["B2"] = 101.0
    sheet["B3"] = 102.0
    sheet["B4"] = 103.0
    sheet["C1"].fill = PatternFill(fill_type="solid", fgColor="FFFFFF")
    workbook_path = tmp_path / "merged-groups.xlsx"
    workbook.save(workbook_path)

    profile = _profile_workbook(workbook_path)["sheets"][0]

    assert profile["effective_max_column"] == 2
    assert profile["ignored_trailing_empty_columns"] == 1
    assert profile["columns"] == ["滚刀直径", "破岩力"]
    assert profile["merged_ranges"] == ["A2:A4"]
    assert profile["forward_fill_columns"] == ["滚刀直径"]
    assert profile["sample_rows"] == [[17, 101.0], [17, 102.0], [17, 103.0]]


def test_review_positive_value_accepts_consistent_with_explanation() -> None:
    from app.guidance.stages import _is_positive_review_value

    assert _is_positive_review_value("consistent; units are defined and aligned")
