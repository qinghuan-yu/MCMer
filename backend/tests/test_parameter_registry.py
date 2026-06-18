from app.artifacts.parameter_registry import build_parameter_registry


def test_parameter_registry_extracts_parameters_from_verified_results() -> None:
    registry = build_parameter_registry(
        {
            "verified_results": [
                {
                    "id": "result_q1_fit",
                    "status": "verified",
                    "source_data": ["fit.csv"],
                    "parameters": [
                        {
                            "symbol": "alpha",
                            "name": "growth rate",
                            "value": 0.12,
                            "unit": "1/day",
                            "source_type": "estimated_from_data",
                        }
                    ],
                }
            ],
            "blocked_results": [],
        }
    )

    assert registry["summary"]["total_count"] == 1
    parameter = registry["parameters"][0]
    assert parameter["id"] == "param_alpha"
    assert parameter["symbol"] == "alpha"
    assert parameter["value"] == 0.12
    assert parameter["source_ref"] == "fit.csv"
    assert parameter["trust_status"] == "estimated"
    assert parameter["linked_result_ids"] == ["result_q1_fit"]


def test_parameter_registry_extracts_inputs_as_source_locked_parameters() -> None:
    registry = build_parameter_registry(
        {
            "verified_results": [
                {
                    "id": "result_q2_score",
                    "inputs": {
                        "N": 120,
                        "threshold": {"value": 0.8, "unit": "score"},
                    },
                    "source_data": ["problem_statement"],
                }
            ],
            "blocked_results": [],
        }
    )

    ids = {item["id"] for item in registry["parameters"]}
    assert ids == {"param_n", "param_threshold"}
    by_symbol = {item["symbol"]: item for item in registry["parameters"]}
    assert by_symbol["N"]["trust_status"] == "source_locked"
    assert by_symbol["threshold"]["unit"] == "score"


def test_parameter_registry_deduplicates_by_symbol_and_value() -> None:
    registry = build_parameter_registry(
        {
            "verified_results": [
                {"id": "r1", "parameters": [{"symbol": "alpha", "value": 0.12}]},
                {"id": "r2", "parameters": [{"symbol": "alpha", "value": 0.12}]},
            ]
        }
    )

    assert registry["summary"]["total_count"] == 1
    assert registry["parameters"][0]["linked_result_ids"] == ["r1", "r2"]
