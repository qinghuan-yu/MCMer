import json
from pathlib import Path

from app.compute.runner import run_solve_spec


def test_compute_runner_writes_registries_from_solve_spec(tmp_path: Path) -> None:
    spec = {
        "spec_id": "spec_demo",
        "subproblem_id": "Q4",
        "inputs": [{"path": "attachment.xlsx", "description": "source table"}],
        "parameters": [
            {
                "symbol": "a",
                "name": "Known coefficient",
                "value": 2,
                "unit": "m/s",
                "source_type": "problem_or_input",
                "source_ref": "problem statement",
            },
            {
                "symbol": "b",
                "name": "Derived coefficient",
                "formula": "a * 3 + 1",
                "unit": "m",
                "source_ref": "solve_spec:spec_demo",
                "estimation_method": "deterministic_formula",
            },
            {
                "symbol": "t0",
                "name": "Signal light start time",
                "identifiability": "non_identifiable",
                "reason": "Objective surface is flat for multiple start times.",
                "next_required_data": ["signal phase observation"],
            },
        ],
        "results": [
            {
                "id": "result_q4_b",
                "section": "Q4",
                "result_type": "parameter_calculation",
                "claim_text": "b is calculated from a.",
                "parameters": ["b"],
            }
        ],
    }

    report = run_solve_spec(spec, tmp_path)

    result_path = tmp_path / "result_registry.json"
    parameter_path = tmp_path / "parameter_registry.json"
    manifest_path = tmp_path / "compute_run_manifest.json"

    assert report["status"] == "partial"
    assert result_path.exists()
    assert parameter_path.exists()
    assert manifest_path.exists()

    result_registry = json.loads(result_path.read_text(encoding="utf-8"))
    parameter_registry = json.loads(parameter_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert result_registry["summary"]["verified_count"] == 1
    assert result_registry["summary"]["blocked_count"] == 1
    assert result_registry["blocked_results"][0]["reason"] == "non_identifiable_parameter"

    params = {item["symbol"]: item for item in parameter_registry["parameters"]}
    assert params["a"]["trust_status"] == "source_locked"
    assert params["b"]["value"] == 7
    assert params["b"]["linked_result_ids"] == ["result_q4_b"]
    assert params["t0"]["trust_status"] == "blocked"

    assert manifest["spec_id"] == "spec_demo"
    assert manifest["status"] == "partial"


def test_compute_runner_rejects_unsafe_formula(tmp_path: Path) -> None:
    spec = {
        "spec_id": "unsafe",
        "parameters": [
            {"symbol": "a", "value": 2},
            {"symbol": "bad", "formula": "__import__('os').system('echo bad')"},
        ],
    }

    try:
        run_solve_spec(spec, tmp_path)
    except ValueError as exc:
        assert "Unsupported or unsafe formula expression" in str(exc)
    else:
        raise AssertionError("unsafe formula should be rejected")


def test_compute_runner_blocks_unresolved_estimates_and_their_dependents(tmp_path: Path) -> None:
    spec = {
        "spec_id": "unresolved_fit",
        "subproblem_id": "Q1",
        "parameters": [
            {"symbol": "b21", "source_type": "estimated", "source_ref": "least squares"},
            {"symbol": "a21", "source_type": "estimated", "source_ref": "least squares"},
            {"symbol": "a22", "source_type": "estimated", "source_ref": "least squares"},
            {"symbol": "t_c", "source_type": "estimated", "source_ref": "least squares"},
            {"symbol": "b22", "formula": "b21 + (a21 - a22) * t_c"},
        ],
        "results": [
            {
                "id": "result_fit",
                "claim_text": "The fitted branch model is available.",
                "parameters": ["b21", "a21", "a22", "t_c", "b22"],
            }
        ],
    }

    report = run_solve_spec(spec, tmp_path)
    result_registry = json.loads((tmp_path / "result_registry.json").read_text(encoding="utf-8"))
    parameter_registry = json.loads((tmp_path / "parameter_registry.json").read_text(encoding="utf-8"))

    assert report["status"] == "partial"
    assert result_registry["verified_results"] == []
    assert result_registry["summary"]["blocked_count"] == 5
    params = {item["symbol"]: item for item in parameter_registry["parameters"]}
    assert params["b21"]["trust_status"] == "blocked"
    assert params["b22"]["trust_status"] == "blocked"
    assert "b21" in params["b22"]["reason"]


def test_compute_runner_resolves_formula_dependencies_declared_later(tmp_path: Path) -> None:
    spec = {
        "spec_id": "out_of_order",
        "parameters": [
            {"symbol": "total", "formula": "base + offset"},
            {"symbol": "base", "value": 10, "source_type": "problem_statement"},
            {"symbol": "offset", "value": 2, "source_type": "problem_statement"},
        ],
    }

    report = run_solve_spec(spec, tmp_path)
    parameter_registry = json.loads((tmp_path / "parameter_registry.json").read_text(encoding="utf-8"))
    params = {item["symbol"]: item for item in parameter_registry["parameters"]}

    assert report["status"] == "passed"
    assert params["total"]["value"] == 12
