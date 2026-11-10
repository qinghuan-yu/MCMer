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
