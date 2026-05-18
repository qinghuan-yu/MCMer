import base64
import json
import zipfile
from pathlib import Path

from app.core.agents.coder_agent import CoderAgent
from app.core.workflow import _generated_image_evidence_paths, _has_generated_image_evidence, _language_verified_generated_images, _quality_gate_before_writing
from app.utils.common_utils import finalize_markdown_export
from app.utils.figure_artifacts import is_verified_paper_figure, is_placeholder_filename, contains_placeholder_text


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB/6X"
    "9q7sAAAAASUVORK5CYII="
)


def _write_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PNG_1X1)


def test_chinese_task_rejects_english_figure_artifact(tmp_path: Path) -> None:
    _write_png(tmp_path / "output" / "english.png")
    payload = {
        "section": "solve",
        "summary": "ok",
        "key_results": [{"id": "r1", "name": "r1", "value": 1, "unit": "m", "formula": "x=1", "inputs": {}, "source_data": ["data.csv"], "code_cell": "1", "evidence": "ok", "source": "test", "verified": True, "status": "verified", "warnings": []}],
        "generated_files": [
            {
                "path": "output/english.png",
                "kind": "figure",
                "paper_ready": True,
                "visible_text_language": "English",
                "chart_language_verified": True,
                "visible_text_audit": ["title", "axis labels"],
            }
        ],
        "warnings": [],
    }
    (tmp_path / "solve_structured_results.json").write_text(json.dumps(payload), encoding="utf-8")

    assert _language_verified_generated_images(
        str(tmp_path),
        ["solve_structured_results.json"],
        "Simplified Chinese",
    ) == []


def test_english_task_rejects_chinese_figure_artifact(tmp_path: Path) -> None:
    _write_png(tmp_path / "output" / "chinese.png")
    artifact = {
        "path": "output/chinese.png",
        "kind": "figure",
        "paper_ready": True,
        "visible_text_language": "Simplified Chinese",
        "chart_language_verified": True,
        "visible_text_audit": ["title", "axis labels"],
    }

    assert not is_verified_paper_figure(artifact, "English", tmp_path)


def test_chart_guard_blocks_deliverable_savefig_and_wrong_language_labels() -> None:
    agent = CoderAgent(task_id="t", model=None, work_dir=".")

    savefig_message = agent._chart_language_guard_message(
        "import matplotlib.pyplot as plt\nplt.plot([1],[1])\nplt.savefig('output/final.png')",
        "English",
    )
    assert "save_paper_figure" in savefig_message

    # Must provide complete params to get past param validation to language check
    language_message = agent._chart_language_guard_message(
        "import matplotlib.pyplot as plt\nplt.title('Result')\nsave_paper_figure(plt.gcf(), 'final.png', visible_text_audit=['title', 'xlabel'], linked_result_ids=['r1'], source_data=['data.csv'])",
        "Simplified Chinese",
    )
    assert "Simplified Chinese" in language_message

    reverse_message = agent._chart_language_guard_message(
        "import matplotlib.pyplot as plt\nplt.title('结果')\nsave_paper_figure(plt.gcf(), 'final.png', visible_text_audit=['结果', '横轴'], linked_result_ids=['r1'], source_data=['data.csv'])",
        "English",
    )
    assert "English" in reverse_message

    shadow_message = agent._chart_language_guard_message(
        "def save_paper_figure(fig, filename):\n    return None",
        "Simplified Chinese",
    )
    assert "must not be redefined" in shadow_message

    variable_savefig_message = agent._chart_language_guard_message(
        "import matplotlib.pyplot as plt\nfilename='output/final.png'\nplt.savefig(filename)",
        "Simplified Chinese",
    )
    assert "savefig" in variable_savefig_message


def test_markdown_image_whitelist_controls_docx_media(tmp_path: Path) -> None:
    _write_png(tmp_path / "output" / "allowed.png")
    _write_png(tmp_path / "output" / "blocked.png")
    md = "![allowed](output/allowed.png)\n\n![blocked](output/blocked.png)\n"
    docx_path = tmp_path / "res.docx"

    assert finalize_markdown_export(
        str(tmp_path / "res.md"),
        md,
        str(docx_path),
        ["output/allowed.png"],
    )
    saved_md = (tmp_path / "res.md").read_text(encoding="utf-8")
    assert "output/allowed.png" in saved_md
    assert "output/blocked.png" not in saved_md
    with zipfile.ZipFile(docx_path) as archive:
        media = [name for name in archive.namelist() if name.startswith("word/media/")]
    assert len(media) == 1


