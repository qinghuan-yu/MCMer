import base64
import json
import zipfile
from pathlib import Path

from app.core.agents.coder_agent import CoderAgent
from app.core.agents.writer_agent import _contains_tool_protocol_text
from app.core.figure_stage import FigureStage
from app.artifacts.artifact_evidence import (
    generated_image_evidence_paths,
    has_generated_image_evidence,
    language_verified_generated_images,
)
from app.utils.common_utils import finalize_markdown_export
from app.utils.figure_artifacts import is_verified_paper_figure, is_placeholder_filename, contains_placeholder_text


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB/6X"
    "9q7sAAAAASUVORK5CYII="
)


def _write_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PNG_1X1)


def test_writer_rejects_tool_protocol_text() -> None:
    from app.artifacts.exporters import _contains_tool_protocol_text as export_guard

    leaked = (
        '<｜｜DSML｜｜tool_calls>\n'
        '<｜｜DSML｜｜invoke name="run_script">\n'
        '<｜｜DSML｜｜parameter name="script" string="true">print("debug")</｜｜DSML｜｜parameter>\n'
        '</｜｜DSML｜｜invoke>\n'
        '</｜｜DSML｜｜tool_calls>'
    )
    assert _contains_tool_protocol_text(leaked)
    assert export_guard(leaked)
    assert _contains_tool_protocol_text('<||DSML||tool_calls><||DSML||invoke name="run_script">')
    assert not _contains_tool_protocol_text("# 正文\n\n这是普通 Markdown 论文正文。")
    assert not export_guard("# 正文\n\n这是普通 Markdown 论文正文。")


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

    assert language_verified_generated_images(
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

    assert language_verified_generated_images(str(tmp_path), [], "Simplified Chinese") == []
    assert has_generated_image_evidence(str(tmp_path), [], []) is True
    assert generated_image_evidence_paths(str(tmp_path), [], []) == ["debug_artifacts/diagnostic.png"]


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

    assert generated_image_evidence_paths(
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
    passed, reason = FigureStage.quality_gate_before_writing(
        registry,
        False,
        [],
        [],
        [],
        "standard",
    )
    assert not passed
    assert "verified_count=0" in reason


def test_quality_gate_passes_with_verified_results() -> None:
    """Quality gate passes when verified_count > 0 and no figures expected."""
    registry = {
        "verified_results": [{"id": "r1", "status": "verified"}],
        "blocked_results": [],
        "summary": {"verified_count": 1, "blocked_count": 0},
    }
    passed, reason = FigureStage.quality_gate_before_writing(
        registry,
        False,
        [],
        [],
        [],
        "standard",
    )
    assert passed
    assert reason == ""


def test_quality_gate_blocks_expected_figures_missing() -> None:
    """Quality gate fails when expected_figures=True but no images."""
    registry = {
        "verified_results": [{"id": "r1", "status": "verified"}],
        "blocked_results": [],
        "summary": {"verified_count": 1, "blocked_count": 0},
    }
    passed, reason = FigureStage.quality_gate_before_writing(
        registry,
        True,
        [{"id": "req_1", "required": True}],
        [],
        [{"figure_request_id": "req_1", "required": True, "satisfied": False, "reason": "missing"}],
        "standard",
    )
    assert not passed
    assert "required figure_requests" in reason


def test_quality_gate_passes_with_verified_and_images() -> None:
    """Quality gate passes when verified and images present."""
    registry = {
        "verified_results": [{"id": "r1", "status": "verified"}],
        "blocked_results": [],
        "summary": {"verified_count": 1, "blocked_count": 0},
    }
    passed, reason = FigureStage.quality_gate_before_writing(
        registry,
        True,
        [{"id": "req_1", "required": True}],
        [{"figure_request_id": "req_1", "required": True, "satisfied": True}],
        [],
        "standard",
    )
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
    subproblem = {
        "id": "p1",
        "objective": "计算碳化硅外延层厚度",
        "steps": ["加载数据", "拟合参数", "输出数值结果"],
    }
    assert FigureStage.subproblem_figure_mode(subproblem, True) == "none"


def test_subproblem_figure_mode_paper_required_for_chart_subproblem():
    """Subproblem mentioning charts should get figure_mode=paper_required."""
    subproblem = {
        "id": "p2",
        "objective": "绘制光谱对比图表",
        "steps": ["生成折线图", "保存可视化结果"],
    }
    assert FigureStage.subproblem_figure_mode(subproblem, True) == "paper_required"


def test_subproblem_figure_mode_none_when_task_has_no_figures():
    """When task doesn't expect figures, all subproblems get figure_mode=none."""
    subproblem = {
        "id": "p1",
        "objective": "绘制图表",
        "steps": ["plot"],
    }
    assert FigureStage.subproblem_figure_mode(subproblem, False) == "none"


def test_deterministic_summary_figure_passes_gate(tmp_path: Path) -> None:
    """Workflow fallback should create a gate-valid figure from verified results."""
    from app.core.workflow import _create_verified_result_summary_figure

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
    assert language_verified_generated_images(
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
        required_images=None,
        result_payload={"test": True},
    )

    assert Path(md_path).exists()
    content = Path(md_path).read_text(encoding="utf-8")
    assert "Hello world" in content


def test_document_finalizer_requires_partial_coverage_disclosure(tmp_path: Path) -> None:
    """Partial coverage diagnostics must be disclosed in the exported paper."""
    from app.artifacts.exporters import DocumentFinalizer

    result_payload = {
        "stages": {
            "writer_context": {
                "coverage_diagnostics": {
                    "must_disclose_partial": True,
                    "coverage_status": "partial",
                    "verified_count": 2,
                    "blocked_count": 3,
                    "provisional_verified_count": 1,
                }
            }
        }
    }

    try:
        DocumentFinalizer.finalize(
            work_dir=str(tmp_path),
            task_id="test",
            task_type="writing",
            paper_content="# Paper\n\nAll requested problems are solved.\n",
            allowed_images=None,
            required_images=None,
            result_payload=result_payload,
        )
        assert False, "Expected coverage disclosure validation failure"
    except ValueError as exc:
        assert getattr(exc, "error_code", "") == "FINALIZER_MISSING_COVERAGE_DISCLOSURE"
        assert getattr(exc, "details", {}).get("blocked_count") == 3


def test_document_finalizer_accepts_partial_coverage_disclosure(tmp_path: Path) -> None:
    """Finalizer allows partial coverage papers when the limitation is explicit."""
    from app.artifacts.exporters import DocumentFinalizer

    result_payload = {
        "stages": {
            "writer_context": {
                "coverage_diagnostics": {
                    "must_disclose_partial": True,
                    "coverage_status": "partial",
                    "verified_count": 2,
                    "blocked_count": 3,
                    "provisional_verified_count": 1,
                }
            }
        }
    }

    md_path, _, _ = DocumentFinalizer.finalize(
        work_dir=str(tmp_path),
        task_id="test",
        task_type="writing",
        paper_content="# Paper\n\nThis result has partial coverage; blocked branches remain for review.\n",
        allowed_images=None,
        required_images=None,
        result_payload=result_payload,
    )

    content = Path(md_path).read_text(encoding="utf-8")
    assert "partial coverage" in content


def test_document_finalizer_rejects_unwhitelisted_markdown_image(tmp_path: Path) -> None:
    """Finalizer must fail when markdown references images outside whitelist."""
    from app.artifacts.exporters import DocumentFinalizer

    _write_png(tmp_path / "output" / "allowed.png")
    _write_png(tmp_path / "output" / "blocked.png")

    paper = "![ok](output/allowed.png)\n\n![blocked](output/blocked.png)\n"
    try:
        DocumentFinalizer.finalize(
            work_dir=str(tmp_path),
            task_id="test",
            task_type="writing",
            paper_content=paper,
            allowed_images=["output/allowed.png"],
            required_images=None,
            result_payload={"test": True},
        )
        assert False, "Expected ValueError for unwhitelisted image reference"
    except ValueError as exc:
        assert "outside FigureBundle whitelist" in str(exc)
        assert getattr(exc, "error_code", "") == "FINALIZER_UNWHITELISTED_IMAGE_REF"


def test_document_finalizer_rejects_missing_markdown_image(tmp_path: Path) -> None:
    """Finalizer must fail when markdown references missing image files."""
    from app.artifacts.exporters import DocumentFinalizer

    paper = "![missing](output/missing.png)\n"
    try:
        DocumentFinalizer.finalize(
            work_dir=str(tmp_path),
            task_id="test",
            task_type="writing",
            paper_content=paper,
            allowed_images=["output/missing.png"],
            required_images=None,
            result_payload={"test": True},
        )
        assert False, "Expected ValueError for missing image file"
    except ValueError as exc:
        assert "missing image files" in str(exc)
        assert getattr(exc, "error_code", "") == "FINALIZER_MISSING_IMAGE_REF"


def test_document_finalizer_rejects_missing_required_image_reference(tmp_path: Path) -> None:
    """Finalizer must fail when required_images are not referenced by markdown."""
    from app.artifacts.exporters import DocumentFinalizer

    _write_png(tmp_path / "output" / "must_use.png")

    paper = "# Test\n\n正文没有引用任何图片。\n"
    try:
        DocumentFinalizer.finalize(
            work_dir=str(tmp_path),
            task_id="test",
            task_type="writing",
            paper_content=paper,
            allowed_images=["output/must_use.png"],
            required_images=["output/must_use.png"],
            result_payload={"test": True},
        )
        assert False, "Expected ValueError for missing required image reference"
    except ValueError as exc:
        assert "missing required figure references" in str(exc)
        assert getattr(exc, "error_code", "") == "FINALIZER_REQUIRED_IMAGE_NOT_REFERENCED"


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


def test_renderer_output_passes_figure_artifact_gate(tmp_path: Path) -> None:
    """Deterministic renderer output should pass FigureArtifact gate."""
    from app.artifacts.renderers import render_verified_summary

    verified = [
        {"id": "r1", "name": "厚度", "value": 0.35, "unit": "mm", "status": "verified"},
        {"id": "r2", "name": "反射率", "value": 0.92, "unit": "%", "status": "verified"},
    ]

    artifact = render_verified_summary(
        verified_results=verified,
        title="验证结果汇总",
        output_path=str(tmp_path / "output" / "verified_summary.png"),
        chart_language="Simplified Chinese",
        linked_result_ids=["r1", "r2"],
        source_data=["result_registry.json"],
        work_dir=str(tmp_path),
    )

    # Artifact should pass the gate
    assert artifact["created_by"] == "deterministic_renderer"
    assert artifact["helper_version"] >= 3
    assert artifact["is_placeholder"] is False
    assert len(artifact["linked_result_ids"]) > 0
    assert len(artifact["source_data"]) > 0

    # Should pass is_verified_paper_figure
    assert is_verified_paper_figure(artifact, "Simplified Chinese", tmp_path)


def test_chinese_renderer_no_english_headers(tmp_path: Path) -> None:
    """Chinese task renderer should use Chinese headers, not English."""
    from app.artifacts.renderers import render_verified_summary

    verified = [{"id": "r1", "name": "测试", "value": 1, "unit": "m", "status": "verified"}]
    artifact = render_verified_summary(
        verified_results=verified,
        title="中文标题",
        output_path=str(tmp_path / "output" / "cn_summary.png"),
        chart_language="Simplified Chinese",
        linked_result_ids=["r1"],
        source_data=["data.csv"],
        work_dir=str(tmp_path),
    )

    # Check that audit text contains Chinese, not English headers
    audit = artifact.get("visible_text_audit", [])
    audit_combined = " ".join(audit).lower()
    assert "id" not in audit_combined or "编号" in audit_combined
    assert "name" not in audit_combined or "名称" in audit_combined


def test_problem_contract_creation() -> None:
    """ProblemContract should correctly detect language from question."""
    from app.artifacts.contracts import ProblemContract

    cn_contract = ProblemContract.from_question("某碳化硅晶圆片的外延层厚度计算", workflow_mode="standard")
    assert cn_contract.chart_language == "Simplified Chinese"
    assert cn_contract.is_chinese
    assert cn_contract.font_profile == "cjk"

    en_contract = ProblemContract.from_question("Calculate the thickness of SiC epitaxial layer", workflow_mode="standard")
    assert en_contract.chart_language == "English"
    assert en_contract.is_english
    assert en_contract.font_profile == "latin"


def test_problem_contract_from_question_prefers_by_problem_figure_flag() -> None:
    from app.artifacts.contracts import ProblemContract

    contract = ProblemContract.from_question(
        question="请画图展示结果",
        solve_spec={
            "requires_figures_by_problem": False,
            "requires_result_figures": True,
            "requires_explanatory_figures": True,
        },
        workflow_mode="standard",
    )
    assert contract.paper_requires_figures is False


def test_save_revision_preserves_existing_res_payload(tmp_path: Path, monkeypatch) -> None:
    """save_revision must keep existing res.json fields (stages/coverage/images)."""
    from app.config.setting import settings
    from app.services.task_service import TaskManager

    monkeypatch.setattr(settings, "WORK_DIR", str(tmp_path))
    task_id = "rev-task"
    task_dir = tmp_path / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    original_payload = {
        "task_id": task_id,
        "task_type": "writing",
        "stages": {
            "paper_ready_images": ["output/keep.png"],
            "paper_ready_images_round_2": ["output/round2.png"],
        },
        "paper_ready_images": ["output/keep.png"],
        "result_coverage": {"verified": 3, "blocked": 1},
    }
    (task_dir / "res.json").write_text(json.dumps(original_payload, ensure_ascii=False), encoding="utf-8")

    manager = TaskManager()
    manager.save_revision(task_id=task_id, paper_content="# Revised\n\nUpdated content.\n", version=1)

    updated = json.loads((task_dir / "res.json").read_text(encoding="utf-8"))
    assert updated.get("stages", {}) == original_payload["stages"]
    assert updated.get("paper_ready_images", []) == original_payload["paper_ready_images"]
    assert updated.get("result_coverage", {}) == original_payload["result_coverage"]
    assert "Updated content" in updated.get("paper", "")


def test_save_revision_prefers_writer_context_allowed_images(tmp_path: Path, monkeypatch) -> None:
    """Revision whitelist must prioritize writer_context over legacy image lists."""
    from app.config.setting import settings
    from app.services.task_service import TaskManager

    monkeypatch.setattr(settings, "WORK_DIR", str(tmp_path))
    task_id = "rev-writer-images"
    task_dir = tmp_path / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "task_id": task_id,
        "task_type": "writing",
        "stages": {
            "paper_ready_images": ["output/legacy_diag.png"],
            "paper_ready_images_round_3": ["output/legacy_round.png"],
            "writer_available_images": ["output/writer_base.png"],
            "writer_available_images_round_2": ["output/writer_round2.png"],
            "writer_context": {
                "allowed_image_paths": ["output/context_base.png"],
                "required_images": ["output/context_base.png"],
            },
            "writer_context_round_4": {
                "allowed_image_paths": ["output/context_round4.png"],
                "required_images": ["output/context_round4.png"],
            },
        },
    }
    (task_dir / "res.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    captured: dict = {}

    def _fake_finalize(**kwargs):
        captured["allowed_images"] = kwargs.get("allowed_images")
        captured["required_images"] = kwargs.get("required_images")
        result_payload = kwargs.get("result_payload", {})
        result_payload["paper"] = kwargs.get("paper_content", "")
        (task_dir / "res.json").write_text(json.dumps(result_payload, ensure_ascii=False), encoding="utf-8")
        return str(task_dir / "res_v1.md"), "", ""

    monkeypatch.setattr("app.services.task_service.DocumentFinalizer.finalize", _fake_finalize)

    manager = TaskManager()
    manager.save_revision(task_id=task_id, paper_content="# Revised\n", version=1)

    assert captured.get("allowed_images") == ["output/context_round4.png"]
    assert captured.get("required_images") == ["output/context_round4.png"]


def test_deterministic_renderer_requires_cjk_font(monkeypatch) -> None:
    """Chinese deterministic rendering must hard-fail when no CJK font is available."""
    from app.artifacts import renderers

    # Force empty font inventory to emulate missing CJK runtime.
    from matplotlib import font_manager
    monkeypatch.setattr(font_manager.fontManager, "ttflist", [])

    try:
        renderers._configure_fonts("Simplified Chinese")
        assert False, "Expected RuntimeError when no CJK font is available"
    except RuntimeError as exc:
        assert "No CJK font available" in str(exc)


def test_polish_contract_prefers_parent_contract(tmp_path: Path, monkeypatch) -> None:
    """Polish should inherit parent ProblemContract before fallback text detection."""
    from app.artifacts.contracts import ProblemContract
    from app.core.workflow import _resolve_polish_contract

    parent_dir = tmp_path / "parent"
    parent_dir.mkdir(parents=True, exist_ok=True)
    parent_contract = ProblemContract.from_question("Please solve this in English.", workflow_mode="standard")
    parent_contract.save(parent_dir)

    def _fake_get_task(task_id: str):
        if task_id == "parent-1":
            return {"work_dir": str(parent_dir)}
        return None

    monkeypatch.setattr("app.core.workflow.task_manager.get_task", _fake_get_task)

    # question intentionally misleading Chinese; should still inherit English from parent
    contract = _resolve_polish_contract(
        task={"parent_task_id": "parent-1"},
        workflow_mode="strict",
        question="请输出中文图表",
        paper_content="正文是中文也不应覆盖父任务已锁定语言",
    )

    assert contract.chart_language == "English"
    assert contract.document_language == "English"
    assert contract.workflow_mode == "strict"


def test_artifact_registry_reports_bare_string_generated_file_rejection(tmp_path: Path) -> None:

    def test_artifact_registry_compat_alias_artifact_valid_images(tmp_path: Path) -> None:
        """Registry should expose artifact_valid_images and keep legacy paper_ready_images in sync."""
        from app.artifacts.registry import ArtifactRegistry

        _write_png(tmp_path / "output" / "ok.png")
        manifest = [
            {
                "path": "output/ok.png",
                "kind": "figure",
                "paper_ready": True,
                "visible_text_language": "Simplified Chinese",
                "chart_language_verified": True,
                "visible_text_audit": ["标题", "横轴", "纵轴"],
                "created_by": "save_paper_figure",
                "helper_version": 3,
                "is_placeholder": False,
                "linked_result_ids": ["r1"],
                "source_data": ["data.csv"],
            }
        ]
        (tmp_path / "figure_artifacts.json").write_text(json.dumps(manifest), encoding="utf-8")

        registry = {
            "verified_results": [{"id": "r1", "status": "verified", "value": 1}],
            "blocked_results": [],
            "summary": {"verified_count": 1, "blocked_count": 0},
        }
        (tmp_path / "result_registry.json").write_text(json.dumps(registry), encoding="utf-8")

        art_reg = ArtifactRegistry.load(str(tmp_path))
        valid = art_reg.validate_for_paper()

        assert valid == ["output/ok.png"]
        assert art_reg.artifact_valid_images == ["output/ok.png"]
        assert art_reg.paper_ready_images == art_reg.artifact_valid_images
    """Bare string generated_files image paths should be recorded in rejection report."""
    from app.artifacts.registry import ArtifactRegistry

    _write_png(tmp_path / "output" / "bare.png")
    structured = {
        "section": "solve",
        "summary": "ok",
        "key_results": [
            {
                "id": "r1",
                "name": "厚度",
                "value": 1,
                "unit": "mm",
                "formula": "x=1",
                "inputs": {},
                "source_data": ["data.csv"],
                "code_cell": "1",
                "evidence": "ok",
                "source": "test",
                "verified": True,
                "status": "verified",
                "warnings": [],
            }
        ],
        "generated_files": ["output/bare.png"],
        "warnings": [],
    }
    (tmp_path / "solve_structured_results.json").write_text(
        json.dumps(structured, ensure_ascii=False), encoding="utf-8"
    )

    registry = ArtifactRegistry.load(str(tmp_path), structured_result_files=["solve_structured_results.json"])
    registry.validate_for_paper()

    reasons_by_path = {r.path: r.rejected_by for r in registry.figure_rejections}
    assert "output/bare.png" in reasons_by_path
    assert "generated_file_not_object" in reasons_by_path["output/bare.png"]


def test_ensure_figure_requests_from_solve_spec() -> None:
    """Workflow should derive required figure_requests from visual subproblems."""
    solve_spec = {
        "subproblems": [
            {
                "id": "problem_1",
                "objective": "绘制导弹轨迹与烟幕遮蔽示意图",
                "steps": ["计算轨迹", "输出图表"],
            },
            {
                "id": "problem_2",
                "objective": "仅输出最优参数数值",
                "steps": ["拟合", "输出 key_results"],
            },
        ]
    }

    requests = FigureStage.ensure_figure_requests_in_solve_spec(solve_spec, "Simplified Chinese")
    assert requests
    req_ids = {item["id"] for item in requests}
    assert "fig_problem_1_geometry_schematic" in req_ids
    assert "fig_problem_1_trajectory_overview" in req_ids

    geometry = next(item for item in requests if item["id"] == "fig_problem_1_geometry_schematic")
    assert geometry["required"] is True
    assert geometry["figure_kind"] == "explanatory_figure"
    assert geometry["must_include_in_paper"] is True
    assert geometry["language"] == "Simplified Chinese"


def test_solve_spec_expect_flags_support_explanatory_only() -> None:
    """requires_explanatory_figures=true should make task expected_figures=true."""
    from app.artifacts.contracts import ProblemContract

    solve_spec = {
        "requires_result_figures": False,
        "requires_explanatory_figures": True,
        "requires_figures": True,
    }
    contract = ProblemContract.from_question("", solve_spec=solve_spec)
    assert contract.paper_requires_figures is True


def test_solve_spec_expects_figures_prefers_by_problem_flag() -> None:
    from app.artifacts.contracts import ProblemContract

    solve_spec = {
        "requires_figures_by_problem": False,
        "requires_result_figures": True,
        "requires_explanatory_figures": True,
    }
    contract = ProblemContract.from_question("figure", solve_spec=solve_spec)
    assert contract.paper_requires_figures is False


def test_semantic_bundle_excludes_diagnostic_summary_for_required_requests(tmp_path: Path) -> None:
    """verified_result_summary should stay in diagnostics and not satisfy required requests."""
    from app.artifacts.registry import ArtifactRegistry

    _write_png(tmp_path / "output" / "verified_result_summary.png")
    manifest = {
        "figures": [
            {
                "path": "output/verified_result_summary.png",
                "kind": "figure",
                "paper_ready": True,
                "visible_text_language": "Simplified Chinese",
                "chart_language_verified": True,
                "visible_text_audit": ["已验证结果摘要", "结果序号", "数值"],
                "created_by": "deterministic_renderer",
                "helper_version": 3,
                "is_placeholder": False,
                "figure_role": "verified_result_summary",
                "linked_result_ids": ["r1"],
                "source_data": ["result_registry.json"],
                "paper_section": "diagnostics",
                "semantic_verified": False,
                "figure_request_id": "diagnostic_verified_result_summary",
                "subproblem_id": "diagnostics",
                "semantic_role": "verified_result_summary",
            }
        ]
    }
    (tmp_path / "figure_artifacts.json").write_text(json.dumps(manifest), encoding="utf-8")

    structured = {
        "section": "solve",
        "summary": "ok",
        "key_results": [
            {
                "id": "r1", "name": "厚度", "value": 1, "unit": "mm", "formula": "x=1",
                "inputs": {}, "source_data": ["data.csv"], "code_cell": "1", "evidence": "ok",
                "source": "solver", "verified": True, "status": "verified", "warnings": []
            }
        ],
        "generated_files": [],
        "warnings": [],
    }
    (tmp_path / "solve_structured_results.json").write_text(json.dumps(structured), encoding="utf-8")

    solve_spec = {
        "figure_requests": [
            {
                "id": "problem_1_trajectory_shielding",
                "required": True,
                "subproblem_id": "problem_1",
                "role": "trajectory_shielding",
                "language": "Simplified Chinese",
            }
        ]
    }
    (tmp_path / "solve_spec.json").write_text(json.dumps(solve_spec, ensure_ascii=False), encoding="utf-8")

    reg = ArtifactRegistry.load(str(tmp_path), structured_result_files=["solve_structured_results.json"])
    reg.validate_for_paper()
    bundle = reg.build_figure_bundle()

    assert len(bundle.required_requests) == 1
    assert len(bundle.available_figures) == 0
    assert len(bundle.explanatory_figures) == 0
    assert len(bundle.result_figures) == 0
    assert len(bundle.diagnostic_figures) == 1
    assert bundle.required_images == []
    assert bundle.missing_requests[0]["figure_request_id"] == "problem_1_trajectory_shielding"


def test_semantic_bundle_tracks_required_explanatory_images(tmp_path: Path) -> None:
    """must_include_in_paper explanatory request should appear in required_images."""
    from app.artifacts.registry import ArtifactRegistry

    _write_png(tmp_path / "output" / "fig_problem_1_geometry_schematic.png")
    manifest = {
        "figures": [
            {
                "path": "output/fig_problem_1_geometry_schematic.png",
                "kind": "figure",
                "paper_ready": True,
                "visible_text_language": "Simplified Chinese",
                "chart_language_verified": True,
                "visible_text_audit": ["几何关系示意图", "导弹", "目标", "烟幕"],
                "created_by": "deterministic_renderer",
                "helper_version": 3,
                "is_placeholder": False,
                "figure_role": "geometry_schematic",
                "figure_kind": "explanatory_figure",
                "linked_result_ids": ["r1"],
                "source_data": ["result_registry.json"],
                "semantic_verified": True,
                "figure_request_id": "fig_problem_1_geometry_schematic",
                "subproblem_id": "problem_1",
                "semantic_role": "geometry_schematic",
            }
        ]
    }
    (tmp_path / "figure_artifacts.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    structured = {
        "section": "solve",
        "summary": "ok",
        "key_results": [
            {
                "id": "r1", "name": "遮蔽时长", "value": 1, "unit": "s", "formula": "x=1",
                "inputs": {}, "source_data": ["result_registry.json"], "code_cell": "1", "evidence": "ok",
                "source": "solver", "verified": True, "status": "verified", "warnings": []
            }
        ],
        "generated_files": [],
        "warnings": [],
    }
    (tmp_path / "solve_structured_results.json").write_text(json.dumps(structured, ensure_ascii=False), encoding="utf-8")

    solve_spec = {
        "figure_requests": [
            {
                "id": "fig_problem_1_geometry_schematic",
                "required": True,
                "must_include_in_paper": True,
                "figure_kind": "explanatory_figure",
            }
        ]
    }
    (tmp_path / "solve_spec.json").write_text(json.dumps(solve_spec, ensure_ascii=False), encoding="utf-8")

    reg = ArtifactRegistry.load(str(tmp_path), structured_result_files=["solve_structured_results.json"])
    reg.validate_for_paper()
    bundle = reg.build_figure_bundle()

    assert bundle.required_images == ["output/fig_problem_1_geometry_schematic.png"]
    assert len(bundle.explanatory_figures) == 1


def test_render_distance_time_curve_semantic_artifact(tmp_path: Path) -> None:
    """distance_time_curve renderer should output semantic-verified artifact."""
    from app.artifacts.renderers import render_distance_time_curve

    artifact = render_distance_time_curve(
        times=[1, 2, 3, 4],
        distances=[10, 9, 8, 7],
        title="距离-时间曲线",
        output_path=str(tmp_path / "output" / "p1_distance_time.png"),
        chart_language="Simplified Chinese",
        figure_request_id="p1_distance_time_curve",
        subproblem_id="problem_1",
        linked_result_ids=["r1", "r2"],
        source_data=["result_registry.json"],
        work_dir=str(tmp_path),
    )

    assert artifact["path"] == "output/p1_distance_time.png"
    assert artifact["semantic_verified"] is True
    assert artifact["figure_request_id"] == "p1_distance_time_curve"
    assert artifact["semantic_role"] == "distance_time_curve"


def test_render_required_requests_deterministically(tmp_path: Path) -> None:
    """Workflow deterministic-first recovery should satisfy supported requests with data."""
    # required_data file must exist for deterministic attempt
    (tmp_path / "distance_time_series.csv").write_text("t,d\n1,1\n2,2\n", encoding="utf-8")

    registry = {
        "verified_results": [
            {"id": "r1", "value": 1.0, "source_data": ["distance_time_series.csv"]},
            {"id": "r2", "value": 2.0, "source_data": ["distance_time_series.csv"]},
            {"id": "r3", "value": 3.0, "source_data": ["distance_time_series.csv"]},
        ]
    }
    required_requests = [
        {
            "id": "problem_1_distance",
            "subproblem_id": "problem_1",
            "required": True,
            "role": "distance_time_curve",
            "expected_content": ["距离-时间曲线"],
            "required_data": ["distance_time_series.csv"],
        }
    ]

    report = FigureStage.render_required_requests_deterministically(
        work_dir=str(tmp_path),
        result_registry=registry,
        required_requests=required_requests,
        chart_language="Simplified Chinese",
    )

    assert report["created_images"]
    assert report["satisfied"][0]["figure_request_id"] == "problem_1_distance"
    assert report["missing"] == []


def test_render_required_requests_rejects_weak_required_data_contract(tmp_path: Path) -> None:
    """Deterministic renderer must not semantic-verify requests with weak required_data contract."""
    (tmp_path / "result_registry.json").write_text("{}", encoding="utf-8")
    registry = {
        "verified_results": [
            {"id": "r1", "value": 1.0, "source_data": ["result_registry.json"]},
            {"id": "r2", "value": 2.0, "source_data": ["result_registry.json"]},
        ]
    }
    required_requests = [
        {
            "id": "problem_1_trajectory",
            "subproblem_id": "problem_1",
            "required": True,
            "role": "trajectory_shielding",
            "expected_content": ["轨迹遮蔽图"],
            "required_data": ["result_registry.json"],
        }
    ]

    report = FigureStage.render_required_requests_deterministically(
        work_dir=str(tmp_path),
        result_registry=registry,
        required_requests=required_requests,
        chart_language="Simplified Chinese",
    )

    assert report["created_images"] == []
    assert report["satisfied"] == []
    assert report["missing"]
    assert report["missing"][0]["reason"] == "insufficient_semantic_required_data_contract"


def test_build_failed_task_result_with_failure_report_paths(tmp_path: Path) -> None:
    """Failure result builder should carry failure report paths without undefined vars."""
    from app.core.workflow import _build_failed_task_result

    md_path = str(tmp_path / "failure_report.md")
    docx_path = str(tmp_path / "failure_report.docx")
    notebook_path = str(tmp_path / "notebook.ipynb")

    payload = _build_failed_task_result(
        task_id="t1",
        task_type="writing",
        work_dir=str(tmp_path),
        error_message="verified_count=0",
        error_code="QUALITY_GATE_FAILED",
        error_type="quality_gate",
        error_details={"gate_reason": "verified_count=0"},
        paper_path=md_path,
        docx_path=docx_path,
        notebook_path=notebook_path,
    )

    assert payload["type"] == "result"
    data = payload["data"]
    assert data["status"] == "failed"
    assert data["paper_path"] == md_path
    assert data["docx_path"] == docx_path
    assert data["notebook_path"] == notebook_path
    assert data["error_code"] == "QUALITY_GATE_FAILED"
    assert data["error_type"] == "quality_gate"
    assert data["error_details"] == {"gate_reason": "verified_count=0"}


def test_classify_failure_maps_finalizer_error() -> None:
    from app.artifacts.exporters import FinalizationValidationError
    from app.core.workflow import _classify_failure

    exc = FinalizationValidationError(
        "missing image",
        error_code="FINALIZER_MISSING_IMAGE_REF",
        details={"paths": ["output/a.png"]},
    )
    message, code, failure_type, details = _classify_failure(exc)

    assert message == "missing image"
    assert code == "FINALIZER_MISSING_IMAGE_REF"
    assert failure_type == "finalizer_validation"
    assert details == {"paths": ["output/a.png"]}


def test_figure_plan_builder_backfills_optical_domain_requests(tmp_path: Path) -> None:
    """FigurePlan should backfill optical interference requests even when solve_spec requests are empty."""
    from app.artifacts.figure_plan import FigurePlanBuilder

    data_dir = tmp_path / "inputs" / "question"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "附件1.xlsx").write_text("placeholder", encoding="utf-8")

    solve_spec = {
        "subproblems": [
            {
                "id": "p1",
                "objective": "基于双光束干涉模型推导厚度公式",
                "method": "干涉模型 + 光谱峰谷识别",
                "input_files": ["附件1.xlsx"],
                "steps": ["读取光谱", "寻找峰谷", "计算厚度"],
                "expected_outputs": ["厚度"],
            }
        ],
        "figure_requests": [],
    }
    plan = FigurePlanBuilder.build(
        question="碳化硅外延层厚度计算，考虑干涉光程差、波数与反射率峰谷",
        solve_spec=solve_spec,
        problem_facts={"domain": "optical"},
        chart_language="Simplified Chinese",
        work_dir=str(tmp_path),
    )

    assert plan["requires_figures"] is True
    assert plan["requires_explanatory_figures"] is True
    assert plan["requires_result_figures"] is True
    req_ids = {item["id"] for item in plan.get("figure_requests", [])}
    assert "fig_p1_optical_path_diagram" in req_ids
    assert "fig_p1_spectrum_curve" in req_ids
    assert "fig_p1_peak_valley_annotation" in req_ids
    assert "fig_p1_algorithm_flowchart" in req_ids

    spectrum_req = next(item for item in plan.get("figure_requests", []) if item.get("id") == "fig_p1_spectrum_curve")
    data_files = spectrum_req.get("depends_on", {}).get("data_files", [])
    assert any("inputs/question/附件1.xlsx" == str(item).replace("\\", "/") for item in data_files)


def test_figure_plan_emits_workflow_issues_for_blocked_requests(tmp_path: Path) -> None:
    from app.artifacts.figure_plan import FigurePlanBuilder

    solve_spec = {
        "subproblems": [{"id": "q1", "objective": "拟合分析", "steps": ["fit"]}],
        "figure_requests": [],
    }
    plan = FigurePlanBuilder.build(
        question="拟合与残差分析图",
        solve_spec=solve_spec,
        problem_facts={},
        chart_language="Simplified Chinese",
        work_dir=str(tmp_path),
    )
    issues = plan.get("workflow_issues", [])
    assert isinstance(issues, list)
    assert any(item.get("code") == "FIGURE_REQUEST_BLOCKED" for item in issues if isinstance(item, dict))


def test_apply_figure_plan_keeps_plan_owned_counts_out_of_solve_spec() -> None:
    solve_spec = {
        "requires_figures": False,
        "requires_result_figures": False,
        "requires_explanatory_figures": False,
        "expected_figures": False,
        "required_feasible_count": 99,
        "blocked_count": 99,
    }
    figure_plan = {
        "requires_figures": True,
        "requires_result_figures": True,
        "requires_explanatory_figures": True,
        "requires_figures_by_problem": True,
        "required_feasible_count": 0,
        "blocked_count": 3,
    }
    FigureStage.apply_figure_plan_to_solve_spec(solve_spec, figure_plan)

    assert solve_spec["requires_figures"] is True
    assert solve_spec["requires_result_figures"] is True
    assert solve_spec["requires_explanatory_figures"] is True
    assert solve_spec["requires_figures_by_problem"] is True
    assert "expected_figures" not in solve_spec
    assert "required_feasible_count" not in solve_spec
    assert "blocked_count" not in solve_spec


def test_render_spectrum_request_from_csv_data_file(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "spectrum.csv").write_text(
        "wavenumber,reflectance\n1000,0.2\n1010,0.25\n1020,0.23\n",
        encoding="utf-8",
    )

    report = FigureStage.render_required_requests_deterministically(
        work_dir=str(tmp_path),
        result_registry={"verified_results": []},
        required_requests=[
            {
                "id": "fig_p1_spectrum_curve",
                "subproblem_id": "p1",
                "required": True,
                "figure_kind": "result_figure",
                "semantic_role": "spectrum_curve",
                "expected_content": ["反射率-波数光谱曲线"],
                "required_data": ["data/spectrum.csv"],
            }
        ],
        chart_language="Simplified Chinese",
    )

    assert report["created_images"]
    assert report["satisfied"]


def test_render_spectrum_request_blocks_semantic_data_mismatch(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "nipt_table.csv").write_text(
        "孕周数,孕妇BMI,Z值\n12,23.1,2.8\n13,24.0,3.1\n14,22.5,2.4\n",
        encoding="utf-8",
    )

    report = FigureStage.render_required_requests_deterministically(
        work_dir=str(tmp_path),
        result_registry={"verified_results": []},
        required_requests=[
            {
                "id": "fig_p2_spectrum_curve",
                "subproblem_id": "p2",
                "required": True,
                "figure_kind": "result_figure",
                "semantic_role": "spectrum_curve",
                "expected_content": ["光谱曲线"],
                "required_data": ["data/nipt_table.csv"],
            }
        ],
        chart_language="Simplified Chinese",
    )

    assert report["created_images"] == []
    assert report["satisfied"] == []
    assert any(
        str(item.get("reason") or "") == "semantic_data_mismatch:spectrum_curve"
        for item in report.get("missing", [])
        if isinstance(item, dict)
    )


def test_figure_plan_general_fit_does_not_default_to_spectrum(tmp_path: Path) -> None:
    from app.artifacts.figure_plan import FigurePlanBuilder

    solve_spec = {
        "subproblems": [
            {
                "id": "p1",
                "objective": "建立回归模型并分析拟合残差",
                "method": "多变量回归拟合",
                "input_files": ["data/train.csv"],
                "steps": ["拟合", "残差分析"],
                "expected_outputs": ["回归系数"],
            }
        ],
        "figure_requests": [],
    }
    plan = FigurePlanBuilder.build(
        question="请基于训练数据建立回归模型并分析误差",
        solve_spec=solve_spec,
        problem_facts={},
        chart_language="Simplified Chinese",
        work_dir=str(tmp_path),
    )

    req_roles = {
        str(item.get("semantic_role") or "")
        for item in plan.get("figure_requests", [])
        if isinstance(item, dict)
    }
    assert "spectrum_curve" not in req_roles
    assert "comparison_bar" in req_roles or "fitting_curve" in req_roles


def test_figure_plan_blocks_placeholder_linked_result_ids(tmp_path: Path) -> None:
    from app.artifacts.figure_plan import FigurePlanBuilder

    solve_spec = {
        "subproblems": [{"id": "q1", "objective": "距离-时间曲线", "input_files": []}],
        "figure_requests": [
            {
                "id": "fig_q1_fit",
                "subproblem_id": "q1",
                "figure_kind": "result_figure",
                "semantic_role": "distance_time_curve",
                "required": True,
                "required_data": ["distance_time_series.csv"],
                "linked_result_ids": ["verified_series"],
                "depends_on": {"data_files": ["distance_time_series.csv"], "result_ids": ["verified_series"]},
            }
        ],
    }

    plan = FigurePlanBuilder.build(
        question="做拟合并输出曲线",
        solve_spec=solve_spec,
        problem_facts={},
        chart_language="Simplified Chinese",
        work_dir=str(tmp_path),
    )

    req = plan["figure_requests"][0]
    assert req["status"] == "blocked"
    assert req["required"] is False
    assert req["blocked_reason"] == "placeholder_linked_result_ids"


def test_figure_plan_keeps_problem_need_when_all_requests_blocked(tmp_path: Path) -> None:
    from app.artifacts.figure_plan import FigurePlanBuilder

    solve_spec = {
        "subproblems": [
            {
                "id": "q1",
                "objective": "拟合与残差分析",
                "method": "fit residual",
                "steps": ["fit", "residual"],
                "expected_outputs": ["拟合参数"],
            }
        ],
        "figure_requests": [],
    }

    plan = FigurePlanBuilder.build(
        question="请进行拟合并给出残差分析图",
        solve_spec=solve_spec,
        problem_facts={},
        chart_language="Simplified Chinese",
        work_dir=str(tmp_path),
    )

    assert plan["requires_figures"] is True
    assert plan["requires_figures_by_problem"] is True
    assert plan["required_feasible_count"] == 0
    assert plan["blocked_count"] > 0


def test_figure_plan_pending_result_binding_not_required(tmp_path: Path) -> None:
    from app.artifacts.figure_plan import FigurePlanBuilder

    solve_spec = {
        "subproblems": [{"id": "q1", "objective": "轨迹遮蔽分析"}],
        "figure_requests": [
            {
                "id": "fig_q1_traj",
                "subproblem_id": "q1",
                "figure_kind": "result_figure",
                "semantic_role": "trajectory_shielding",
                "required": True,
                "required_data": ["missile_trajectory.json", "cloud_center_trajectory.json", "shielding_intervals.json"],
                "linked_result_ids": ["q1_traj_result"],
                "depends_on": {
                    "data_files": ["missile_trajectory.json", "cloud_center_trajectory.json", "shielding_intervals.json"],
                    "result_ids": ["q1_traj_result"],
                },
            }
        ],
    }

    plan = FigurePlanBuilder.build(
        question="轨迹遮蔽图",
        solve_spec=solve_spec,
        problem_facts={},
        chart_language="Simplified Chinese",
        work_dir=str(tmp_path),
    )
    req = plan["figure_requests"][0]
    assert req["status"] == "blocked"
    assert req["required"] is False
    assert req["blocked_reason"] == "pending_result_binding"
    assert req["result_binding_status"] == "pending_result_binding"


def test_figure_plan_injects_data_overview_fallback_when_advanced_blocked(tmp_path: Path) -> None:
    from app.artifacts.figure_plan import FigurePlanBuilder

    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "nipt.csv").write_text(
        "孕周数,孕妇BMI,Z值\n12,23.1,2.8\n13,24.0,3.1\n14,22.5,2.4\n",
        encoding="utf-8",
    )

    solve_spec = {
        "subproblems": [
            {
                "id": "p1",
                "objective": "建立回归模型并分析拟合残差",
                "method": "多变量回归拟合",
                "input_files": ["data/nipt.csv"],
                "steps": ["拟合", "残差分析"],
                "expected_outputs": ["回归参数"],
            }
        ],
        "figure_requests": [],
    }

    plan = FigurePlanBuilder.build(
        question="请建立回归模型并给出结果图",
        solve_spec=solve_spec,
        problem_facts={},
        chart_language="Simplified Chinese",
        work_dir=str(tmp_path),
    )

    fallback = [
        item for item in plan.get("figure_requests", [])
        if isinstance(item, dict) and str(item.get("semantic_role") or "") == "data_overview"
    ]
    assert fallback
    assert any(bool(item.get("required", False)) for item in fallback)


