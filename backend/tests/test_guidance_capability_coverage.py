from app.schemas.response import extract_capability_coverage


def test_capability_coverage_reports_partial_missing_operator_and_blocked_recovery() -> None:
    coverage = extract_capability_coverage(
        approved_model={
            "approved_model_ir": {
                "subproblems": [
                    {
                        "execution_status": "solvable",
                        "subproblem_id": "forecast_part",
                        "operators": [
                            {"node_id": "read", "operator_id": "data.read_excel_table"},
                            {"node_id": "fit", "operator_id": "forecast.robust_regularized_time_split"},
                        ],
                        "validation": {"model_families": ["forecast"]},
                    },
                    {
                        "execution_status": "solvable",
                        "subproblem_id": "unsupported_part",
                        "operators": [
                            {"node_id": "solve", "operator_id": "pde.finite_element"},
                        ],
                        "validation": {"model_families": ["simulation"]},
                    },
                    {
                        "execution_status": "blocked",
                        "subproblem_id": "missing_data_part",
                        "blocking_reason": "Required boundary observations are absent.",
                        "missing_inputs": ["boundary observations"],
                        "recovery_action": "Upload the boundary observation table.",
                        "expected_outputs": ["boundary estimate"],
                    },
                ],
            },
        },
        compilation_result={
            "diagnostics": [{
                "code": "operator_not_registered",
                "message": "operator is not registered: pde.finite_element",
                "subproblem_id": "unsupported_part",
                "node_id": "solve",
            }],
        },
        result_registry={
            "verified_results": [{
                "id": "forecast_result",
                "subproblem_id": "forecast_part",
            }],
            "blocked_results": [
                {
                    "id": "forecast_interval",
                    "subproblem_id": "forecast_part",
                    "reason": "Interval evidence is unavailable.",
                    "recovery_action": "Provide enough holdout observations.",
                },
                {
                    "id": "unsupported_result",
                    "subproblem_id": "unsupported_part",
                    "reason": "operator is not registered: pde.finite_element",
                    "recovery_action": "Register a validated PDE operator.",
                },
            ],
        },
    )

    by_id = {item["subproblem_id"]: item for item in coverage}
    assert by_id["forecast_part"] == {
        "subproblem_id": "forecast_part",
        "coverage_status": "partial",
        "model_families": ["forecast"],
        "required_operators": [
            "data.read_excel_table",
            "forecast.robust_regularized_time_split",
        ],
        "missing_operators": [],
        "blocking_reasons": ["Interval evidence is unavailable."],
        "recovery_actions": ["Provide enough holdout observations."],
    }
    assert by_id["unsupported_part"]["coverage_status"] == "blocked"
    assert by_id["unsupported_part"]["missing_operators"] == ["pde.finite_element"]
    assert "Register a validated PDE operator." in by_id["unsupported_part"]["recovery_actions"]
    assert by_id["missing_data_part"]["coverage_status"] == "blocked"
    assert by_id["missing_data_part"]["blocking_reasons"] == [
        "Required boundary observations are absent."
    ]
    assert by_id["missing_data_part"]["recovery_actions"] == [
        "Upload the boundary observation table."
    ]
