import json
from pathlib import Path

from app.artifacts.figure_plan import FigurePlanBuilder
from app.artifacts.registry import FigureBundle
from app.core.skills.renderer import RendererSkill
from app.core.skills.visualization_planner import VisualizationPlannerSkill
from app.core.skills.writer_context import WriterContextSkill
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


def test_result_overview_renders_single_scalar_verified_result(tmp_path: Path) -> None:
    report = FigureStage.render_required_requests_deterministically(
        work_dir=str(tmp_path),
        result_registry={
            "verified_results": [
                {"id": "q1_score", "value": 2.8, "source_data": ["result_registry.json"]},
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
                "linked_result_ids": ["q1_score"],
                "view_type": "numeric_overview",
            }
        ],
        chart_language="English",
    )

    assert report["created_images"]
    assert report["satisfied"]
    assert report["missing"] == []


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


def test_reevaluate_injects_result_overview_when_comparison_columns_missing(tmp_path: Path) -> None:
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

    reevaluated = FigurePlanBuilder.reevaluate_requests(
        requests=[
            {
                "id": "fig_q1_comparison_bar",
                "subproblem_id": "q1",
                "figure_kind": "result_figure",
                "semantic_role": "comparison_bar",
                "required": True,
                "required_data": ["data/summary.csv"],
                "required_columns": ["method", "metric", "value"],
                "linked_result_ids": ["q1_result"],
                "depends_on": {"data_files": ["data/summary.csv"], "result_ids": ["q1_result"]},
            }
        ],
        chart_language="Simplified Chinese",
        work_dir=str(tmp_path),
    )

    blocked_comparison = [
        item for item in reevaluated
        if item.get("semantic_role") == "comparison_bar"
    ][0]
    fallback = [
        item for item in reevaluated
        if item.get("semantic_role") == "result_overview"
    ]
    assert blocked_comparison["blocked_reason"] == "required_columns_missing:comparison_bar"
    assert fallback
    assert fallback[0]["required"] is True
    assert fallback[0]["linked_result_ids"] == ["q1_result"]


