import json
from pathlib import Path

from app.artifacts.figure_plan import FigurePlanBuilder
from app.core.figure_stage import FigureStage


def test_result_overview_fallback_is_required_and_canonical(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "summary.csv").write_text(
        "week,bmi,zscore\n12,23.1,2.8\n13,24.0,3.1\n14,22.5,2.4\n",
        encoding="utf-8",
    )
    (tmp_path / "result_registry.json").write_text(
        json.dumps(
            {
                "verified_results": [{"id": "q1_result", "value": 2.8}],
                "blocked_results": [],
                "summary": {"verified_count": 1, "blocked_count": 0},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    plan = FigurePlanBuilder.build(
        question="modeling task",
        solve_spec={
            "requires_figures": True,
            "requires_result_figures": True,
            "subproblems": [
                {
                    "id": "q1",
                    "objective": "summarize verified results",
                    "input_files": ["data/summary.csv"],
                }
            ],
            "figure_requests": [],
        },
        problem_facts={},
        chart_language="Simplified Chinese",
        work_dir=str(tmp_path),
    )

    overview = [
        item for item in plan["figure_requests"]
        if item.get("semantic_role") == "result_overview"
    ]
    assert overview
    assert overview[0]["required"] is True
    assert overview[0]["figure_kind"] == "result_figure"
    assert overview[0]["linked_result_ids"] == ["q1_result"]
    assert overview[0]["canonical_caption"] == "核心结果概览图"


def test_result_overview_renders_from_verified_results_without_csv(tmp_path: Path) -> None:
    report = FigureStage.render_required_requests_deterministically(
        work_dir=str(tmp_path),
        result_registry={
            "verified_results": [
                {"id": "r1", "value": 1.2, "source_data": ["result_registry.json"]},
                {"id": "r2", "value": 2.4, "source_data": ["result_registry.json"]},
                {"id": "r3", "value": 3.6, "source_data": ["result_registry.json"]},
            ]
        },
        required_requests=[
            {
                "id": "fig_q1_result_overview",
                "subproblem_id": "q1",
                "required": True,
                "figure_kind": "result_figure",
                "semantic_role": "result_overview",
                "required_data": ["result_registry.json"],
                "linked_result_ids": ["r1", "r2", "r3"],
                "view_type": "numeric_overview",
            }
        ],
        chart_language="English",
    )

    assert report["created_images"]
    assert report["satisfied"]


def test_result_overview_rejects_unbound_overview(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "summary.csv").write_text(
        "week,bmi,zscore\n12,23.1,2.8\n13,24.0,3.1\n14,22.5,2.4\n",
        encoding="utf-8",
    )

    report = FigureStage.render_required_requests_deterministically(
        work_dir=str(tmp_path),
        result_registry={"verified_results": []},
        required_requests=[
            {
                "id": "fig_q1_result_overview",
                "subproblem_id": "q1",
                "required": True,
                "figure_kind": "result_figure",
                "semantic_role": "result_overview",
                "required_data": ["data/summary.csv"],
                "view_type": "numeric_overview",
            }
        ],
        chart_language="English",
    )

    assert report["created_images"] == []
    assert report["missing"][0]["reason"] == "missing_verified_result_binding"