def test_render_data_overview_request_from_numeric_csv(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "overview.csv").write_text(
        "week,bmi,zscore\n12,23.1,2.8\n13,24.0,3.1\n14,22.5,2.4\n",
        encoding="utf-8",
    )

    report = FigureStage.render_required_requests_deterministically(
        work_dir=str(tmp_path),
        result_registry={"verified_results": []},
        required_requests=[
            {
                "id": "fig_p1_data_overview",
                "subproblem_id": "p1",
                "required": True,
                "figure_kind": "result_figure",
                "semantic_role": "data_overview",
                "expected_content": ["核心数值变量分布概览"],
                "required_data": ["data/overview.csv"],
                "view_type": "numeric_overview",
            }
        ],
        chart_language="Simplified Chinese",
    )

    assert report["created_images"]
    assert report["satisfied"]


def test_quality_gate_reports_blocked_required_by_problem() -> None:
    registry = {
        "verified_results": [{"id": "r1", "status": "verified"}],
        "blocked_results": [],
        "summary": {"verified_count": 1, "blocked_count": 0},
    }
    passed, reason = FigureStage.quality_gate_before_writing(
        registry,
        True,
        [],
        [],
        [],
        "standard",
        requires_figures_by_problem=True,
        blocked_requests=[
            {
                "id": "fig_q1_fit",
                "status": "blocked",
                "blocked_reason": "unsupported_renderer:fitting_curve",
            }
        ],
    )
    assert passed is False
    assert "blocked_required_by_problem_count" in reason