def test_debug_images_count_as_repair_evidence_not_paper_ready(tmp_path: Path) -> None:
    _write_png(tmp_path / "debug_artifacts" / "diagnostic.png")

    assert _language_verified_generated_images(str(tmp_path), [], "Simplified Chinese") == []
    assert _has_generated_image_evidence(str(tmp_path), [], []) is True
    assert _generated_image_evidence_paths(str(tmp_path), [], []) == ["debug_artifacts/diagnostic.png"]


def test_artifact_evidence_field_counts_as_image_evidence(tmp_path: Path) -> None:
    _write_png(tmp_path / "debug_artifacts" / "blocked.png")
    payload = {
        "section": "solve",
        "summary": "blocked",
        "key_results": [],
        "generated_files": [],
        "artifact_evidence": ["debug_artifacts/blocked.png"],
        "warnings": ["blocked"],
    }
    (tmp_path / "solve_structured_results.json").write_text(json.dumps(payload), encoding="utf-8")

    assert _generated_image_evidence_paths(
        str(tmp_path),
        ["solve_structured_results.json"],
        [],
    ) == ["debug_artifacts/blocked.png"]


def test_helper_artifact_with_created_by_passes(tmp_path: Path) -> None:
    """Positive case: artifact produced by save_paper_figure() passes the gate."""
    _write_png(tmp_path / "output" / "figure.png")
    artifact = {
        "path": "output/figure.png",
        "kind": "figure",
        "paper_ready": True,
        "visible_text_language": "Simplified Chinese",
        "chart_language_verified": True,
        "visible_text_audit": ["标题", "横轴", "纵轴"],
        "created_by": "save_paper_figure",
        "helper_version": 3,
        "is_placeholder": False,
        "figure_role": "result_summary",
        "linked_result_ids": ["r1"],
        "source_data": ["result_registry.json"],
    }
    assert is_verified_paper_figure(artifact, "Simplified Chinese", tmp_path)


def test_old_artifact_without_created_by_rejected(tmp_path: Path) -> None:
    """Old artifacts without helper_version/created_by are now rejected — helper is mandatory."""
    _write_png(tmp_path / "output" / "old_figure.png")
    artifact = {
        "path": "output/old_figure.png",
        "kind": "figure",
        "paper_ready": True,
        "visible_text_language": "Simplified Chinese",
        "chart_language_verified": True,
        "visible_text_audit": ["标题", "横轴"],
    }
    assert not is_verified_paper_figure(artifact, "Simplified Chinese", tmp_path)


def test_artifact_with_helper_version_but_no_created_by_rejected(tmp_path: Path) -> None:
    """Agent hand-wrote helper_version but forgot created_by → rejected."""
    _write_png(tmp_path / "output" / "fake.png")
    artifact = {
        "path": "output/fake.png",
        "kind": "figure",
        "paper_ready": True,
        "visible_text_language": "Simplified Chinese",
        "chart_language_verified": True,
        "visible_text_audit": ["标题"],
        "helper_version": 3,
    }
    assert not is_verified_paper_figure(artifact, "Simplified Chinese", tmp_path)


def test_helper_artifact_english_task(tmp_path: Path) -> None:
    """Positive case: English task with proper helper artifact."""
    _write_png(tmp_path / "output" / "en_fig.png")
    artifact = {
        "path": "output/en_fig.png",
        "kind": "figure",
        "paper_ready": True,
        "visible_text_language": "English",
        "chart_language_verified": True,
        "visible_text_audit": ["Title", "X axis", "Y axis"],
        "created_by": "save_paper_figure",
        "helper_version": 3,
        "is_placeholder": False,
        "figure_role": "result_summary",
        "linked_result_ids": ["r1"],
        "source_data": ["result_registry.json"],
    }
    assert is_verified_paper_figure(artifact, "English", tmp_path)


# --- Placeholder filename and text tests ---

