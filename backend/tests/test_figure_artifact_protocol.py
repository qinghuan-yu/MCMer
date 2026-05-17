import base64
import json
import zipfile
from pathlib import Path

from app.core.agents.coder_agent import CoderAgent
from app.core.workflow import _generated_image_evidence_paths, _has_generated_image_evidence, _language_verified_generated_images
from app.utils.common_utils import finalize_markdown_export
from app.utils.figure_artifacts import is_verified_paper_figure


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
