from pydantic import ValidationError


def _solvable_payload():
    from app.guidance.model_ir import (
        BinaryExpression,
        CandidateModel,
        DataReference,
        EquationSpec,
        FinalAnswerSpec,
        OperatorInvocation,
        SolvableModelIR,
        SymbolExpression,
        ValidationPlan,
        VariableSpec,
    )

    return SolvableModelIR(
        execution_status="solvable",
        subproblem_id="problem1",
        objective="Estimate branch-flow parameters from aggregate observations.",
        variables=[
            VariableSpec(
                id="sample",
                role="observed",
                symbol="k",
                unit="sample",
                shape=["sample"],
            ),
            VariableSpec(
                id="main_flow",
                role="observed",
                symbol="F",
                unit="vehicles/2min",
                shape=["sample"],
            ),
            VariableSpec(
                id="branch_slope",
                role="unknown_parameter",
                symbol="a",
                unit="vehicles/2min/sample",
                shape=[],
            ),
        ],
        data=[
            DataReference(
                id="traffic_sheet",
                source_ref="data/attachment.xlsx#sheet1",
                fields={"sample": "k", "main_flow": "F"},
                unit_overrides={"main_flow": "vehicles/2min"},
            )
        ],
        equations=[
            EquationSpec(
                id="aggregate_equation",
                left=SymbolExpression(symbol="main_flow"),
                right=BinaryExpression(
                    operator="multiply",
                    left=SymbolExpression(symbol="branch_slope"),
                    right=SymbolExpression(symbol="sample"),
                ),
            )
        ],
        operators=[
            OperatorInvocation(
                node_id="fit_branch_slope",
                operator_id="regression.constrained_least_squares",
                inputs={
                    "x": "traffic_sheet.sample",
                    "y": "traffic_sheet.main_flow",
                },
                outputs=["branch_slope", "fit_result"],
            )
        ],
        candidate_models=[
            CandidateModel(
                id="constant_baseline",
                role="baseline",
                operator_node_ids=["fit_branch_slope"],
                selection_metric="rmse",
            ),
            CandidateModel(
                id="linear_recommended",
                role="recommended",
                operator_node_ids=["fit_branch_slope"],
                selection_metric="rmse",
            ),
        ],
        validation=ValidationPlan(
            identifiability_checks=["design_matrix_rank"],
            evidence_checks=["residual_recompute", "baseline_comparison"],
            robustness_checks=["leave_one_out"],
        ),
        final_answers=[
            FinalAnswerSpec(
                id="answer_branch_slope",
                item="Branch-flow slope",
                value_ref="branch_slope",
                status="chosen_under_prior",
                evidence_ids=["fit_result", "branch_slope"],
            )
        ],
    )


def test_model_ir_serializes_typed_expression_and_evidence_bound_answer() -> None:
    from app.guidance.model_ir import ModelIR

    model_ir = ModelIR(
        schema_version="1.0",
        model_id="aggregate_flow_decomposition",
        subproblems=[_solvable_payload()],
    )

    payload = model_ir.model_dump(mode="json")

    assert payload["subproblems"][0]["execution_status"] == "solvable"
    equation = payload["subproblems"][0]["equations"][0]
    assert equation["right"]["kind"] == "binary"
    assert equation["right"]["operator"] == "multiply"
    answer = payload["subproblems"][0]["final_answers"][0]
    assert answer["evidence_ids"] == ["fit_result", "branch_slope"]


def test_final_answer_without_evidence_is_rejected() -> None:
    from app.guidance.model_ir import FinalAnswerSpec

    try:
        FinalAnswerSpec(
            id="unsupported_answer",
            item="Unsupported conclusion",
            value_ref="missing_result",
            status="evidence_verified",
            evidence_ids=[],
        )
    except ValidationError as exc:
        assert "evidence_ids" in str(exc)
    else:
        raise AssertionError("an answer without evidence must be rejected")


def test_model_ir_preserves_honest_blocked_subproblem_contract() -> None:
    from app.guidance.model_ir import BlockedModelIR, ModelIR

    model_ir = ModelIR(
        schema_version="1.0",
        model_id="partial_engineering_model",
        subproblems=[
            BlockedModelIR(
                execution_status="blocked",
                subproblem_id="problem2",
                objective="Compute the constrained engineering optimum.",
                blocking_reason="The governing failure constraint is absent.",
                missing_inputs=["failure criterion", "material limit"],
                recovery_action="Supply the missing criterion and limits, then rebuild the Model IR.",
                expected_outputs=["feasible optimum", "active constraint"],
            )
        ],
    )

    blocked = model_ir.subproblems[0]
    assert blocked.execution_status == "blocked"
    assert blocked.missing_inputs == ["failure criterion", "material limit"]


def test_semantic_validator_localizes_unit_field_dimension_and_cycle_errors() -> None:
    from app.guidance.model_ir import OperatorInvocation, SymbolExpression, VariableSpec
    from app.guidance.model_ir_semantics import ModelIRSemanticValidator

    subproblem = _solvable_payload().model_copy(deep=True)
    subproblem.data[0].unit_overrides["main_flow"] = "N*m"
    subproblem.operators[0].inputs["y"] = "traffic_sheet.missing_flow"
    subproblem.variables.append(
        VariableSpec(
            id="matrix_state",
            role="state",
            symbol="M",
            unit="vehicles/2min",
            shape=["sample", "feature"],
        )
    )
    subproblem.equations[0].right = SymbolExpression(symbol="matrix_state")
    subproblem.operators[0].inputs["x"] = "postprocess.result"
    subproblem.operators.append(
        OperatorInvocation(
            node_id="postprocess",
            operator_id="transform.identity",
            inputs={"value": "fit_branch_slope.fit_result"},
            outputs=["result"],
        )
    )

    diagnostics = ModelIRSemanticValidator().validate_subproblem(subproblem)
    by_code = {item.code: item for item in diagnostics}

    assert "unit_conflict" in by_code
    assert "unknown_data_field" in by_code
    assert "dimension_mismatch" in by_code
    assert "operator_cycle" in by_code
    assert all(item.subproblem_id == "problem1" for item in diagnostics)
    assert all(item.path.startswith("subproblems[problem1]") for item in diagnostics)


def test_free_text_formula_is_rejected_instead_of_becoming_model_semantics() -> None:
    from app.guidance.model_ir import ModelIR

    payload = {
        "schema_version": "1.0",
        "model_id": "free_text_model",
        "subproblems": [
            {
                "execution_status": "solvable",
                "subproblem_id": "problem1",
                "objective": "Fit a trend.",
                "variables": [],
                "data": [],
                "equations": ["y = a*x + b"],
                "operators": [],
                "candidate_models": [],
                "validation": {
                    "identifiability_checks": [],
                    "evidence_checks": [],
                    "robustness_checks": []
                },
                "final_answers": []
            }
        ]
    }

    try:
        ModelIR.model_validate(payload)
    except ValidationError as exc:
        assert "equations" in str(exc)
    else:
        raise AssertionError("free-text equations must not be accepted as executable semantics")
