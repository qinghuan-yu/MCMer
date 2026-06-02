from pathlib import Path

from app.artifacts.answer_plan import AnswerPlanBuilder
from app.artifacts.diagnostics import build_verified_output_bundle


def test_answer_plan_marks_subproblem_slots_covered() -> None:
    plan = AnswerPlanBuilder.evaluate(
        solve_spec={
            "subproblems": [
                {
                    "id": "q1",
                    "objective": "calculate the fitted parameter values",
                    "expected_outputs": ["parameter table"],
                },
                {
                    "id": "q2",
                    "objective": "predict traffic flow and fill the requested table",
                },
            ]
        },
        result_registry={
            "verified_results": [
                {"id": "q1_parameters", "section": "q1", "value": 1.2},
                {"id": "q2_answer_table", "section": "q2", "value": "table filled"},
            ],
            "blocked_results": [],
        },
    )

    assert plan["coverage_status"] == "ready"
    assert plan["slot_count"] == 2
    assert plan["covered_count"] == 2
    assert [slot["status"] for slot in plan["slots"]] == ["covered", "covered"]
    assert plan["slots"][1]["requires_table_answer"] is True


def test_answer_plan_reports_missing_and_blocked_slots() -> None:
    plan = AnswerPlanBuilder.evaluate(
        solve_spec={
            "subproblems": [
                {"id": "q1", "objective": "calculate model parameters"},
                {"id": "q2", "objective": "fill final answer table"},
                {"id": "q3", "objective": "perform sensitivity analysis"},
            ]
        },
        result_registry={
            "verified_results": [{"id": "q1_parameters", "section": "q1", "value": 1.2}],
            "blocked_results": [{"id": "q2_blocked", "section": "q2", "status": "blocked"}],
        },
    )

    assert plan["coverage_status"] == "partial"
    assert plan["covered_count"] == 1
    assert plan["blocked_count"] == 1
    assert plan["missing_count"] == 1
    assert [slot["status"] for slot in plan["slots"]] == ["covered", "blocked", "missing"]


def test_verified_output_bundle_requires_answer_gate_for_actual_test(tmp_path: Path) -> None:
    paper = tmp_path / "paper.md"
    docx = tmp_path / "paper.docx"
    paper.write_text("# Paper\n\nNo images required.\n", encoding="utf-8")
    docx.write_bytes(b"not-a-real-docx")

    bundle = build_verified_output_bundle(
        status="passed",
        work_dir=tmp_path,
        task_id="task-1",
        task_type="writing",
        paper_path=str(paper),
        docx_path=str(docx),
        result_payload={
            "result_coverage": {"verified_count": 1, "blocked_count": 0},
            "stages": {
                "result_registry": {
                    "verified_results": [{"id": "q1_result", "section": "q1", "value": 1}],
                    "blocked_results": [],
                },
                "answer_plan": {
                    "coverage_status": "missing",
                    "slot_count": 2,
                    "covered_count": 1,
                    "blocked_count": 0,
                    "missing_count": 1,
                    "slots": [{"id": "q2_answer", "status": "missing"}],
                },
            },
        },
    )

    assert bundle["answer_gate"]["passed"] is False
    assert bundle["answer_gate"]["missing_count"] == 1
    assert bundle["ready_for_actual_test"] is False

    ready_bundle = build_verified_output_bundle(
        status="passed",
        work_dir=tmp_path,
        task_id="task-1",
        task_type="writing",
        paper_path=str(paper),
        docx_path=str(docx),
        result_payload={
            "result_coverage": {"verified_count": 1, "blocked_count": 0},
            "stages": {
                "answer_plan": {
                    "coverage_status": "ready",
                    "slot_count": 1,
                    "covered_count": 1,
                    "blocked_count": 0,
                    "missing_count": 0,
                },
            },
        },
    )

    assert ready_bundle["answer_gate"]["passed"] is True
    assert ready_bundle["ready_for_actual_test"] is True