def test_placeholder_filename_rejected(tmp_path: Path) -> None:
    """Placeholder filenames must be rejected by is_verified_paper_figure."""
    _write_png(tmp_path / "output" / "test_plot.png")
    artifact = {
        "path": "output/test_plot.png",
        "kind": "figure",
        "paper_ready": True,
        "visible_text_language": "Simplified Chinese",
        "chart_language_verified": True,
        "visible_text_audit": ["标题", "横轴"],
        "created_by": "save_paper_figure",
        "helper_version": 3,
    }
    assert not is_verified_paper_figure(artifact, "Simplified Chinese", tmp_path)


def test_placeholder_filename_detection() -> None:
    """is_placeholder_filename correctly identifies test/demo names."""
    assert is_placeholder_filename("output/test_plot.png")
    assert is_placeholder_filename("output/demo_figure.png")
    assert is_placeholder_filename("output/example_chart.png")
    assert is_placeholder_filename("output/sample_result.png")
    assert is_placeholder_filename("output/placeholder.png")
    assert is_placeholder_filename("output/正弦图.png")
    assert is_placeholder_filename("output/示例图.png")
    assert not is_placeholder_filename("output/problem1_trajectory.png")
    assert not is_placeholder_filename("output/result_comparison.png")
    assert not is_placeholder_filename("output/figure1_convergence.png")


def test_placeholder_text_detection() -> None:
    """contains_placeholder_text correctly identifies placeholder content."""
    assert contains_placeholder_text("This is a test plot")
    assert contains_placeholder_text("demo figure")
    assert contains_placeholder_text("示例图表")
    assert contains_placeholder_text("正弦曲线")
    assert not contains_placeholder_text("问题1轨迹图")
    assert not contains_placeholder_text("收敛性分析")


def test_placeholder_audit_text_rejected(tmp_path: Path) -> None:
    """Artifacts with placeholder text in audit are rejected."""
    _write_png(tmp_path / "output" / "real_figure.png")
    artifact = {
        "path": "output/real_figure.png",
        "kind": "figure",
        "paper_ready": True,
        "visible_text_language": "Simplified Chinese",
        "chart_language_verified": True,
        "visible_text_audit": ["test plot", "横轴", "纵轴"],
        "created_by": "save_paper_figure",
        "helper_version": 3,
    }
    assert not is_verified_paper_figure(artifact, "Simplified Chinese", tmp_path)


def test_is_placeholder_flag_rejected(tmp_path: Path) -> None:
    """Artifacts with is_placeholder=True are rejected."""
    _write_png(tmp_path / "output" / "figure.png")
    artifact = {
        "path": "output/figure.png",
        "kind": "figure",
        "paper_ready": True,
        "visible_text_language": "Simplified Chinese",
        "chart_language_verified": True,
        "visible_text_audit": ["标题"],
        "created_by": "save_paper_figure",
        "helper_version": 3,
        "is_placeholder": True,
    }
    assert not is_verified_paper_figure(artifact, "Simplified Chinese", tmp_path)


# --- Quality gate tests ---

def test_quality_gate_blocks_zero_verified() -> None:
    """Quality gate must fail when verified_count == 0."""
    registry = {
        "verified_results": [],
        "blocked_results": [{"id": "r1", "status": "blocked"}],
        "summary": {"verified_count": 0, "blocked_count": 1},
    }
    passed, reason = _quality_gate_before_writing(registry, [], False, "standard")
    assert not passed
    assert "verified_count=0" in reason


def test_quality_gate_passes_with_verified_results() -> None:
    """Quality gate passes when verified_count > 0 and no figures expected."""
    registry = {
        "verified_results": [{"id": "r1", "status": "verified"}],
        "blocked_results": [],
        "summary": {"verified_count": 1, "blocked_count": 0},
    }
    passed, reason = _quality_gate_before_writing(registry, [], False, "standard")
    assert passed
    assert reason == ""


def test_quality_gate_blocks_expected_figures_missing() -> None:
    """Quality gate fails when expected_figures=True but no images."""
    registry = {
        "verified_results": [{"id": "r1", "status": "verified"}],
        "blocked_results": [],
        "summary": {"verified_count": 1, "blocked_count": 0},
    }
    passed, reason = _quality_gate_before_writing(registry, [], True, "standard")
    assert not passed
    assert "expected_figures=true" in reason