def test_render_fitting_request_from_csv_data_file(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "fit.csv").write_text(
        "x,observed,fitted\n1,1.0,0.9\n2,2.1,2.0\n3,3.05,3.0\n",
        encoding="utf-8",
    )

    report = FigureStage.render_required_requests_deterministically(
        work_dir=str(tmp_path),
        result_registry={"verified_results": [{"id": "q1_fit_result", "value": 1.0}]},
        required_requests=[
            {
                "id": "fig_q1_fitting_curve",
                "subproblem_id": "q1",
                "required": True,
                "figure_kind": "result_figure",
                "semantic_role": "fitting_curve",
                "expected_content": ["拟合曲线"],
                "required_data": ["data/fit.csv"],
            }
        ],
        chart_language="Simplified Chinese",
    )

    assert report["created_images"]
    assert report["satisfied"]


def test_figure_plan_reevaluate_promotes_pending_binding_after_solver(tmp_path: Path) -> None:
    from app.artifacts.figure_plan import FigurePlanBuilder

    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "fit.csv").write_text(
        "x,observed,fitted\n1,1.0,0.9\n2,2.1,2.0\n",
        encoding="utf-8",
    )
    (tmp_path / "result_registry.json").write_text(
        json.dumps(
            {
                "verified_results": [{"id": "q1_fit_result", "value": 1.23}],
                "blocked_results": [],
                "summary": {"verified_count": 1, "blocked_count": 0},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    requests = [
        {
            "id": "fig_q1_fitting_curve",
            "subproblem_id": "q1",
            "figure_kind": "result_figure",
            "semantic_role": "fitting_curve",
            "required": True,
            "status": "blocked",
            "blocked_reason": "pending_result_binding",
            "required_data": ["data/fit.csv"],
            "linked_result_ids": ["q1_fit_result"],
            "depends_on": {
                "data_files": ["data/fit.csv"],
                "result_ids": ["q1_fit_result"],
            },
        }
    ]

    reevaluated = FigurePlanBuilder.reevaluate_requests(
        requests=requests,
        chart_language="Simplified Chinese",
        work_dir=str(tmp_path),
    )

    req = reevaluated[0]
    assert req["required"] is True
    assert req["status"] == "required"
    assert req["result_binding_status"] == "verified_result_bound"


def test_figure_plan_reevaluate_auto_binds_verified_results_when_ids_missing(tmp_path: Path) -> None:
    from app.artifacts.figure_plan import FigurePlanBuilder

    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "summary.csv").write_text(
        "method,metric,value\nA,risk,1.2\nB,risk,0.8\n",
        encoding="utf-8",
    )
    (tmp_path / "result_registry.json").write_text(
        json.dumps(
            {
                "verified_results": [{"id": "q1_comparison_result", "value": 0.8}],
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
                "linked_result_ids": [],
                "depends_on": {"data_files": ["data/summary.csv"], "result_ids": []},
            }
        ],
        chart_language="Simplified Chinese",
        work_dir=str(tmp_path),
    )

    req = reevaluated[0]
    assert req["required"] is True
    assert req["status"] == "required"
    assert req["linked_result_ids"] == ["q1_comparison_result"]
    assert req["result_binding_status"] == "verified_result_bound"
    assert req["auto_bound_verified_results"] is True


def test_figure_plan_blocks_comparison_when_required_columns_missing(tmp_path: Path) -> None:
    from app.artifacts.figure_plan import FigurePlanBuilder

    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "nipt.csv").write_text(
        "孕周数,孕妇BMI,Y染色体浓度\n12,28.1,0.04\n13,29.0,0.05\n",
        encoding="utf-8",
    )
    (tmp_path / "result_registry.json").write_text(
        json.dumps(
            {
                "verified_results": [{"id": "q1_result", "value": 1.0}],
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
                "required_data": ["data/nipt.csv"],
                "required_columns": ["method", "metric", "value"],
                "linked_result_ids": [],
                "depends_on": {"data_files": ["data/nipt.csv"], "result_ids": []},
            }
        ],
        chart_language="Simplified Chinese",
        work_dir=str(tmp_path),
    )

    req = reevaluated[0]
    assert req["required"] is False
    assert req["status"] == "blocked"
    assert req["blocked_reason"] == "required_columns_missing:comparison_bar"


def test_required_figure_requests_filters_blocked_and_infeasible() -> None:
    solve_spec = {
        "figure_requests": [
            {"id": "a", "required": True, "status": "required", "feasible": True},
            {"id": "b", "required": True, "status": "blocked", "feasible": False},
            {"id": "c", "required": True, "status": "required", "feasible": False},
            {"id": "d", "required": False, "status": "recommended", "feasible": True},
        ]
    }

    required = FigureStage.required_figure_requests(solve_spec)
    assert [item["id"] for item in required] == ["a"]


def test_figure_data_protocol_contract_includes_phase2_roles() -> None:
    contract = FigureStage.figure_data_protocol_contract(
        [
            {
                "id": "fig_q1_fit",
                "semantic_role": "fitting_curve",
                "required_data": ["data/q1_observed_vs_fitted.csv"],
                "linked_result_ids": ["q1_fitting_result"],
            },
            {
                "id": "fig_q1_res",
                "semantic_role": "residual_plot",
                "required_data": ["data/q1_residuals.csv"],
                "linked_result_ids": ["q1_residual_result"],
            },
            {
                "id": "fig_q1_cmp",
                "semantic_role": "comparison_bar",
                "required_data": ["data/q1_method_comparison.csv"],
                "linked_result_ids": ["q1_comparison_result"],
            },
        ]
    )

    assert "fitting_curve" in contract
    assert "x, observed, fitted" in contract
    assert "residual_plot" in contract
    assert "x, residual" in contract
    assert "comparison_bar" in contract
    assert "method, metric, value" in contract


def test_missing_reason_summary_groups_phase2_column_missing() -> None:
    summary = FigureStage.missing_reason_summary(
        [
            {"reason": "missing_fitting_columns"},
            {"reason": "missing_residual_columns"},
            {"reason": "missing_comparison_columns"},
        ]
    )
    assert summary["missing_source_data"] == 3


def test_figure_stage_workflow_issue_summary_counts() -> None:
    from app.core.figure_stage import FigureStage

    issues = FigureStage.normalize_workflow_issues(
        {
            "workflow_issues": [
                {
                    "code": "FIGURE_REQUEST_BLOCKED",
                    "severity": "warning",
                    "stage": "figure_plan",
                    "reason": "missing_source_data",
                    "request_id": "fig_1",
                },
                {
                    "code": "FIGURE_REQUEST_BLOCKED",
                    "severity": "warning",
                    "stage": "figure_plan",
                    "reason": "missing_source_data",
                    "request_id": "fig_2",
                },
            ]
        }
    )
    summary = FigureStage.workflow_issue_summary(issues)

    assert summary["count"] == 2
    assert summary["by_code"].get("FIGURE_REQUEST_BLOCKED") == 2
    assert summary["by_stage"].get("figure_plan") == 2
    assert summary["top_reasons"].get("missing_source_data") == 2


def test_explanatory_figure_passes_without_linked_result_ids(tmp_path: Path) -> None:
    """Explanatory figure should be validated by model_contract evidence, not linked_result_ids."""
    _write_png(tmp_path / "output" / "geometry.png")
    artifact = {
        "path": "output/geometry.png",
        "kind": "figure",
        "paper_ready": True,
        "visible_text_language": "Simplified Chinese",
        "chart_language_verified": True,
        "visible_text_audit": ["几何关系示意图", "导弹", "目标"],
        "created_by": "deterministic_renderer",
        "helper_version": 3,
        "is_placeholder": False,
        "figure_kind": "explanatory_figure",
        "semantic_role": "geometry_schematic",
        "evidence_binding_type": "model_contract",
        "model_objects": ["missile", "target", "smoke_cloud"],
        "formulas": ["2ndcos(theta)=mλ"],
        "source_problem_refs": ["problem_1"],
    }
    assert is_verified_paper_figure(artifact, "Simplified Chinese", tmp_path, {"r1"})
