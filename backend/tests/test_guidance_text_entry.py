import hashlib
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes, ws
from app.config.setting import settings
from app.guidance import workflow
from test_scipy_milp_guidance import QUESTION, ScriptedIntegerExample


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "WORK_DIR", str(tmp_path))
    monkeypatch.setattr(workflow, "LLM", ScriptedIntegerExample)
    monkeypatch.setattr(routes.task_manager, "_active_tasks", {})
    monkeypatch.setattr(routes.task_manager, "_workflow_tasks", {})
    message_dir = tmp_path / "messages"
    message_dir.mkdir()
    monkeypatch.setattr(ws.redis_manager, "_message_log_dir", str(message_dir))
    monkeypatch.setattr(ws.redis_manager, "_use_memory", True)
    monkeypatch.setattr(ws.redis_manager, "_memory_channels", {})
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.include_router(ws.router, prefix="/ws")
    with TestClient(app) as client:
        yield client


def run_task(client, task_id):
    with client.websocket_connect(f"/ws/{task_id}") as socket:
        while True:
            event = socket.receive_json()
            if event.get("type") in {"result", "error", "cancelled"}:
                return event


@pytest.mark.parametrize("with_data", [False, True])
def test_pasted_question_through_api_and_background_guidance(client, with_data):
    question = QUESTION.read_text(encoding="utf-8").strip()
    response = client.post("/api/tasks", json={"task_type": "guidance", "question": question, "expect_files": with_data})
    assert response.status_code == 200
    task = response.json()
    root = Path(task["work_dir"])
    if with_data:
        uploaded = client.post(f"/api/tasks/{task['task_id']}/upload", data={"purpose": "supplementary_data"},
                               files={"file": ("观测.csv", "编号,值\n甲,3".encode())})
        assert uploaded.status_code == 200
    result = run_task(client, task["task_id"])
    assert result["data"]["status"] == "completed", result["data"].get("error_message")
    original = json.loads((root / "input_catalog.json").read_bytes())
    assert (root / original["source_file"]).read_bytes() == question.encode("utf-8")
    assert original["source_sha256"] == hashlib.sha256(question.encode("utf-8")).hexdigest()
    assert any("非负整数" in b["text"] for b in original["blocks"])
    assert all(b["source_ref"].startswith(original["source_file"] + "#") for b in original["blocks"])
    facts = json.loads((root / "problem_spec.json").read_bytes())["locked_facts"]
    assert all(ref.startswith(original["source_file"] + "#") for fact in facts for ref in fact["source_refs"])
    if with_data:
        assert "data/观测.csv" in [item["source_ref"] for item in json.loads(
            (root / "evidence_profile.json").read_bytes())["evidence"]]


def test_uploaded_original_keeps_priority_and_distinct_pasted_request(client):
    note = "请在最终报告中核对每条约束。"
    response = client.post("/api/tasks", json={"task_type": "guidance", "question": note, "expect_files": True})
    task = response.json()
    root = Path(task["work_dir"])
    original = QUESTION.read_bytes()
    for _ in range(2):
        uploaded = client.post(f"/api/tasks/{task['task_id']}/upload", data={"purpose": "question_source"},
                               files={"file": ("question.txt", original)})
        assert uploaded.status_code == 200
    result = run_task(client, task["task_id"])
    assert result["data"]["status"] == "completed", result["data"].get("error_message")
    catalog = json.loads((root / "input_catalog.json").read_bytes())
    assert catalog["source_file"] == "inputs/question/question.txt"
    assert (root / catalog["source_file"]).read_bytes() == original
    trace = [p for p in (root / "inputs").rglob("*.txt") if p.read_bytes() == note.encode("utf-8")]
    assert len(trace) == 1
    assert all(note not in block["text"] for block in catalog["blocks"])
    summary = json.loads((root / "problem_spec.json").read_bytes())["task_summary"]
    assert note in summary and "补充" in summary and "-x + y" in summary


def test_empty_question_still_requires_a_source(client):
    assert client.post("/api/tasks", json={"task_type": "guidance", "question": "  "}).status_code == 400
    task = client.post("/api/tasks", json={"task_type": "guidance", "expect_files": True}).json()
    assert not (Path(task["work_dir"]) / "input_catalog.json").exists()


def test_upload_only_pipeline_uses_selected_original_after_an_appendix(client):
    task = client.post("/api/tasks", json={"task_type": "guidance", "expect_files": True}).json()
    root = Path(task["work_dir"])
    appendix = "附录：符号表。".encode("utf-8")
    for name, content in [("附录.txt", appendix), ("原题.txt", QUESTION.read_bytes()), ("附录.txt", appendix)]:
        reply = client.post(f"/api/tasks/{task['task_id']}/upload", data={"purpose": "question_source"},
                            files={"file": (name, content)})
        assert reply.status_code == 200
    result = run_task(client, task["task_id"])
    assert result["data"]["status"] == "completed", result["data"].get("error_message")
    catalog = json.loads((root / "input_catalog.json").read_bytes())
    assert catalog["source_file"] == "inputs/question/原题.txt"
    assert not (root / "inputs/form").exists()
    assert json.loads((root / "problem_spec.json").read_bytes())["task_summary"] == QUESTION.read_text(encoding="utf-8").strip()


def test_pasted_source_keeps_request_bytes_including_line_endings(client):
    question = "\r\n  变量甲为非负整数。\r\n请报告甲的上界。  \r\n"
    task = client.post("/api/tasks", json={"task_type": "guidance", "question": question}).json()
    root = Path(task["work_dir"])
    catalog = json.loads((root / "input_catalog.json").read_bytes())
    assert (root / catalog["source_file"]).read_bytes() == question.encode("utf-8")
    restored = json.loads((root / "task_info.json").read_bytes())
    assert restored["pasted_question_sha256"] == catalog["source_sha256"]
    assert restored["input_catalog_ref"] == "input_catalog.json"


def test_old_unstarted_text_task_recovers_from_its_saved_question(client, tmp_path):
    root = tmp_path / "legacy-pasted"
    root.mkdir()
    question = QUESTION.read_text(encoding="utf-8")
    saved = {"task_id": root.name, "task_type": "guidance", "status": "pending", "question": question,
             "work_dir": str(root), "source_question": "", "source_question_file": ""}
    (root / "task_info.json").write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    assert run_task(client, root.name)["data"]["status"] == "completed"
    catalog = json.loads((root / "input_catalog.json").read_bytes())
    assert (root / catalog["source_file"]).read_bytes() == question.encode("utf-8")
    restored = json.loads((root / "task_info.json").read_bytes())
    assert restored["pasted_question_sha256"] == catalog["source_sha256"]
    assert restored["input_catalog_ref"] == "input_catalog.json"


def test_changed_registered_source_is_not_replaced_by_task_summary(client):
    task = client.post("/api/tasks", json={"task_type": "guidance", "question": "这是原文。"}).json()
    root = Path(task["work_dir"])
    catalog = json.loads((root / "input_catalog.json").read_bytes())
    (root / catalog["source_file"]).write_bytes("被替换的文字。".encode("utf-8"))
    result = run_task(client, task["task_id"])
    assert result["data"]["status"] == "failed" and "摘要" in result["data"]["error_message"]
    assert (root / catalog["source_file"]).read_bytes() == "被替换的文字。".encode("utf-8")
