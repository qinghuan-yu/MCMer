import asyncio
import zipfile
from pathlib import Path

from app.core.workflow import _build_completed_task_result
from app.schemas.response import TaskResult


def test_guidance_completed_result_exposes_guidance_path_and_legacy_paper_path() -> None:
    payload = _build_completed_task_result(
        task_id="task-1",
        task_type="writing",
        work_dir="work/task-1",
        paper_path="work/task-1/guidance.md",
        docx_path="work/task-1/guidance.docx",
        notebook_path="work/task-1/notebook.ipynb",
        primary_artifact_type="guidance",
        audit_status="BLOCK",
        audit_summary="parameter provenance is incomplete",
        audit_blocks=[{"type": "missing_parameter_registry_coverage"}],
    )

    data = payload["data"]
    assert data["primary_artifact_type"] == "guidance"
    assert data["markdown_path"] == "work/task-1/guidance.md"
    assert data["guidance_path"] == "work/task-1/guidance.md"
    assert data["paper_path"] == data["guidance_path"]
    assert data["docx_path"] == ""
    assert data["audit_status"] == "BLOCK"
    assert data["audit_summary"] == "parameter provenance is incomplete"
    assert data["audit_blocks"] == [{"type": "missing_parameter_registry_coverage"}]


def test_task_result_defaults_to_legacy_paper_artifact() -> None:
    data = TaskResult(
        task_id="task-2",
        status="completed",
        paper_path="work/task-2/res.md",
    ).model_dump()

    assert data["primary_artifact_type"] == "paper"
    assert data["markdown_path"] == "work/task-2/res.md"
    assert data["guidance_path"] == ""
    assert data["paper_path"] == "work/task-2/res.md"


def test_guidance_download_returns_markdown_attachment(tmp_path: Path, monkeypatch) -> None:
    from app.api import routes

    guidance_path = tmp_path / "guidance.md"
    guidance_path.write_text("# trusted guidance\n", encoding="utf-8")
    monkeypatch.setattr(
        routes.task_manager,
        "get_task",
        lambda task_id: {"task_id": task_id, "work_dir": str(tmp_path)},
    )

    response = asyncio.run(routes.download_guidance("task-1"))

    assert Path(response.path) == guidance_path
    assert response.media_type == "text/markdown"
    assert response.filename == "guidance.md"


def test_workspace_download_returns_zip_with_task_artifacts(tmp_path: Path, monkeypatch) -> None:
    from app.api import routes

    (tmp_path / "guidance.md").write_text("# trusted guidance\n", encoding="utf-8")
    (tmp_path / "parameter_registry.json").write_text('{"parameters":[]}', encoding="utf-8")
    (tmp_path / "workspace.zip").write_text("old bundle", encoding="utf-8")
    monkeypatch.setattr(
        routes.task_manager,
        "get_task",
        lambda task_id: {"task_id": task_id, "work_dir": str(tmp_path)},
    )

    response = asyncio.run(routes.download_workspace("task-zip"))

    assert response.media_type == "application/zip"
    assert response.filename == "task-zip-workspace.zip"
    with zipfile.ZipFile(response.path) as archive:
        names = set(archive.namelist())
        assert "guidance.md" in names
        assert "parameter_registry.json" in names
        assert "workspace.zip" not in names


def test_frontend_history_and_paper_views_expose_workspace_zip_download() -> None:
    repo_root = Path(__file__).parents[2]
    paper_view = (repo_root / "frontend" / "src" / "components" / "PaperView.vue").read_text(encoding="utf-8")
    history_view = (repo_root / "frontend" / "src" / "components" / "HistoryView.vue").read_text(encoding="utf-8")

    assert "/api/tasks/${encodeURIComponent(props.taskId)}/workspace.zip" in paper_view
    assert "下载工作区 ZIP" in paper_view
    assert "/api/tasks/${encodeURIComponent(task.task_id)}/workspace.zip" in history_view
    assert "下载工作区" in history_view


def test_workflow_dispatches_guidance_without_legacy_writing_runner(monkeypatch) -> None:
    from app.core import workflow

    task = {"task_id": "task-1", "task_type": "guidance"}
    monkeypatch.setattr(workflow.task_manager, "get_task", lambda task_id: task)

    async def fake_guidance_runner(task_id, current_task):
        yield {"type": "guidance-runner", "task_id": task_id, "task": current_task}

    monkeypatch.setattr(workflow, "run_guidance_workflow", fake_guidance_runner, raising=False)

    async def collect_messages():
        return [message async for message in workflow.run_workflow("task-1", "question")]

    messages = asyncio.run(collect_messages())

    assert messages == [{"type": "guidance-runner", "task_id": "task-1", "task": task}]


def test_workflow_dispatches_polish_through_independent_polish_boundary(monkeypatch) -> None:
    from app.core import workflow
    from app.polish import workflow as polish_workflow

    task = {"task_id": "task-polish", "task_type": "polish"}
    monkeypatch.setattr(workflow.task_manager, "get_task", lambda task_id: task)

    async def fake_polish_runner(task_id, current_task):
        yield {"type": "polish-runner", "task_id": task_id, "task": current_task}

    monkeypatch.setattr(polish_workflow, "run_polish_workflow", fake_polish_runner)

    async def collect_messages():
        return [message async for message in workflow.run_workflow("task-polish", "ignored")]

    messages = asyncio.run(collect_messages())

    assert messages == [{"type": "polish-runner", "task_id": "task-polish", "task": task}]


def test_core_workflow_no_longer_exposes_legacy_paper_writing_runner() -> None:
    from app.core import workflow

    assert not hasattr(workflow, "_legacy_paper_writing_workflow")


def test_core_workflow_no_longer_exposes_polish_implementation() -> None:
    from app.core import workflow

    assert not hasattr(workflow, "_polish_workflow")


def test_guidance_runtime_policy_cannot_reenter_legacy_repair_agents() -> None:
    from app.guidance.policy import GUIDANCE_RUNTIME_POLICY

    assert GUIDANCE_RUNTIME_POLICY.output_format == "markdown"
    assert GUIDANCE_RUNTIME_POLICY.run_verification_agent is False
    assert GUIDANCE_RUNTIME_POLICY.run_chart_agent is False
    assert GUIDANCE_RUNTIME_POLICY.run_llm_figure_repair is False
    assert GUIDANCE_RUNTIME_POLICY.run_final_audit_repair_agents is False
    assert GUIDANCE_RUNTIME_POLICY.export_docx is False


def test_guidance_workflow_persists_blocked_final_status(monkeypatch) -> None:
    from app.guidance import workflow

    status_updates: list[str] = []

    class RecordingTaskManager:
        def update_status(self, task_id, status):
            status_updates.append(status.value)

    class BlockedPipeline:
        async def run(self, task_id, task):
            yield {"type": "progress", "data": {"status": "running"}}
            yield {
                "type": "result",
                "data": {
                    "task_id": task_id,
                    "status": "blocked",
                    "task_type": "guidance",
                },
            }

    monkeypatch.setattr(workflow, "task_manager", RecordingTaskManager())
    monkeypatch.setattr(
        workflow,
        "build_default_guidance_pipeline",
        lambda workflow_mode="standard": BlockedPipeline(),
    )

    async def collect_messages():
        return [
            message
            async for message in workflow.run_guidance_workflow(
                "task-blocked",
                {"task_id": "task-blocked", "task_type": "guidance"},
            )
        ]

    messages = asyncio.run(collect_messages())

    assert messages[-1]["data"]["status"] == "blocked"
    assert status_updates == ["running", "blocked"]