def test_quality_gate_passes_with_verified_and_images() -> None:
    """Quality gate passes when verified and images present."""
    registry = {
        "verified_results": [{"id": "r1", "status": "verified"}],
        "blocked_results": [],
        "summary": {"verified_count": 1, "blocked_count": 0},
    }
    passed, reason = _quality_gate_before_writing(registry, ["output/fig.png"], True, "standard")
    assert passed


# --- linked_result_ids semantic binding tests ---

def test_empty_linked_result_ids_rejected_when_verified_ids_provided(tmp_path: Path) -> None:
    """Artifact with linked_result_ids=[] is rejected when verified_result_ids is provided."""
    _write_png(tmp_path / "output" / "figure.png")
    artifact = {
        "path": "output/figure.png",
        "kind": "figure",
        "paper_ready": True,
        "visible_text_language": "Simplified Chinese",
        "chart_language_verified": True,
        "visible_text_audit": ["标题", "横轴"],
        "created_by": "save_paper_figure",
        "helper_version": 3,
        "linked_result_ids": [],
        "source_data": [],
    }
    verified_ids = {"r1", "r2"}
    assert not is_verified_paper_figure(artifact, "Simplified Chinese", tmp_path, verified_ids)


def test_linked_result_ids_referencing_non_verified_rejected(tmp_path: Path) -> None:
    """Artifact referencing non-verified result IDs is rejected."""
    _write_png(tmp_path / "output" / "figure.png")
    artifact = {
        "path": "output/figure.png",
        "kind": "figure",
        "paper_ready": True,
        "visible_text_language": "Simplified Chinese",
        "chart_language_verified": True,
        "visible_text_audit": ["标题", "横轴"],
        "created_by": "save_paper_figure",
        "helper_version": 3,
        "linked_result_ids": ["nonexistent_id"],
        "source_data": ["result_registry.json"],
    }
    verified_ids = {"r1", "r2"}
    assert not is_verified_paper_figure(artifact, "Simplified Chinese", tmp_path, verified_ids)


def test_linked_result_ids_skip_when_verified_ids_none(tmp_path: Path) -> None:
    """When verified_result_ids=None, linked_result_ids check is skipped."""
    _write_png(tmp_path / "output" / "figure.png")
    artifact = {
        "path": "output/figure.png",
        "kind": "figure",
        "paper_ready": True,
        "visible_text_language": "Simplified Chinese",
        "chart_language_verified": True,
        "visible_text_audit": ["标题", "横轴"],
        "created_by": "save_paper_figure",
        "helper_version": 3,
        "linked_result_ids": [],
        "source_data": [],
    }
    # verified_result_ids=None means skip the check (used by coder_agent guard)
    assert is_verified_paper_figure(artifact, "Simplified Chinese", tmp_path, None)


def test_linked_result_ids_valid_binding_passes(tmp_path: Path) -> None:
    """Artifact with valid linked_result_ids passes."""
    _write_png(tmp_path / "output" / "figure.png")
    artifact = {
        "path": "output/figure.png",
        "kind": "figure",
        "paper_ready": True,
        "visible_text_language": "Simplified Chinese",
        "chart_language_verified": True,
        "visible_text_audit": ["标题", "横轴"],
        "created_by": "save_paper_figure",
        "helper_version": 3,
        "linked_result_ids": ["r1"],
        "source_data": ["result_registry.json"],
    }
    verified_ids = {"r1", "r2"}
    assert is_verified_paper_figure(artifact, "Simplified Chinese", tmp_path, verified_ids)


def test_missing_linked_result_ids_field_rejected(tmp_path: Path) -> None:
    """Artifact missing linked_result_ids entirely is rejected when verified_ids provided."""
    _write_png(tmp_path / "output" / "figure.png")
    artifact = {
        "path": "output/figure.png",
        "kind": "figure",
        "paper_ready": True,
        "visible_text_language": "Simplified Chinese",
        "chart_language_verified": True,
        "visible_text_audit": ["标题", "横轴"],
        "created_by": "save_paper_figure",
        "helper_version": 3,
    }
    verified_ids = {"r1"}
    assert not is_verified_paper_figure(artifact, "Simplified Chinese", tmp_path, verified_ids)


