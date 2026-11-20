import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from scripts.run_closeout import BudgetLedger, build_pipeline, prepare_run, validate_case


CASE = Path(__file__).resolve().parents[1] / "closeout/portfolio_case.json"
LIMITS = {"max_calls": 3, "max_total_tokens": 1800, "max_total_run_seconds": 1800, "max_runs": 2}


def test_unknown_usage_reservation_survives_reload(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = BudgetLedger(path, LIMITS, "frozen")
    ledger.reserve(history=[{"role": "user", "content": "input"}], max_tokens=10, agent="test")
    resumed = BudgetLedger(path, LIMITS, "frozen")
    assert len(resumed.state["calls"]) == 1
    assert resumed.charged_tokens() > 1000
    with pytest.raises(RuntimeError, match="token"):
        resumed.reserve(history=[{"role": "user", "content": "input"}], max_tokens=10, agent="test")


def test_known_usage_releases_reservation_but_not_call_count(tmp_path):
    path = tmp_path / "ledger.json"
    limits = {**LIMITS, "max_calls": 1}
    ledger = BudgetLedger(path, limits, "frozen")
    call = ledger.reserve(history=[], max_tokens=10, agent="test")
    ledger.finish(call, status="returned", usage={"total_tokens": 25})
    resumed = BudgetLedger(path, limits, "frozen")
    assert resumed.charged_tokens() == 25
    with pytest.raises(RuntimeError, match="call"):
        resumed.reserve(history=[], max_tokens=10, agent="test")


def test_model_tools_must_match_the_registered_mathematical_contracts():
    from copy import deepcopy
    from app.guidance.math_workspace import MathWorkspace
    from scripts.run_closeout import validate_request

    tools = list(reversed(MathWorkspace.tools()))
    for stage in ("GuidanceModeler", "GuidanceModelReviser_q1"):
        validate_request({"agent_name": stage, "tools": tools, "max_attempts": 1})
    altered = deepcopy(tools)
    altered[0]["function"]["parameters"] = {"type": "object"}
    for stage, supplied in (("GuidanceModelReviewer", tools), ("GuidanceModeler", altered),
                            ("GuidanceModeler", [*tools, tools[0]])):
        with pytest.raises(ValueError, match="tools"):
            validate_request({"agent_name": stage, "tools": supplied, "max_attempts": 1})


def test_tool_schemas_are_charged_before_dispatch(tmp_path):
    from app.guidance.math_workspace import MathWorkspace

    ledger = BudgetLedger(tmp_path / "ledger.json", LIMITS, "frozen")
    with pytest.raises(RuntimeError, match="token"):
        ledger.reserve(history=[], tools=MathWorkspace.tools(), max_tokens=10, agent="GuidanceModeler")
    assert ledger.state["calls"] == []


def test_official_harness_emits_registered_schemas_at_the_provider_boundary():
    from app.guidance.deepseek_harness import run_math_turn
    from app.guidance.math_workspace import MathWorkspace
    from scripts.run_closeout import validate_request

    executed = []

    async def provider(history, tools):
        validate_request({"agent_name": "GuidanceModeler_q1", "tools": tools, "max_attempts": 1})
        return {"role": "assistant", "tool_calls": [{"id": "s", "type": "function",
                "function": {"name": "submit_model", "arguments": "{}"}}]}

    def execute(name, args):
        executed.append(name)
        return {"submitted": True}

    asyncio.run(run_math_turn(prompt="test", system_prompt="test", tools=MathWorkspace.tools(),
                              request_model=provider, execute_tool=execute, is_done=lambda: bool(executed),
                              max_steps=1, timeout_seconds=30))
    assert executed == ["submit_model"]


def test_runtime_record_binds_installed_code_and_rejects_version_drift(tmp_path):
    from scripts.run_closeout import math_runtime_snapshot

    package_path = "node_modules/example"
    package = tmp_path / package_path
    package.mkdir(parents=True)
    (tmp_path / "package-lock.json").write_text(json.dumps({"packages": {
        "": {}, package_path: {"version": "1.0.0"},
    }}), encoding="utf-8")
    manifest = package / "package.json"
    manifest.write_text('{"version":"1.0.0"}', encoding="utf-8")
    script = package / "runtime.js"
    script.write_text("first", encoding="utf-8")
    initial = math_runtime_snapshot(tmp_path)
    script.write_text("second", encoding="utf-8")
    assert math_runtime_snapshot(tmp_path)["installed_code_sha256"] != initial["installed_code_sha256"]
    manifest.write_text('{"version":"2.0.0"}', encoding="utf-8")
    with pytest.raises(ValueError, match="version"):
        math_runtime_snapshot(tmp_path)


def test_runtime_disappearing_at_close_still_records_failed_run(tmp_path, monkeypatch):
    from scripts import run_closeout as runner
    from app.services.config_manager import config_manager
    from app.utils.log_util import logger

    class OfflinePipeline:
        _stages = {}

        async def run(self, name, task):
            yield {"type": "result", "data": {"status": "completed"}}

    snapshots = iter([{"node": "test"}, None])

    def runtime(_directory):
        value = next(snapshots)
        if value is None:
            raise FileNotFoundError("missing runtime manifest")
        return value

    root = tmp_path / "run-suite"
    monkeypatch.setattr(runner.sys, "argv", ["runner", "--case", str(CASE), "--root", str(root), "--live"])
    monkeypatch.setattr(runner, "math_runtime_snapshot", runtime)
    monkeypatch.setattr(runner, "build_pipeline", lambda **kwargs: OfflinePipeline())
    monkeypatch.setattr(config_manager, "get_effective_key", lambda name: "offline-test-key")
    monkeypatch.setattr(config_manager, "CONFIG_FILE", CASE)
    monkeypatch.setattr(logger, "add", lambda *args, **kwargs: None)
    monkeypatch.setattr(logger, "remove", lambda *args, **kwargs: None)
    asyncio.run(runner.main())
    ledger = json.loads((root / "ledger.json").read_bytes())
    receipt = json.loads((root / "run-01/run_summary.json").read_bytes())
    assert ledger["calls"] == []
    assert receipt["code_unchanged"] is False
    assert receipt["status"] == ledger["runs"][0]["status"] == "failed"
    assert receipt["pipeline_result"]["status"] == "completed"


def test_case_or_budget_change_cannot_reset_ledger(tmp_path):
    path = tmp_path / "ledger.json"
    BudgetLedger(path, LIMITS, "frozen")
    with pytest.raises(ValueError, match="binding"):
        BudgetLedger(path, {**LIMITS, "max_calls": 24}, "frozen")
    with pytest.raises(ValueError, match="binding"):
        BudgetLedger(path, LIMITS, "new-case")


def test_attempts_require_repair_evidence_and_preserve_elapsed_time(tmp_path):
    ledger = BudgetLedger(tmp_path / "ledger.json", LIMITS, "frozen")
    ledger.begin_run(code_sha="first", repair_evidence=None)
    with pytest.raises(RuntimeError, match="unfinished"):
        ledger.begin_run(code_sha="second", repair_evidence="test-proof")
    ledger.end_run(status="blocked", seconds=1799)
    with pytest.raises(RuntimeError, match="repair"):
        ledger.begin_run(code_sha="second", repair_evidence=None)
    ledger.begin_run(code_sha="second", repair_evidence="test-proof")
    assert ledger.remaining_seconds() == 1
    ledger.end_run(status="blocked", seconds=1)
    with pytest.raises(RuntimeError):
        ledger.begin_run(code_sha="third", repair_evidence="test-proof")


def test_changed_task_is_rejected_before_output_directory_exists(tmp_path):
    case = json.loads(CASE.read_text(encoding="utf-8"))
    (tmp_path / case["task_file"]).write_text("changed", encoding="utf-8")
    case_path = tmp_path / "case.json"
    case_path.write_text(json.dumps(case), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA"):
        prepare_run(case_path, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_fresh_input_and_default_stages_exclude_reference_material(tmp_path):
    task = prepare_run(CASE, tmp_path / "run")
    original = (CASE.parent / "portfolio_task.txt").read_bytes()
    assert hashlib.sha256(original).hexdigest() == validate_case(CASE)["task_sha256"]
    assert task["source_question_text"] == original.decode("utf-8").strip()
    assert not any(p.name.startswith("probe") for p in (tmp_path / "run").rglob("*"))
    from app.guidance import workflow
    original_factory = workflow.LLM
    pipeline = build_pipeline(live=False)
    assert workflow.LLM is original_factory
    assert list(pipeline._stages) == ["decomposition", "source", "modeling", "model_review", "calculation", "result_verification", "guidance"]
