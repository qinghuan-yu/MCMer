import json
from pathlib import Path

import pytest
from pydantic import ValidationError


def test_candidate_model_requires_applicability_boundary() -> None:
    from app.guidance.model_ir import CandidateModel

    try:
        CandidateModel(
            id="constant_baseline",
            role="baseline",
            operator_node_ids=["fit"],
            selection_metric="rmse",
        )
    except ValidationError as exc:
        assert "applicability_boundary" in str(exc)
    else:
        raise AssertionError("candidate routes must declare an applicability boundary")


def test_validation_plan_requires_supported_model_families() -> None:
    from app.guidance.model_ir import ValidationPlan

    validation = ValidationPlan(
        model_families=["regression"],
        identifiability_checks=["design_matrix_rank"],
        evidence_checks=["residual_recompute"],
        robustness_checks=["cross_validation"],
    )
    assert validation.model_families == ["regression"]

    try:
        ValidationPlan(
            model_families=["unsupported_family"],
            identifiability_checks=["design_matrix_rank"],
            evidence_checks=["residual_recompute"],
            robustness_checks=["cross_validation"],
        )
    except ValidationError as exc:
        assert "model_families" in str(exc)
    else:
        raise AssertionError("unknown model families must be rejected")


def _solvable_payload():
    from app.guidance.model_ir import (
        BinaryExpression,
        CandidateModel,
        DataReference,
        EquationSpec,
        FinalAnswerSpec,
        OperatorInvocation,
        OperatorGraphExecutionComponent,
        SolvableModelIR,
        SymbolExpression,
        ValidationPlan,
        VariableSpec,
    )

    return SolvableModelIR(
        execution_status="solvable",
        subproblem_id="problem1",
        objective="Estimate branch-flow parameters from aggregate observations.",
        fact_bindings=[],
        execution_components=[OperatorGraphExecutionComponent(
            kind="operator_graph",
            component_id="branch_fit_graph",
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
        )],
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
        assumptions=[],
        candidate_models=[
            CandidateModel(
                id="constant_baseline",
                role="baseline",
                operator_node_ids=["fit_branch_slope"],
                selection_metric="rmse",
                applicability_boundary="Observed sample range and constant-flow hypothesis.",
            ),
            CandidateModel(
                id="linear_recommended",
                role="recommended",
                operator_node_ids=["fit_branch_slope"],
                selection_metric="rmse",
                applicability_boundary="Observed sample range under the declared linear trend.",
            ),
        ],
        validation=ValidationPlan(
            model_families=["regression"],
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


def test_canonical_indexed_component_owns_its_variables_without_flat_duplicates() -> None:
    from app.guidance.model_ir import ModelIR

    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "model_ir"
        / "generic_multiperiod_inventory.json"
    )
    payload = json.loads(fixture.read_text(encoding="utf-8"))

    model_ir = ModelIR.model_validate(payload)

    component = model_ir.subproblems[0].execution_components[0]
    assert component.kind == "indexed_linear"
    assert component.model.variables[0].id == "purchase"


def test_canonical_model_ir_rejects_legacy_flat_execution_fields() -> None:
    from app.guidance.model_ir import ModelIR

    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "model_ir"
        / "generic_multiperiod_inventory.json"
    )
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    subproblem = payload["subproblems"][0]
    component = subproblem.pop("execution_components")[0]
    subproblem["variables"] = []
    subproblem["indexed_linear_model"] = component["model"]
    subproblem["operators"] = [{
        "node_id": component["solver_node_id"],
        "operator_id": "optimization.milp",
        "outputs": component["outputs"],
    }]

    with pytest.raises(ValidationError) as exc_info:
        ModelIR.model_validate(payload)

    rejected_fields = {
        error["loc"][-1]
        for error in exc_info.value.errors()
        if error["type"] == "extra_forbidden"
    }
    assert {"variables", "operators", "indexed_linear_model"} <= rejected_fields


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
    graph = subproblem.execution_components[0]
    subproblem.data[0].unit_overrides["main_flow"] = "N*m"
    graph.operators[0].inputs["y"] = "traffic_sheet.missing_flow"
    graph.variables.append(
        VariableSpec(
            id="matrix_state",
            role="state",
            symbol="M",
            unit="vehicles/2min",
            shape=["sample", "feature"],
        )
    )
    subproblem.equations[0].right = SymbolExpression(symbol="matrix_state")
    graph.operators[0].inputs["x"] = "postprocess.result"
    graph.operators.append(
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


def test_semantic_validator_rejects_incomplete_indexed_architecture_coverage() -> None:
    from app.guidance.model_ir import (
        ConstantLinearValue,
        IndexedLinearConstraint,
        IndexedLinearModelSpec,
        IndexedLinearExecutionComponent,
        IndexedLinearObjective,
        IndexedLinearTerm,
        IndexedVariableReference,
        IndexedVariableSpec,
        IndexSetSpec,
    )
    from app.guidance.model_ir_semantics import ModelIRSemanticValidator

    subproblem = _solvable_payload().model_copy(deep=True)
    purchase_term = IndexedLinearTerm(
        coefficient=ConstantLinearValue(value=1.0),
        variable=IndexedVariableReference(variable_id="purchase"),
    )
    subproblem.execution_components.append(IndexedLinearExecutionComponent(
        kind="indexed_linear",
        component_id="purchase_policy",
        solver_node_id="solve",
        outputs=["solution"],
        model=IndexedLinearModelSpec(
            index_sets=[IndexSetSpec(id="period", values=[1])],
            variables=[
                IndexedVariableSpec(id="purchase", domain="integer"),
                IndexedVariableSpec(id="idle_capacity", domain="continuous"),
            ],
            objective=IndexedLinearObjective(
                sense="minimize",
                terms=[purchase_term],
            ),
            constraints=[
                IndexedLinearConstraint(
                    id="purchase_limit",
                    terms=[purchase_term],
                    relation="less_equal",
                    rhs=ConstantLinearValue(value=10.0),
                )
            ],
        ),
    ))

    diagnostics = ModelIRSemanticValidator().validate_subproblem(subproblem)
    codes = {item.code for item in diagnostics}

    assert "indexed_variable_unused" in codes


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