# --- source_data validation tests ---

def test_empty_source_data_rejected_when_verified_ids_provided(tmp_path: Path) -> None:
    """Artifact with valid linked_result_ids but source_data=[] is rejected."""
    _write_png(tmp_path / "output" / "figure.png")
    artifact = {
        "path": "output/figure.png",
        "kind": "figure",
        "paper_ready": True,
        "visible_text_language": "Simplified Chinese",
        "chart_language_verified": True,
        "visible_text_audit": ["标题", "横轴"],
        "created_by": "save_paper_figure",
        "helper_version": 3,
        "linked_result_ids": ["r1"],
        "source_data": [],
    }
    verified_ids = {"r1", "r2"}
    assert not is_verified_paper_figure(artifact, "Simplified Chinese", tmp_path, verified_ids)


def test_missing_source_data_field_rejected(tmp_path: Path) -> None:
    """Artifact missing source_data entirely is rejected when verified_ids provided."""
    _write_png(tmp_path / "output" / "figure.png")
    artifact = {
        "path": "output/figure.png",
        "kind": "figure",
        "paper_ready": True,
        "visible_text_language": "Simplified Chinese",
        "chart_language_verified": True,
        "visible_text_audit": ["标题", "横轴"],
        "created_by": "save_paper_figure",
        "helper_version": 3,
        "linked_result_ids": ["r1"],
    }
    verified_ids = {"r1"}
    assert not is_verified_paper_figure(artifact, "Simplified Chinese", tmp_path, verified_ids)


def test_source_data_with_empty_strings_rejected(tmp_path: Path) -> None:
    """Artifact with source_data containing only whitespace strings is rejected."""
    _write_png(tmp_path / "output" / "figure.png")
    artifact = {
        "path": "output/figure.png",
        "kind": "figure",
        "paper_ready": True,
        "visible_text_language": "Simplified Chinese",
        "chart_language_verified": True,
        "visible_text_audit": ["标题", "横轴"],
        "created_by": "save_paper_figure",
        "helper_version": 3,
        "linked_result_ids": ["r1"],
        "source_data": ["  ", ""],
    }
    verified_ids = {"r1"}
    assert not is_verified_paper_figure(artifact, "Simplified Chinese", tmp_path, verified_ids)


# --- Solver behavior tests (P3) ---

def test_guard_blocks_figure_code_in_none_mode():
    """figure_mode=none should block savefig/plt calls."""
    from app.core.agents.coder_agent import CoderAgent
    agent = CoderAgent(task_id="t", model=None, work_dir=".", figure_mode="none")
    msg = agent._chart_language_guard_message(
        "import matplotlib.pyplot as plt\nplt.plot([1],[1])\nplt.savefig('output/fig.png')",
        "Simplified Chinese",
    )
    assert "纯数值任务" in msg
    assert "禁止" in msg


def test_guard_blocks_incomplete_save_paper_figure():
    """Guard should block save_paper_figure() missing required params (linked_result_ids, source_data)."""
    from app.core.agents.coder_agent import CoderAgent
    agent = CoderAgent(task_id="t", model=None, work_dir=".", figure_mode="paper_required")
    msg = agent._chart_language_guard_message(
        "save_paper_figure(fig, 'test.png', visible_text_audit=['title'])",
        "English",
    )
    # Missing linked_result_ids and source_data → blocked
    assert "blocked" in msg.lower() or "missing" in msg.lower()


def test_guard_blocks_generic_audit_text():
    """Guard should block save_paper_figure() with only generic audit markers."""
    from app.core.agents.coder_agent import CoderAgent
    agent = CoderAgent(task_id="t", model=None, work_dir=".", figure_mode="paper_required")
    msg = agent._chart_language_guard_message(
        "save_paper_figure(fig, 'result.png', visible_text_audit=['title', 'axis labels'], linked_result_ids=['r1'], source_data=['data.csv'])",
        "English",
    )
    assert "generic" in msg.lower() or "blocked" in msg.lower()