def test_reevaluate_injects_overview_when_result_section_does_not_match_subproblem(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "summary.csv").write_text(
        "week,bmi,zscore\n12,23.1,2.8\n13,24.0,3.1\n14,22.5,2.4\n",
        encoding="utf-8",
    )
    (tmp_path / "result_registry.json").write_text(
        json.dumps(
            {
                "verified_results": [
                    {"id": "global_result", "section": "summary", "value": 2.8}
                ],
                "blocked_results": [],
                "summary": {"verified_count": 1, "blocked_count": 0},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    reevaluated = FigurePlanBuilder.reevaluate_requests(
        requests=[
            {
                "id": "fig_q1_comparison_bar",
                "subproblem_id": "q1",
                "figure_kind": "result_figure",
                "semantic_role": "comparison_bar",
                "required": True,
                "required_data": ["data/summary.csv"],
                "required_columns": ["method", "metric", "value"],
                "linked_result_ids": ["global_result"],
                "depends_on": {"data_files": ["data/summary.csv"], "result_ids": ["global_result"]},
            }
        ],
        chart_language="Simplified Chinese",
        work_dir=str(tmp_path),
    )

    fallback = [
        item for item in reevaluated
        if item.get("semantic_role") == "result_overview"
    ]
    assert fallback
    assert fallback[0]["required"] is True
    assert fallback[0]["linked_result_ids"] == ["global_result"]


def test_reevaluate_does_not_create_unbound_overview_when_verified_results_exist(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "summary.csv").write_text(
        "week,bmi,zscore\n12,23.1,2.8\n13,24.0,3.1\n14,22.5,2.4\n",
        encoding="utf-8",
    )
    (tmp_path / "result_registry.json").write_text(
        json.dumps(
            {
                "verified_results": [
                    {"id": "global_result", "section": "summary", "value": 2.8}
                ],
                "blocked_results": [],
                "summary": {"verified_count": 1, "blocked_count": 0},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    requests = []
    for idx in range(1, 4):
        requests.append(
            {
                "id": f"fig_q{idx}_comparison_bar",
                "subproblem_id": f"q{idx}",
                "figure_kind": "result_figure",
                "semantic_role": "comparison_bar",
                "required": True,
                "required_data": ["data/summary.csv"],
                "required_columns": ["method", "metric", "value"],
                "linked_result_ids": ["global_result"],
                "depends_on": {"data_files": ["data/summary.csv"], "result_ids": ["global_result"]},
            }
        )

    reevaluated = FigurePlanBuilder.reevaluate_requests(
        requests=requests,
        chart_language="Simplified Chinese",
        work_dir=str(tmp_path),
    )

    fallback = [
        item for item in reevaluated
        if item.get("semantic_role") == "result_overview"
    ]
    assert len(fallback) == 1
    assert fallback[0]["linked_result_ids"] == ["global_result"]
    assert all(item.get("linked_result_ids") for item in fallback)


def test_writer_context_skill_serializes_canonical_result_overview(tmp_path: Path) -> None:
    bundle = FigureBundle(
        available_figures=[
            {
                "path": "output/fig_q1_result_overview.png",
                "caption": "Core result overview",
                "canonical_caption": "Core result overview",
                "figure_request_id": "fig_q1_result_overview",
                "semantic_role": "result_overview",
                "figure_kind": "result_figure",
                "view_type": "numeric_overview",
                "linked_result_ids": ["q1_result"],
                "source_data": ["result_registry.json"],
                "semantic_verified": True,
                "chart_language_verified": True,
                "paper_ready": True,
            }
        ],
        result_figures=[
            {
                "path": "output/fig_q1_result_overview.png",
                "caption": "Core result overview",
                "canonical_caption": "Core result overview",
                "figure_request_id": "fig_q1_result_overview",
                "semantic_role": "result_overview",
                "figure_kind": "result_figure",
                "view_type": "numeric_overview",
                "linked_result_ids": ["q1_result"],
                "source_data": ["result_registry.json"],
                "semantic_verified": True,
                "chart_language_verified": True,
                "paper_ready": True,
            }
        ],
        required_images=["output/fig_q1_result_overview.png"],
    )
    result_registry = {
        "verified_results": [{"id": "q1_result", "value": 2.8}],
        "blocked_results": [],
    }

    context = WriterContextSkill.build_context(
        result_registry=result_registry,
        figure_bundle=bundle,
        chart_language="English",
    )
    path = context.save(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["available_figures"][0]["semantic_role"] == "result_overview"
    assert payload["available_figures"][0]["caption"] == "Core result overview"
    assert payload["available_figures"][0]["linked_result_ids"] == ["q1_result"]
    assert payload["rules"]["writer_must_use_canonical_caption"] is True
    assert "comparison_bar" in payload["rules"]["do_not_relabel_result_overview"]


def test_visualization_planner_uses_verified_result_contract_views(tmp_path: Path) -> None:
    (tmp_path / "result_registry.json").write_text(
        json.dumps(
            {
                "verified_results": [
                    {
                        "id": "q1_fit",
                        "section": "q1",
                        "value": 2.8,
                        "result_type": "regression_model",
                        "source_data": ["result_registry.json"],
                        "visualization_contract": {
                            "result_id": "q1_fit",
                            "required_views": ["fitting_curve"],
                            "fallback_views": ["result_overview"],
                        },
                    }
                ],
                "blocked_results": [],
                "summary": {"verified_count": 1, "blocked_count": 0},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    registry = json.loads((tmp_path / "result_registry.json").read_text(encoding="utf-8"))

    plan = VisualizationPlannerSkill.build_figure_plan(
        result_registry=registry,
        chart_language="English",
        work_dir=str(tmp_path),
        solve_spec={"requires_result_figures": True},
    )

    roles = [item.get("semantic_role") for item in plan["figure_requests"]]
    assert "fitting_curve" in roles
    assert "result_overview" in roles
    overview = [item for item in plan["figure_requests"] if item.get("semantic_role") == "result_overview"][0]
    assert overview["linked_result_ids"] == ["q1_fit"]
    assert plan["planner"] == "VisualizationPlannerSkill"
    assert plan["source"] == "result_registry"


def test_visualization_planner_downgrades_missing_columns_but_keeps_overview(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "summary.csv").write_text(
        "week,bmi,zscore\n12,23.1,2.8\n13,24.0,3.1\n",
        encoding="utf-8",
    )
    registry = {
        "verified_results": [
            {
                "id": "q1_comparison",
                "section": "q1",
                "value": 2.8,
                "result_type": "unknown",
                "source_data": ["data/summary.csv"],
                "visualization_contract": {
                    "result_id": "q1_comparison",
                    "required_views": ["comparison_bar"],
                    "fallback_views": ["result_overview"],
                },
            }
        ],
        "blocked_results": [],
        "summary": {"verified_count": 1, "blocked_count": 0},
    }
    (tmp_path / "result_registry.json").write_text(json.dumps(registry), encoding="utf-8")

    plan = VisualizationPlannerSkill.build_figure_plan(
        result_registry=registry,
        chart_language="English",
        work_dir=str(tmp_path),
        solve_spec={"requires_result_figures": True},
    )

    blocked = [item for item in plan["figure_requests"] if item.get("semantic_role") == "comparison_bar"][0]
    overview = [item for item in plan["figure_requests"] if item.get("semantic_role") == "result_overview"][0]
    assert blocked["status"] == "blocked"
    assert blocked["blocked_reason"] == "required_columns_missing:comparison_bar"
    assert overview["required"] is True
    assert overview["linked_result_ids"] == ["q1_comparison"]


def test_renderer_skill_facade_renders_result_overview(tmp_path: Path) -> None:
    result = RendererSkill.render_required_requests(
        work_dir=str(tmp_path),
        result_registry={
            "verified_results": [
                {"id": "q1_score", "value": 2.8, "source_data": ["result_registry.json"]},
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
                "linked_result_ids": ["q1_score"],
                "view_type": "numeric_overview",
            }
        ],
        chart_language="English",
    )

    payload = result.to_dict()
    assert payload["renderer"] == "RendererSkill"
    assert payload["created_images"]
    assert payload["satisfied"]
    assert payload["missing"] == []


def test_renderer_skill_result_overview_accepts_single_verified_scalar(tmp_path: Path) -> None:
    result = RendererSkill.render_required_requests(
        work_dir=str(tmp_path),
        result_registry={
            "verified_results": [
                {"id": "single_scalar", "value": "score = 7.5", "source_data": ["result_registry.json"]},
            ]
        },
        required_requests=[
            {
                "id": "fig_scalar_result_overview",
                "subproblem_id": "q1",
                "required": True,
                "figure_kind": "result_figure",
                "semantic_role": "result_overview",
                "required_data": ["result_registry.json"],
                "linked_result_ids": ["single_scalar"],
                "view_type": "numeric_overview",
            }
        ],
        chart_language="English",
    )

    assert result.created_images
    assert result.satisfied
    assert result.missing == []


def test_renderer_skill_renders_comparison_bar(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "comparison.csv").write_text(
        "method,metric,value\nA,risk,1.2\nB,risk,0.8\nC,risk,1.5\n",
        encoding="utf-8",
    )

    result = RendererSkill.render_required_requests(
        work_dir=str(tmp_path),
        result_registry={
            "verified_results": [
                {"id": "q1_comparison", "value": 0.8, "source_data": ["data/comparison.csv"]},
            ]
        },
        required_requests=[
            {
                "id": "fig_q1_comparison_bar",
                "subproblem_id": "q1",
                "required": True,
                "figure_kind": "result_figure",
                "semantic_role": "comparison_bar",
                "required_data": ["data/comparison.csv"],
                "required_columns": ["method", "metric", "value"],
                "linked_result_ids": ["q1_comparison"],
                "view_type": "comparison_bar",
                "expected_content": ["Comparison bar"],
            }
        ],
        chart_language="English",
    )

    assert result.created_images == ["output/fig_q1_comparison_bar.png"]
    assert result.satisfied == [{"figure_request_id": "fig_q1_comparison_bar", "required": True, "satisfied": True}]
    assert result.missing == []


def test_renderer_skill_renders_fitting_curve_and_residual_plot(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "fit.csv").write_text(
        "x,observed,fitted,residual\n1,1.1,1.0,0.1\n2,1.9,2.0,-0.1\n3,3.2,3.0,0.2\n",
        encoding="utf-8",
    )

    result = RendererSkill.render_required_requests(
        work_dir=str(tmp_path),
        result_registry={
            "verified_results": [
                {"id": "q1_fit", "value": 0.95, "source_data": ["data/fit.csv"]},
            ]
        },
        required_requests=[
            {
                "id": "fig_q1_fitting_curve",
                "subproblem_id": "q1",
                "required": True,
                "figure_kind": "result_figure",
                "semantic_role": "fitting_curve",
                "required_data": ["data/fit.csv"],
                "linked_result_ids": ["q1_fit"],
                "view_type": "fitting_curve",
                "expected_content": ["Fitting curve"],
            },
            {
                "id": "fig_q1_residual_plot",
                "subproblem_id": "q1",
                "required": True,
                "figure_kind": "result_figure",
                "semantic_role": "residual_plot",
                "required_data": ["data/fit.csv"],
                "linked_result_ids": ["q1_fit"],
                "view_type": "residual_plot",
                "expected_content": ["Residual plot"],
            },
        ],
        chart_language="English",
    )

    assert result.created_images == [
        "output/fig_q1_fitting_curve.png",
        "output/fig_q1_residual_plot.png",
    ]
    assert len(result.satisfied) == 2
    assert result.missing == []


def test_renderer_skill_renders_spectrum_curve(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "spectrum.csv").write_text(
        "wavenumber,reflectance\n1000,0.20\n1100,0.25\n1200,0.21\n",
        encoding="utf-8",
    )

    result = RendererSkill.render_required_requests(
        work_dir=str(tmp_path),
        result_registry={
            "verified_results": [
                {"id": "q1_spectrum", "value": 0.25, "source_data": ["data/spectrum.csv"]},
            ]
        },
        required_requests=[
            {
                "id": "fig_q1_spectrum_curve",
                "subproblem_id": "q1",
                "required": True,
                "figure_kind": "result_figure",
                "semantic_role": "spectrum_curve",
                "required_data": ["data/spectrum.csv"],
                "linked_result_ids": ["q1_spectrum"],
                "view_type": "spectrum_curve",
                "expected_content": ["Spectrum curve"],
            }
        ],
        chart_language="English",
    )

    assert result.created_images == ["output/fig_q1_spectrum_curve.png"]
    assert result.satisfied == [{"figure_request_id": "fig_q1_spectrum_curve", "required": True, "satisfied": True}]
    assert result.missing == []
