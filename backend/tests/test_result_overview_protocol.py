import asyncio
import json
from pathlib import Path

from app.artifacts.figure_plan import FigurePlanBuilder
from app.artifacts.diagnostics import WorkflowTrace
from app.artifacts.registry import FigureBundle
from app.core.skills.renderer import RendererSkill
from app.core.skills.visualization_planner import VisualizationPlannerSkill
from app.core.skills.writer_context import WriterContextSkill
from app.core.stages.figure_gate_stage import FigureGateStage
from app.core.stages.figure_recovery_stage import FigureRecoveryStage
from app.core.stages.finalizer_stage import FinalizerStage
from app.core.stages.quality_gate_stage import QualityGateStage
from app.core.stages.writer_stage import WriterStage
from app.core.figure_stage import FigureStage
from app.artifacts.visualization_schema import supported_views_for_result_type


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
    assert FigureStage.coerce_numeric_value("f1(t) = 8.0000") == 8.0
    assert FigureStage.coerce_numeric_value("Flow=66.35") == 66.35

    columns, linked_ids, sources = FigureStage.deterministic_overview_columns_from_registry(
        {
            "verified_results": [
                {"id": "r1", "name": "First metric", "value": 1.2, "source_data": ["result_registry.json"]},
                {"id": "r2", "name": "Second metric", "value": 2.4, "source_data": ["result_registry.json"]},
                {"id": "r3", "name": "Third metric", "value": 3.6, "source_data": ["result_registry.json"]},
                {"id": "summary_result_table", "name": "Table summary", "value": "A | B\n1 | 2"},
            ]
        }
    )

    assert columns == {
        "First metric": [1.2],
        "Second metric": [2.4],
        "Third metric": [3.6],
    }
    assert "result_index" not in columns
    assert linked_ids == ["r1", "r2", "r3"]
    assert sources == ["result_registry.json"]

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


def test_visualization_planner_limits_overview_fallback_to_one(tmp_path: Path) -> None:
    registry = {
        "verified_results": [
            {"id": "q1", "section": "q1", "value": 1.0},
            {"id": "q2", "section": "q2", "value": 2.0},
            {"id": "q3", "section": "q3", "value": 3.0},
        ],
        "blocked_results": [],
    }

    plan = VisualizationPlannerSkill.build_figure_plan(
        result_registry=registry,
        chart_language="English",
        work_dir=str(tmp_path),
        solve_spec={"requires_result_figures": True},
    )

    overview = [
        item for item in plan["figure_requests"]
        if item.get("semantic_role") == "result_overview"
    ]
    assert len(overview) == 1
    assert overview[0]["id"] == "fig_result_overview"
    assert overview[0]["linked_result_ids"] == ["q1", "q2", "q3"]


def test_visualization_planner_infers_spectrum_view_from_verified_results(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "spectrum.csv").write_text(
        "wavenumber,reflectance\n400,0.13\n500,0.42\n600,0.31\n",
        encoding="utf-8",
    )
    registry = {
        "verified_results": [
            {
                "id": "q1_calculated_thickness",
                "section": "q1",
                "name": "Calculated thickness",
                "value": "121.69",
                "source_data": ["data/spectrum.csv"],
                "visualization_contract": {
                    "result_id": "q1_calculated_thickness",
                    "required_views": ["spectrum_curve"],
                    "fallback_views": ["result_overview"],
                    "source_data": ["data/spectrum.csv"],
                },
            }
        ],
        "blocked_results": [],
    }

    plan = VisualizationPlannerSkill.build_figure_plan(
        result_registry=registry,
        chart_language="English",
        work_dir=str(tmp_path),
        solve_spec={"requires_result_figures": True},
    )

    roles = [item.get("semantic_role") for item in plan["figure_requests"]]
    spectrum = [
        item for item in plan["figure_requests"]
        if item.get("semantic_role") == "spectrum_curve"
    ]
    assert "spectrum_curve" in roles
    assert spectrum[0]["required"] is True
    assert spectrum[0]["linked_result_ids"] == ["q1_calculated_thickness"]
    assert spectrum[0]["required_data"] == ["data/spectrum.csv"]
    assert any(item.get("semantic_role") == "result_overview" for item in plan["figure_requests"])