def test_guard_blocks_placeholder_filename_in_save_paper_figure():
    """Guard should block save_paper_figure() with placeholder filename."""
    from app.core.agents.coder_agent import CoderAgent
    agent = CoderAgent(task_id="t", model=None, work_dir=".", figure_mode="paper_required")
    msg = agent._chart_language_guard_message(
        "save_paper_figure(fig, 'test_plot.png', visible_text_audit=['标题', '横轴'], linked_result_ids=['r1'], source_data=['data.csv'])",
        "Simplified Chinese",
    )
    assert "placeholder" in msg.lower() or "blocked" in msg.lower()


def test_guard_allows_complete_save_paper_figure():
    """Guard should pass complete save_paper_figure() with all required params and real content."""
    from app.core.agents.coder_agent import CoderAgent
    agent = CoderAgent(task_id="t", model=None, work_dir=".", figure_mode="paper_required")
    msg = agent._chart_language_guard_message(
        "save_paper_figure(fig, 'problem1_trajectory.png', visible_text_audit=['波长/nm', '反射率/%'], linked_result_ids=['thickness_result'], source_data=['result_registry.json'])",
        "Simplified Chinese",
    )
    # Complete params, Chinese labels, descriptive filename → should pass
    assert "纯数值任务" not in msg
    assert "blocked" not in msg.lower()


def test_guard_catches_second_incomplete_call():
    """AST-based guard catches the second save_paper_figure() even if the first is complete."""
    from app.core.agents.coder_agent import CoderAgent
    agent = CoderAgent(task_id="t", model=None, work_dir=".", figure_mode="paper_required")
    code = (
        "save_paper_figure(fig, 'good.png', visible_text_audit=['波长/nm', '反射率/%'], linked_result_ids=['r1'], source_data=['data.csv'])\n"
        "save_paper_figure(fig, 'bad.png', visible_text_audit=['title'])\n"
    )
    msg = agent._chart_language_guard_message(code, "Simplified Chinese")
    # Should block because the second call is missing linked_result_ids
    assert "blocked" in msg.lower() or "missing" in msg.lower()


def test_guard_blocks_helper_exploration():
    """All modes should block inspect.signature exploration."""
    from app.core.agents.coder_agent import CoderAgent
    agent = CoderAgent(task_id="t", model=None, work_dir=".", figure_mode="paper_required")
    msg = agent._chart_language_guard_message(
        "import inspect\ninspect.signature(save_paper_figure)",
        "Simplified Chinese",
    )
    assert "Guard blocked" in msg or "helper" in msg.lower()


def test_subproblem_figure_mode_none_for_numeric_subproblem():
    """Numeric subproblem should get figure_mode=none even when task expects figures."""
    from app.core.workflow import _subproblem_figure_mode
    subproblem = {
        "id": "p1",
        "objective": "计算碳化硅外延层厚度",
        "steps": ["加载数据", "拟合参数", "输出数值结果"],
    }
    assert _subproblem_figure_mode(subproblem, True) == "none"


def test_subproblem_figure_mode_paper_required_for_chart_subproblem():
    """Subproblem mentioning charts should get figure_mode=paper_required."""
    from app.core.workflow import _subproblem_figure_mode
    subproblem = {
        "id": "p2",
        "objective": "绘制光谱对比图表",
        "steps": ["生成折线图", "保存可视化结果"],
    }
    assert _subproblem_figure_mode(subproblem, True) == "paper_required"


def test_subproblem_figure_mode_none_when_task_has_no_figures():
    """When task doesn't expect figures, all subproblems get figure_mode=none."""
    from app.core.workflow import _subproblem_figure_mode
    subproblem = {
        "id": "p1",
        "objective": "绘制图表",
        "steps": ["plot"],
    }
    assert _subproblem_figure_mode(subproblem, False) == "none"


def test_deterministic_summary_figure_passes_gate(tmp_path: Path) -> None:
    """Workflow fallback should create a gate-valid figure from verified results."""
    from app.core.workflow import (
        _create_verified_result_summary_figure,
        _language_verified_generated_images,
    )

    registry = {
        "verified_results": [
            {
                "id": "r1",
                "name": "有效遮蔽时长",
                "value": 4.75,
                "unit": "s",
                "source_file": "solve_structured_results.json",
            }
        ],
        "blocked_results": [],
        "summary": {"verified_count": 1, "blocked_count": 0},
    }
    created = _create_verified_result_summary_figure(
        str(tmp_path),
        registry,
        "Simplified Chinese",
    )

    assert created == ["output/verified_result_summary.png"]
    assert _language_verified_generated_images(
        str(tmp_path),
        [],
        "Simplified Chinese",
        registry,
    ) == created


