"""数值领域注册与编排入口。"""

from __future__ import annotations

from app.guidance.operator_runtimes import (
    artifact_io,
    decision,
    diagnostics,
    dynamics,
    forecast,
    graph,
    milp,
    optimization,
    output_contracts,
    plotting,
    regression,
)
from app.guidance.operators import (
    OperatorDefinition,
    OperatorPort,
    OperatorRegistry,
)


DEFAULT_OPERATOR_IDS = {
    "artifact.extract_mapping",
    "artifact.extract_matrix",
    "artifact.extract_scalar",
    "artifact.extract_vector",
    "data.read_excel_table",
    "data.scalar_constant",
    "decision.mcda_weighted_ranking",
    "design.periodic_key_moments",
    "diagnostics.design_rank",
    "diagnostics.leave_one_out",
    "dynamics.affine_ode_rk4",
    "forecast.robust_regularized_time_split",
    "forecast.future_basis_rolling_origin",
    "graph.shortest_path_max_flow",
    "model_selection.continuous_hinge_bic",
    "optimization.constrained_quadratic_multistart",
    "optimization.milp",
    "plot.xy_evidence",
    "regression.basis_least_squares",
    "regression.zero_intercept_grouped",
}

def _port(kind: str) -> OperatorPort:
    return OperatorPort(value_kind=kind, unit="unspecified")

def build_default_operator_registry() -> OperatorRegistry:
    registry = OperatorRegistry()
    registrations = [
        (
            "artifact.extract_mapping",
            {"artifact": _port("artifact")},
            {"value": _port("artifact")},
            artifact_io.extract_mapping,
            output_contracts.mapping_output_validator("value"),
        ),
        (
            "artifact.extract_matrix",
            {"artifact": _port("artifact")},
            {"value": _port("matrix")},
            artifact_io.extract_matrix,
            artifact_io.valid_matrix_output,
        ),
        (
            "artifact.extract_scalar",
            {"artifact": _port("artifact")},
            {"value": _port("scalar")},
            artifact_io.extract_scalar,
            artifact_io.valid_scalar_output,
        ),
        (
            "artifact.extract_vector",
            {"artifact": _port("artifact")},
            {"value": _port("vector")},
            artifact_io.extract_vector,
            artifact_io.valid_vector_output,
        ),
        (
            "data.scalar_constant",
            {},
            {"value": _port("scalar")},
            artifact_io.scalar_constant,
            artifact_io.valid_scalar_output,
        ),
        (
            "design.periodic_key_moments",
            {},
            {"design": _port("artifact")},
            diagnostics.periodic_key_moments,
            output_contracts.mapping_output_validator("design"),
        ),
        (
            "data.read_excel_table",
            {"source": _port("artifact")},
            {"table": _port("table")},
            artifact_io.read_excel_table,
            output_contracts.mapping_output_validator("table"),
        ),
        (
            "regression.zero_intercept_grouped",
            {"table": _port("table")},
            {"fit": _port("artifact")},
            regression.zero_intercept_grouped,
            output_contracts.mapping_output_validator("fit"),
        ),
        (
            "regression.basis_least_squares",
            {"x": _port("vector"), "y": _port("vector")},
            {"fit": _port("artifact")},
            regression.basis_least_squares,
            output_contracts.mapping_output_validator("fit"),
        ),
        (
            "forecast.robust_regularized_time_split",
            {
                "time": _port("vector"),
                "features": _port("matrix"),
                "target": _port("vector"),
            },
            {"evaluation": _port("artifact")},
            forecast.robust_regularized_time_split,
            output_contracts.mapping_output_validator("evaluation"),
        ),
        (
            "forecast.future_basis_rolling_origin",
            {"time": _port("vector"), "target": _port("vector")},
            {"forecast": _port("artifact")},
            forecast.future_basis_rolling_origin,
            output_contracts.mapping_output_validator("forecast"),
        ),
        (
            "model_selection.continuous_hinge_bic",
            {"x": _port("vector"), "y": _port("matrix")},
            {"comparison": _port("artifact")},
            regression.continuous_hinge_bic,
            output_contracts.mapping_output_validator("comparison"),
        ),
        (
            "optimization.constrained_quadratic_multistart",
            {"data": _port("vector")},
            {"solution": _port("artifact")},
            optimization.constrained_quadratic_multistart,
            output_contracts.mapping_output_validator("solution"),
        ),
        (
            "optimization.milp",
            {},
            {"solution": _port("artifact")},
            milp.solve_milp,
            milp.valid_milp_output,
        ),
        (
            "decision.mcda_weighted_ranking",
            {"matrix": _port("matrix")},
            {"decision": _port("artifact")},
            decision.mcda_weighted_ranking,
            output_contracts.mapping_output_validator("decision"),
        ),
        (
            "dynamics.affine_ode_rk4",
            {"time": _port("vector"), "states": _port("matrix")},
            {"simulation": _port("artifact")},
            dynamics.affine_ode_rk4,
            output_contracts.mapping_output_validator("simulation"),
        ),
        (
            "graph.shortest_path_max_flow",
            {"metrics": _port("matrix")},
            {"analysis": _port("artifact")},
            graph.shortest_path_max_flow,
            output_contracts.mapping_output_validator("analysis"),
        ),
        (
            "diagnostics.design_rank",
            {"matrix": _port("matrix")},
            {"diagnostic": _port("artifact")},
            diagnostics.design_rank,
            output_contracts.mapping_output_validator("diagnostic"),
        ),
        (
            "diagnostics.leave_one_out",
            {"x": _port("vector"), "y": _port("vector")},
            {"diagnostic": _port("artifact")},
            diagnostics.leave_one_out,
            output_contracts.mapping_output_validator("diagnostic"),
        ),
        (
            "plot.xy_evidence",
            {
                "x": _port("vector"),
                "observed": _port("vector"),
                "predicted": _port("vector"),
            },
            {"figure": _port("artifact")},
            plotting.xy_evidence,
            output_contracts.mapping_output_validator("figure"),
        ),
    ]
    for operator_id, inputs, outputs, executor, validator in registrations:
        registry.register(
            OperatorDefinition(
                operator_id=operator_id,
                version="1.0.0",
                inputs=inputs,
                outputs=outputs,
                preconditions=[],
                deterministic=True,
                validator_id=f"{operator_id}.validator.v1",
            ),
            executor=executor,
            validator=validator,
        )
    return registry