def test_writer_context_skill_serializes_canonical_result_overview(tmp_path: Path) -> None:
    bundle = FigureBundle(
        available_figures=[
            {
                "path": "output/fig_q1_result_overview.png",
                "caption": "Residual plot",
                "canonical_caption": "Residual plot",
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
                "caption": "Residual plot",
                "canonical_caption": "Residual plot",
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
        diagnostic_figures=[
            {
                "path": "output/workflow_debug.png",
                "caption": "Workflow debug",
                "semantic_role": "workflow",
                "paper_ready": False,
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
    assert payload["allowed_image_paths"] == ["output/fig_q1_result_overview.png"]
    assert payload["forbidden_image_paths"] == ["output/workflow_debug.png"]
    assert payload["rules"]["writer_must_use_canonical_caption"] is True
    assert "data_overview" in payload["rules"]["result_overview_aliases"]
    assert "comparison_bar" in payload["rules"]["do_not_relabel_result_overview"]


def test_writer_context_normalizes_legacy_figure_artifact_bindings(tmp_path: Path) -> None:
    bundle = FigureBundle(
        available_figures=[
            {
                "path": "output/fig_q1_fit.png",
                "caption": "Fit",
                "canonical_caption": "Model fitting curve",
                "figure_request_id": "fig_q1_fit",
                "semantic_role": "fitting_curve",
                "figure_kind": "result_figure",
                "view_type": "fitting_curve",
                "linked_result_ids": ["q1_fit"],
                "data_bindings": ["data/fit.csv"],
                "semantic_verified": True,
                "chart_language_verified": True,
                "paper_ready": True,
            }
        ],
        result_figures=[],
        diagnostic_figures=[],
    )

    context = WriterContextSkill.build_context(
        result_registry={"verified_results": [{"id": "q1_fit"}], "blocked_results": []},
        figure_bundle=bundle,
        chart_language="English",
    )
    payload = context.to_dict()

    figure = payload["available_figures"][0]
    assert figure["caption"] == "Model fitting curve"
    assert figure["source_data"] == ["data/fit.csv"]
    assert figure["linked_result_ids"] == ["q1_fit"]


def test_writer_context_frontloads_coverage_diagnostics_for_partial_results() -> None:
    payload = WriterContextSkill.build(
        result_registry={
            "verified_results": [
                {
                    "id": "r_salvaged",
                    "confidence_level": "salvaged_pending_review",
                    "warnings": ["artifact_scalar_salvage_requires_formula_and_unit_verification"],
                }
            ],
            "blocked_results": [
                {
                    "id": "p3_blocked",
                    "section": "Problem 3",
                    "status": "blocked",
                    "warnings": ["tool budget exceeded"],
                }
            ],
            "summary": {
                "verified_count": 1,
                "blocked_count": 1,
                "coverage_status": "partial",
                "top_blocked_reasons": ["tool budget exceeded"],
            },
        },
        figure_bundle=FigureBundle(),
        chart_language="English",
    )

    assert list(payload.keys())[:2] == ["coverage_diagnostics", "rules"]
    diagnostics = payload["coverage_diagnostics"]
    assert diagnostics["coverage_status"] == "partial"
    assert diagnostics["must_disclose_partial"] is True
    assert diagnostics["provisional_verified_count"] == 1
    assert diagnostics["top_blocked_sections"] == [{"section": "Problem 3", "count": 1}]
    assert payload["rules"]["writer_must_disclose_partial_coverage"] is True


def test_writer_stage_prepares_context_as_only_image_whitelist(tmp_path: Path) -> None:
    bundle = FigureBundle(
        available_figures=[
            {
                "path": "output/fig_q1_result_overview.png",
                "figure_request_id": "fig_q1_result_overview",
                "semantic_role": "data_overview",
                "figure_kind": "result_figure",
                "linked_result_ids": ["q1_result"],
                "source_data": ["result_registry.json"],
                "semantic_verified": True,
                "chart_language_verified": True,
                "paper_ready": True,
            }
        ],
        diagnostic_figures=[{"path": "output/workflow_debug.png", "semantic_role": "workflow"}],
    )

    snapshot = WriterStage.prepare_context(
        work_dir=tmp_path,
        result_registry={"verified_results": [{"id": "q1_result"}], "blocked_results": []},
        figure_bundle=bundle,
        chart_language="Simplified Chinese",
    )

    assert snapshot.allowed_images == ["output/fig_q1_result_overview.png"]
    assert Path(snapshot.path).name == "writer_context.json"
    assert snapshot.payload["available_figures"][0]["semantic_role"] == "result_overview"
    assert snapshot.payload["forbidden_image_paths"] == ["output/workflow_debug.png"]
    assert "writer_context.allowed_image_paths" in WriterStage.context_contract_text()
    assert "forbidden_image_paths" in WriterStage.context_contract_text()
    assert "coverage_diagnostics.must_disclose_partial" in WriterStage.context_contract_text()


def test_figure_gate_stage_builds_bundle_snapshot(monkeypatch) -> None:
    bundle = FigureBundle(available_figures=[{"path": "output/fig.png"}])

    class FakeRegistry:
        def validate_for_paper(self):
            return ["output/fig.png"]

    monkeypatch.setattr(
        "app.core.stages.figure_gate_stage.LegacyFigureStage.build_bundle_snapshot",
        lambda registry: {
            "figure_bundle": bundle,
            "figure_bundle_dict": bundle.to_dict(),
            "writer_images": ["output/fig.png"],
        },
    )

    snapshot = FigureGateStage.from_registry(FakeRegistry())

    assert snapshot.artifact_valid_images == ["output/fig.png"]
    assert snapshot.figure_bundle is bundle
    assert snapshot.writer_images == ["output/fig.png"]


def test_artifact_registry_marks_solve_spec_figure_requests_fallback(tmp_path: Path) -> None:
    from app.artifacts.registry import ArtifactRegistry

    (tmp_path / "solve_spec.json").write_text(
        json.dumps(
            {
                "figure_requests": [
                    {
                        "id": "fig_legacy",
                        "required": True,
                        "semantic_role": "result_overview",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    registry = ArtifactRegistry.load(str(tmp_path), structured_result_files=[])
    requests = registry._load_figure_requests()

    assert requests[0]["id"] == "fig_legacy"
    assert registry.protocol_warnings
    assert registry.protocol_warnings[0]["code"] == "DEPRECATED_SOLVE_SPEC_FIGURE_REQUESTS_FALLBACK"


def test_quality_gate_stage_writes_reports_and_trace(tmp_path: Path) -> None:
    class FakeRegistry:
        figure_rejections = []

        def rejection_summary(self):
            return []

    trace = WorkflowTrace()
    bundle = FigureBundle(available_figures=[{"path": "output/fig.png"}])
    snapshot = QualityGateStage.evaluate(
        work_dir=tmp_path,
        result_registry={"verified_results": [{"id": "q1"}], "blocked_results": []},
        figure_bundle=bundle,
        artifact_registry=FakeRegistry(),
        artifact_valid_images=["output/fig.png"],
        writer_images=["output/fig.png"],
        solve_spec={"requires_figures": False},
        figure_plan_payload={},
        workflow_mode="standard",
        expected_figures=False,
        trace=trace,
    )

    assert snapshot.passed is True
    assert (tmp_path / "figure_artifact_gate_report.json").exists()
    assert (tmp_path / "quality_gate_report.json").exists()
    assert trace.stages and trace.stages[-1]["name"] == "figure_gate"


def test_quality_gate_prefers_figure_plan_requirements_over_solve_spec(tmp_path: Path) -> None:
    class FakeRegistry:
        figure_rejections = []

        def rejection_summary(self):
            return []

    trace = WorkflowTrace()
    bundle = FigureBundle(
        required_requests=[{"id": "fig_q1_fit", "required": True}],
        missing_requests=[
            {
                "figure_request_id": "fig_q1_fit",
                "required": True,
                "satisfied": False,
                "reason": "missing_source_data",
            }
        ],
    )

    snapshot = QualityGateStage.evaluate(
        work_dir=tmp_path,
        result_registry={"verified_results": [{"id": "q1"}], "blocked_results": []},
        figure_bundle=bundle,
        artifact_registry=FakeRegistry(),
        artifact_valid_images=[],
        writer_images=[],
        solve_spec={"requires_figures": False, "expected_figures": False},
        figure_plan_payload={
            "source": "result_registry",
            "requires_figures": True,
            "requires_result_figures": True,
            "required_feasible_count": 1,
            "figure_requests": [{"id": "fig_q1_fit", "required": True}],
        },
        workflow_mode="standard",
        expected_figures=False,
        trace=trace,
    )

    assert snapshot.passed is False
    assert snapshot.expected_figures is True
    assert snapshot.figure_plan_diagnostics["requirement_source"] == "figure_plan"
    assert snapshot.figure_plan_diagnostics["required_feasible_count"] == 1
    assert "未满足全部 required figure_requests" in snapshot.reason


def test_quality_gate_ignores_legacy_expected_figures_when_plan_says_no_figures(tmp_path: Path) -> None:
    class FakeRegistry:
        figure_rejections = []

        def rejection_summary(self):
            return []

    trace = WorkflowTrace()
    bundle = FigureBundle()

    snapshot = QualityGateStage.evaluate(
        work_dir=tmp_path,
        result_registry={"verified_results": [{"id": "q1"}], "blocked_results": []},
        figure_bundle=bundle,
        artifact_registry=FakeRegistry(),
        artifact_valid_images=[],
        writer_images=[],
        solve_spec={"requires_figures": True, "expected_figures": True},
        figure_plan_payload={
            "source": "result_registry",
            "requires_figures": False,
            "requires_result_figures": False,
            "requires_explanatory_figures": False,
            "requires_figures_by_problem": False,
            "required_feasible_count": 0,
            "figure_requests": [],
        },
        workflow_mode="standard",
        expected_figures=True,
        trace=trace,
    )

    assert snapshot.passed is True
    assert snapshot.expected_figures is False
    assert snapshot.figure_plan_diagnostics["requirement_source"] == "figure_plan"


def test_workflow_expected_figures_prefers_figure_plan_over_legacy_solve_spec() -> None:
    from app.core.workflow import _expected_figures_from_plan_or_legacy

    solve_spec = {
        "requires_figures": True,
        "requires_result_figures": True,
        "requires_explanatory_figures": False,
    }
    figure_plan = {
        "source": "VisualizationPlannerSkill",
        "requires_figures": False,
        "requires_result_figures": False,
        "requires_explanatory_figures": False,
        "figure_requests": [],
    }

    assert _expected_figures_from_plan_or_legacy(
        figure_plan,
        solve_spec,
        "需要绘图的旧题面提示",
        "legacy breakdown asks for charts",
    ) is False


def test_figure_recovery_stage_refreshes_registry_after_render(monkeypatch, tmp_path: Path) -> None:
    class FakeRenderReport:
        def to_dict(self):
            return {"created_images": ["output/fig_q1.png"], "missing": []}

    class FakeRegistry:
        def validate_for_paper(self):
            return ["output/fig_q1.png"]

    monkeypatch.setattr(
        "app.core.stages.figure_recovery_stage.RendererSkill.render_required_requests",
        lambda **kwargs: FakeRenderReport(),
    )
    monkeypatch.setattr(
        "app.core.stages.figure_recovery_stage.ArtifactRegistry.load",
        lambda *args, **kwargs: FakeRegistry(),
    )

    snapshot = FigureRecoveryStage.render_required_requests(
        work_dir=tmp_path,
        result_registry={"verified_results": [{"id": "q1"}]},
        required_requests=[{"id": "fig_q1"}],
        chart_language="English",
        chart_images=["output/existing.png"],
        structured_result_files=["solve_structured_results.json"],
        contract=None,
    )

    assert snapshot.report["created_images"] == ["output/fig_q1.png"]
    assert snapshot.chart_images == ["output/existing.png", "output/fig_q1.png"]
    assert snapshot.artifact_valid_images == ["output/fig_q1.png"]


def test_figure_recovery_reevaluates_plan_without_mutating_solve_spec(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "fit.csv").write_text(
        "x,observed,fitted\n1,2.0,2.1\n2,4.0,3.9\n",
        encoding="utf-8",
    )
    solve_spec = {
        "figure_requests": [
            {
                "id": "legacy_request",
                "required": True,
                "semantic_role": "comparison_bar",
                "figure_kind": "result_figure",
                "required_data": ["missing.csv"],
                "linked_result_ids": ["legacy_result"],
            }
        ],
        "required_feasible_count": 99,
        "blocked_count": 99,
    }
    figure_plan_payload = {
        "source": "figure_plan",
        "figure_requests": [
            {
                "id": "fig_q1_data_overview",
                "required": True,
                "semantic_role": "data_overview",
                "figure_kind": "result_figure",
                "required_data": ["data/fit.csv"],
                "required_columns": ["x", "observed", "fitted"],
            }
        ],
    }

    snapshot = FigureRecoveryStage.reevaluate_solve_spec_requests(
        work_dir=tmp_path,
        solve_spec=solve_spec,
        chart_language="English",
        solve_spec_path=tmp_path / "solve_spec.json",
        figure_plan_payload=figure_plan_payload,
    )

    assert [item["id"] for item in snapshot.requests] == ["fig_q1_data_overview"]
    assert snapshot.figure_plan_payload["required_feasible_count"] == 1
    assert snapshot.figure_plan_payload["blocked_count"] == 0
    assert solve_spec["figure_requests"][0]["id"] == "legacy_request"
    assert solve_spec["required_feasible_count"] == 99
    assert solve_spec["blocked_count"] == 99


def test_finalizer_stage_exports_quality_failure(monkeypatch, tmp_path: Path) -> None:
    def fake_export_failure_report(**kwargs):
        assert kwargs["gate_reason"] == "blocked"
        (tmp_path / "failure.md").write_text("# failed\n", encoding="utf-8")
        return str(tmp_path / "failure.md"), str(tmp_path / "failure.docx")

    monkeypatch.setattr(
        "app.core.stages.finalizer_stage.DocumentFinalizer.export_failure_report",
        fake_export_failure_report,
    )

    output = FinalizerStage.export_quality_failure(
        work_dir=tmp_path,
        question="q",
        result_registry={},
        gate_reason="blocked",
        stage_outputs={},
    )

    assert output.paper_path.endswith("failure.md")
    assert output.docx_path.endswith("failure.docx")
    bundle = json.loads((tmp_path / "verified_output_bundle.json").read_text(encoding="utf-8"))
    assert bundle["status"] == "failed"
    assert bundle["ready_for_actual_test"] is False
    assert bundle["failure"]["gate_reason"] == "blocked"


def test_finalizer_stage_writes_verified_output_bundle_on_success(monkeypatch, tmp_path: Path) -> None:
    async def fake_finalize_async(**kwargs):
        paper_path = tmp_path / "res.md"
        paper_path.write_text("# done\n\n![figure](output/fig.png)\n", encoding="utf-8")
        return str(paper_path), "", str(tmp_path / "notebook.ipynb")

    monkeypatch.setattr(
        "app.core.stages.finalizer_stage.DocumentFinalizer.finalize_async",
        fake_finalize_async,
    )

    output = asyncio.run(
        FinalizerStage.finalize_success(
            work_dir=tmp_path,
            task_id="task_1",
            task_type="writing",
            paper_content="# done",
            allowed_images=["output/fig.png"],
            required_images=["output/fig.png"],
            result_payload={
                "result_coverage": {"verified_count": 1, "blocked_count": 0},
                "stages": {
                    "figure_bundle": {
                        "available_figures": [{"path": "output/fig.png"}],
                        "missing_requests": [],
                        "blocked_requests": [],
                    },
                    "figure_plan_diagnostics": {
                        "effective_expected_figures": True,
                        "blocked_request_count": 0,
                        "diagnostic_summary": {"count": 0, "by_category": {}, "top_reasons": []},
                    },
                },
            },
        )
    )

    assert output.paper_path.endswith("res.md")
    bundle = json.loads((tmp_path / "verified_output_bundle.json").read_text(encoding="utf-8"))
    assert bundle["status"] == "passed"
    assert bundle["ready_for_actual_test"] is True
    assert bundle["task"]["task_id"] == "task_1"
    assert bundle["result_gate"]["verified_count"] == 1
    assert bundle["visualization_gate"]["available_figure_count"] == 1
    assert bundle["document_gate"]["missing_required_images"] == []


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


def test_visualization_schema_normalizes_long_term_regression_view_names() -> None:
    views = supported_views_for_result_type("regression_model")

    assert "fitting_curve" in views
    assert "residual_plot" in views
    assert "result_overview" in views
    assert "scatter_with_fit" not in views


def test_visualization_planner_adds_explanatory_flowchart_from_complex_solve_spec(tmp_path: Path) -> None:
    (tmp_path / "fit_results.csv").write_text(
        "x,observed,fitted,residual\n1,10,9.5,0.5\n2,12,12.2,-0.2\n",
        encoding="utf-8",
    )
    registry = {
        "verified_results": [
            {
                "id": "fit_result",
                "section": "problem2",
                "result_type": "regression_model",
                "data_artifacts": ["fit_results.csv"],
                "visualization_contract": {
                    "result_id": "fit_result",
                    "candidate_views": ["residual_plot"],
                    "source_data": ["fit_results.csv"],
                },
            }
        ],
        "blocked_results": [],
        "summary": {"verified_count": 1, "blocked_count": 0},
    }
    solve_spec = {
        "requires_result_figures": False,
        "requires_explanatory_figures": False,
        "subproblems": [
            {
                "id": "problem2",
                "method": "nonlinear least squares with flow conservation and signal delay C(t)",
                "steps": [
                    "define f1(t), f2(t), f3(t)",
                    "build conservation equation",
                    "fit parameters",
                    "check RMSE and residuals",
                ],
                "complexity_hint": "complex",
            }
        ],
    }

    plan = VisualizationPlannerSkill.build_figure_plan(
        result_registry=registry,
        chart_language="English",
        work_dir=str(tmp_path),
        solve_spec=solve_spec,
    )

    explanatory = [
        item for item in plan["figure_requests"]
        if item.get("figure_kind") == "explanatory_figure"
    ]
    assert plan["requires_explanatory_figures"] is True
    assert plan["required_feasible_explanatory_count"] == 1
    assert explanatory
    assert explanatory[0]["semantic_role"] == "algorithm_flowchart"
    assert explanatory[0]["required"] is True
    assert explanatory[0]["must_include_in_paper"] is True


def test_visualization_planner_accepts_scatter_with_fit_contract_alias(tmp_path: Path) -> None:
    registry = {
        "verified_results": [
            {
                "id": "q1_fit",
                "section": "q1",
                "result_type": "regression_model",
                "source_data": ["result_registry.json"],
                "visualization_contract": {
                    "result_id": "q1_fit",
                    "required_views": ["scatter_with_fit"],
                    "fallback_views": ["result_overview"],
                },
            }
        ],
        "blocked_results": [],
    }

    plan = VisualizationPlannerSkill.build_figure_plan(
        result_registry=registry,
        chart_language="English",
        work_dir=str(tmp_path),
        solve_spec={"requires_result_figures": True},
    )

    roles = [item.get("semantic_role") for item in plan["figure_requests"]]
    assert roles == ["fitting_curve", "result_overview"]
    assert plan["source"] == "result_registry"


def test_visualization_planner_reports_missing_contract(tmp_path: Path) -> None:
    registry = {
        "verified_results": [
            {
                "id": "q1_metric",
                "section": "q1",
                "value": 2.8,
                "source_data": ["result_registry.json"],
            }
        ],
        "blocked_results": [],
    }

    plan = VisualizationPlannerSkill.build_figure_plan(
        result_registry=registry,
        chart_language="English",
        work_dir=str(tmp_path),
        solve_spec={"requires_result_figures": True},
    )

    assert plan["blocked_count"] == 1
    assert plan["figure_requests"][0]["protocol_blocked_reason"] == "missing_visualization_contract"
    assert plan["workflow_issues"][0]["reason"] == "missing_visualization_contract"


def test_visualization_planner_reports_missing_contract_source_data(tmp_path: Path) -> None:
    registry = {
        "verified_results": [
            {
                "id": "q1_metric",
                "section": "q1",
                "value": 2.8,
                "visualization_contract": {
                    "result_id": "q1_metric",
                    "required_views": ["result_overview"],
                },
            }
        ],
        "blocked_results": [],
    }

    plan = VisualizationPlannerSkill.build_figure_plan(
        result_registry=registry,
        chart_language="English",
        work_dir=str(tmp_path),
        solve_spec={"requires_result_figures": True},
    )

    assert plan["blocked_count"] == 1
    assert plan["figure_requests"][0]["protocol_blocked_reason"] == "missing_visualization_source_data"
    assert plan["workflow_issues"][0]["reason"] == "missing_visualization_source_data"


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


def test_renderer_skill_renders_peak_valley_annotation(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "spectrum.csv").write_text(
        "wavenumber,reflectance\n1000,0.20\n1100,0.30\n1200,0.18\n",
        encoding="utf-8",
    )

    result = RendererSkill.render_required_requests(
        work_dir=str(tmp_path),
        result_registry={
            "verified_results": [
                {"id": "q1_peak_valley", "value": 0.30, "source_data": ["data/spectrum.csv"]},
            ]
        },
        required_requests=[
            {
                "id": "fig_q1_peak_valley_annotation",
                "subproblem_id": "q1",
                "required": True,
                "figure_kind": "result_figure",
                "semantic_role": "peak_valley_annotation",
                "required_data": ["data/spectrum.csv"],
                "linked_result_ids": ["q1_peak_valley"],
                "view_type": "peak_valley_annotation",
                "expected_content": ["Peak valley annotation"],
            }
        ],
        chart_language="English",
    )

    assert result.created_images == ["output/fig_q1_peak_valley_annotation.png"]
    assert result.satisfied == [{"figure_request_id": "fig_q1_peak_valley_annotation", "required": True, "satisfied": True}]
    assert result.missing == []


def test_renderer_skill_renders_registry_series_curves(tmp_path: Path) -> None:
    result = RendererSkill.render_required_requests(
        work_dir=str(tmp_path),
        result_registry={
            "verified_results": [
                {"id": "q1_t1", "value": 1.2, "source_data": ["trajectory.csv"]},
                {"id": "q1_t2", "value": 2.4, "source_data": ["trajectory.csv"]},
                {"id": "q1_t3", "value": 3.6, "source_data": ["trajectory.csv"]},
            ]
        },
        required_requests=[
            {
                "id": "fig_q1_trajectory_shielding",
                "subproblem_id": "q1",
                "required": True,
                "figure_kind": "result_figure",
                "semantic_role": "trajectory_shielding",
                "required_data": ["trajectory.csv"],
                "linked_result_ids": ["q1_t1", "q1_t2", "q1_t3"],
                "view_type": "trajectory_shielding",
                "expected_content": ["Trajectory shielding"],
            },
            {
                "id": "fig_q1_distance_time_curve",
                "subproblem_id": "q1",
                "required": True,
                "figure_kind": "result_figure",
                "semantic_role": "distance_time_curve",
                "required_data": ["trajectory.csv"],
                "linked_result_ids": ["q1_t1", "q1_t2", "q1_t3"],
                "view_type": "distance_time_curve",
                "expected_content": ["Distance time curve"],
            },
        ],
        chart_language="English",
    )

    assert result.created_images == [
        "output/fig_q1_trajectory_shielding.png",
        "output/fig_q1_distance_time_curve.png",
    ]
    assert len(result.satisfied) == 2
    assert result.missing == []


def test_renderer_skill_renders_explanatory_schematics(tmp_path: Path) -> None:
    result = RendererSkill.render_required_requests(
        work_dir=str(tmp_path),
        result_registry={"verified_results": []},
        required_requests=[
            {
                "id": "fig_q1_physical_principle_schematic",
                "subproblem_id": "q1",
                "required": True,
                "figure_kind": "explanatory_figure",
                "semantic_role": "physical_principle_schematic",
                "required_data": ["result_registry.json"],
                "expected_content": ["Physical principle"],
                "depends_on": {
                    "model_objects": ["model_core"],
                    "formulas": ["x = vt"],
                    "source_problem_refs": ["q1"],
                },
            },
            {
                "id": "fig_q1_timeline_schematic",
                "subproblem_id": "q1",
                "required": True,
                "figure_kind": "explanatory_figure",
                "semantic_role": "timeline_schematic",
                "required_data": ["result_registry.json"],
                "expected_content": ["Timeline"],
                "depends_on": {"model_objects": ["stage"], "source_problem_refs": ["q1"]},
            },
            {
                "id": "fig_q1_algorithm_flowchart",
                "subproblem_id": "q1",
                "required": True,
                "figure_kind": "explanatory_figure",
                "semantic_role": "algorithm_flowchart",
                "required_data": ["result_registry.json"],
                "expected_content": ["Algorithm flow"],
                "depends_on": {"model_objects": ["algorithm"], "source_problem_refs": ["q1"]},
            },
        ],
        chart_language="English",
    )

    assert result.created_images == [
        "output/fig_q1_physical_principle_schematic.png",
        "output/fig_q1_timeline_schematic.png",
        "output/fig_q1_algorithm_flowchart.png",
    ]
    assert len(result.satisfied) == 3
    assert result.missing == []