# --- E2E Tests for Artifacts Module (阶段九) ---

def test_artifact_registry_loads_and_validates(tmp_path: Path) -> None:
    """ArtifactRegistry should load manifest and validate figures."""
    from app.artifacts.registry import ArtifactRegistry
    from app.artifacts.contracts import ProblemContract

    # Create a valid figure
    _write_png(tmp_path / "output" / "fig1.png")
    manifest = {
        "figures": [{
            "path": "output/fig1.png",
            "kind": "figure",
            "paper_ready": True,
            "visible_text_language": "Simplified Chinese",
            "chart_language_verified": True,
            "visible_text_audit": ["波长/nm", "反射率/%"],
            "created_by": "save_paper_figure",
            "helper_version": 3,
            "is_placeholder": False,
            "linked_result_ids": ["r1"],
            "source_data": ["result_registry.json"],
        }]
    }
    (tmp_path / "figure_artifacts.json").write_text(json.dumps(manifest), encoding="utf-8")

    # Write a structured result file (build_result_registry reads these)
    structured = {
        "section": "solve",
        "summary": "ok",
        "key_results": [
            {"id": "r1", "name": "厚度", "value": 42, "unit": "mm", "status": "verified", "verified": True, "source": "solver", "source_data": ["data.csv"], "code_cell": "1", "evidence": "ok", "formula": "", "inputs": {}, "warnings": []}
        ],
        "generated_files": [],
        "warnings": [],
    }
    (tmp_path / "solve_structured_results.json").write_text(json.dumps(structured), encoding="utf-8")

    contract = ProblemContract(
        document_language="Simplified Chinese",
        chart_language="Simplified Chinese",
        allowed_visible_text_language="zh-CN",
        font_profile="cjk",
        paper_requires_figures=True,
        paper_requires_tables=False,
        workflow_mode="standard",
    )

    art_reg = ArtifactRegistry.load(str(tmp_path), contract=contract)
    paper_ready = art_reg.validate_for_paper()

    assert "output/fig1.png" in paper_ready
    assert art_reg.verified_count() == 1
    assert art_reg.has_verified_results()
    assert len(art_reg.figure_rejections) == 0


def test_artifact_registry_rejects_placeholder(tmp_path: Path) -> None:
    """ArtifactRegistry should reject placeholder figures."""
    from app.artifacts.registry import ArtifactRegistry

    _write_png(tmp_path / "output" / "test_plot.png")
    manifest = {
        "figures": [{
            "path": "output/test_plot.png",
            "kind": "figure",
            "paper_ready": True,
            "visible_text_language": "Simplified Chinese",
            "chart_language_verified": True,
            "visible_text_audit": ["波长/nm", "反射率/%"],
            "created_by": "save_paper_figure",
            "helper_version": 3,
            "is_placeholder": False,
            "linked_result_ids": ["r1"],
            "source_data": ["data.csv"],
        }]
    }
    (tmp_path / "figure_artifacts.json").write_text(json.dumps(manifest), encoding="utf-8")

    registry = {
        "verified_results": [{"id": "r1", "status": "verified", "value": 42}],
        "blocked_results": [],
        "summary": {"verified_count": 1, "blocked_count": 0},
    }
    (tmp_path / "result_registry.json").write_text(json.dumps(registry), encoding="utf-8")

    art_reg = ArtifactRegistry.load(str(tmp_path))
    paper_ready = art_reg.validate_for_paper()

    assert "output/test_plot.png" not in paper_ready
    assert len(art_reg.figure_rejections) == 1
    assert "placeholder_filename" in art_reg.figure_rejections[0].rejected_by


