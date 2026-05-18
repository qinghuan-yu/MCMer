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

    language_message = agent._chart_language_guard_message(
        "import matplotlib.pyplot as plt\nplt.title('Result')\nsave_paper_figure(plt.gcf(), 'final.png', visible_text_audit=['title'])",
        "Simplified Chinese",
    )
    assert "Simplified Chinese" in language_message

    reverse_message = agent._chart_language_guard_message(
        "import matplotlib.pyplot as plt\nplt.title('结果')\nsave_paper_figure(plt.gcf(), 'final.png', visible_text_audit=['title'])",
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
    }
    assert is_verified_paper_figure(artifact, "Simplified Chinese", tmp_path)


def test_old_artifact_without_created_by_still_passes(tmp_path: Path) -> None:
    """Backward compatibility: old artifacts without helper_version pass."""
    _write_png(tmp_path / "output" / "old_figure.png")
    artifact = {
        "path": "output/old_figure.png",
        "kind": "figure",
        "paper_ready": True,
        "visible_text_language": "Simplified Chinese",
        "chart_language_verified": True,
        "visible_text_audit": ["标题", "横轴"],
    }
    assert is_verified_paper_figure(artifact, "Simplified Chinese", tmp_path)


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