def test_confidence_level_classification() -> None:
    """Result entries should get correct confidence levels."""
    from app.utils.common_utils import _determine_confidence_level

    # verified_exact: has code_cell and evidence
    assert _determine_confidence_level(
        {"code_cell": "1", "evidence": "ok"}, "verified", True, "solver", []
    ) == "verified_exact"

    # verified_source: from source data, no code_cell
    assert _determine_confidence_level(
        {"source_data": ["data.csv"]}, "verified", True, "solver", []
    ) == "verified_source"

    # verified_baseline: timeout warning
    assert _determine_confidence_level(
        {}, "verified", True, "solver", ["超时降级估算"]
    ) == "verified_baseline"

    # salvaged_pending_review: from artifact salvage
    assert _determine_confidence_level(
        {}, "verified", True, "artifact_salvage", []
    ) == "salvaged_pending_review"

    # blocked
    assert _determine_confidence_level(
        {}, "blocked", False, "solver", ["阻断"]
    ) == "blocked"

    # unverified
    assert _determine_confidence_level(
        {}, "unverified", False, "solver", []
    ) == "unverified"


def test_document_finalizer_exports(tmp_path: Path) -> None:
    """DocumentFinalizer should export markdown and optionally DOCX."""
    from app.artifacts.exporters import DocumentFinalizer

    md_path, docx_path, _ = DocumentFinalizer.finalize(
        work_dir=str(tmp_path),
        task_id="test",
        task_type="writing",
        paper_content="# Test\n\nHello world\n",
        allowed_images=None,
        result_payload={"test": True},
    )

    assert Path(md_path).exists()
    content = Path(md_path).read_text(encoding="utf-8")
    assert "Hello world" in content


def test_failure_report_export(tmp_path: Path) -> None:
    """DocumentFinalizer.export_failure_report should create failure files."""
    from app.artifacts.exporters import DocumentFinalizer

    registry = {
        "verified_results": [],
        "blocked_results": [{"id": "r1", "status": "blocked", "name": "test", "warnings": ["error"]}],
        "summary": {"verified_count": 0, "blocked_count": 1},
    }

    md_path, docx_path = DocumentFinalizer.export_failure_report(
        work_dir=str(tmp_path),
        question="Test question",
        result_registry=registry,
        gate_reason="verified_count=0",
    )

    assert Path(md_path).exists()
    assert Path(str(tmp_path / "failure_manifest.json")).exists()
    content = Path(md_path).read_text(encoding="utf-8")
    assert "verified_count=0" in content


def test_diagnostics_gate_reports(tmp_path: Path) -> None:
    """Gate reports should be serializable and saveable."""
    from app.artifacts.diagnostics import FigureGateReport, QualityGateReport, BudgetReport, WorkflowTrace

    fig_gate = FigureGateReport.from_rejections(
        ["output/fig1.png"],
        [{"path": "output/test.png", "rejected_by": ["placeholder_filename"]}],
    )
    assert not fig_gate.passed
    fig_gate.save(str(tmp_path))
    assert Path(tmp_path / "figure_artifact_gate_report.json").exists()

    quality_gate = QualityGateReport.from_check(
        verified_count=1, blocked_count=0, expected_figures=True,
        paper_ready_count=1, passed=True, reason="",
    )
    assert quality_gate.passed
    quality_gate.save(str(tmp_path))
    assert Path(tmp_path / "quality_gate_report.json").exists()

    budget = BudgetReport()
    budget.add("solver", "p1", 15, 22, guard_blocked=3)
    budget.save(str(tmp_path))
    assert Path(tmp_path / "budget_report.json").exists()

    trace = WorkflowTrace()
    trace.add_stage("solve", "completed", "ok", 12.5)
    trace.save(str(tmp_path))
    assert Path(tmp_path / "workflow_trace.json").exists()


def test_figure_request_planner(tmp_path: Path) -> None:
    """FigureRequestPlanner should generate requests from verified results."""
    from app.artifacts.figure_requests import FigureRequestPlanner

    registry = {
        "verified_results": [
            {"id": "thickness", "name": "厚度", "value": 0.35, "unit": "mm"},
            {"id": "reflectance", "name": "反射率", "value": 0.92, "unit": "%"},
        ],
        "blocked_results": [],
        "summary": {"verified_count": 2, "blocked_count": 0},
    }

    requests = FigureRequestPlanner.plan_from_registry(
        registry, "Simplified Chinese",
    )
    assert len(requests) > 0
    assert all(r.chart_language == "Simplified Chinese" for r in requests)
    assert all(len(r.linked_result_ids) > 0 for r in requests)
