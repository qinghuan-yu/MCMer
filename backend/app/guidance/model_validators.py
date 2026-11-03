"""数值领域注册与编排入口。"""

from __future__ import annotations

from collections.abc import (
    Iterable,
    Mapping,
)
from typing import Any

from app.guidance.model_validation import (
    dynamics,
    evaluation,
    forecast,
    graph,
    milp,
    optimization,
    regression,
    robustness,
)
from app.guidance.model_validation.contracts import (
    ModelValidationAssessment,
    ValidationStatus,
    Validator,
    walk_mappings,
)


class ModelValidatorRegistry:
    def __init__(self) -> None:
        self._validators: dict[str, dict[str, Validator]] = {}

    def register(self, family: str, name: str, validator: Validator) -> None:
        family_validators = self._validators.setdefault(family, {})
        if name in family_validators:
            raise ValueError(f"validator already registered: {family}.{name}")
        family_validators[name] = validator

    def assess(
        self,
        *,
        family: str,
        candidate_roles: set[str],
        evidence: Iterable[Mapping[str, Any]],
    ) -> ModelValidationAssessment:
        validators = self._validators.get(family)
        if validators is None:
            return ModelValidationAssessment(
                status="not_assessed",
                reasons=[f"no validators registered for model family: {family}"],
            )

        evidence_nodes = list(walk_mappings(evidence))
        checks = {
            name: validator(evidence_nodes)
            for name, validator in validators.items()
        }
        reasons: list[str] = []
        required_roles = {"baseline", "recommended"}
        missing_roles = sorted(required_roles - candidate_roles)
        if missing_roles:
            reasons.append(
                "candidate comparison is incomplete; missing roles: "
                + ", ".join(missing_roles)
            )

        failed_checks = [
            name for name, check in checks.items() if check.status == "failed"
        ]
        unassessed_checks = [
            name for name, check in checks.items() if check.status == "not_assessed"
        ]
        if failed_checks:
            reasons.append("failed checks: " + ", ".join(failed_checks))
        if unassessed_checks:
            reasons.append("unassessed checks: " + ", ".join(unassessed_checks))

        if missing_roles or failed_checks:
            status: ValidationStatus = "failed"
        elif unassessed_checks:
            status = "not_assessed"
        else:
            status = "passed"
        return ModelValidationAssessment(status=status, checks=checks, reasons=reasons)

def build_default_model_validator_registry() -> ModelValidatorRegistry:
    registry = ModelValidatorRegistry()
    for family in ("regression", "forecast"):
        registry.register(family, "baseline_comparison", regression.regression_baseline)
        registry.register(family, "residual_recompute", regression.residual_recompute)
        registry.register(family, "cross_validation", regression.cross_validation)
        registry.register(family, "sensitivity", robustness.sensitivity)
        registry.register(family, "extreme_inputs", robustness.extreme_inputs)
    registry.register("forecast", "temporal_leakage", forecast.temporal_leakage)
    registry.register("forecast", "prediction_interval", forecast.prediction_interval)
    registry.register("future_forecast", "rolling_origin_recompute", forecast.future_rolling_origin)
    registry.register("future_forecast", "future_index_check", forecast.future_index)
    registry.register("future_forecast", "error_and_baseline_recompute", forecast.future_errors)
    registry.register("future_forecast", "interval_recompute", forecast.future_interval)
    registry.register("future_forecast", "sensitivity_recompute", forecast.future_sensitivity)

    registry.register("optimization", "baseline_comparison", optimization.optimization_baseline)
    registry.register("optimization", "sensitivity", robustness.sensitivity)
    registry.register("optimization", "extreme_inputs", robustness.extreme_inputs)
    registry.register("optimization", "multistart", optimization.multistart)
    registry.register("optimization", "feasibility_certificate", optimization.optimization_feasibility)
    registry.register("optimization", "bound_certificate", optimization.optimization_bounds)
    registry.register("mixed_integer_linear_optimization", "objective_recompute", milp.milp_objective)
    registry.register(
        "mixed_integer_linear_optimization",
        "feasibility_and_conservation",
        milp.milp_feasibility,
    )
    registry.register("mixed_integer_linear_optimization", "integrality_recompute", milp.milp_integrality)
    registry.register("mixed_integer_linear_optimization", "bound_and_gap", milp.milp_bounds)
    registry.register("mixed_integer_linear_optimization", "baseline_comparison", milp.milp_baseline)
    registry.register("evaluation", "normalization_recompute", evaluation.evaluation_normalization)
    registry.register("evaluation", "weight_recompute", evaluation.evaluation_weights)
    registry.register("evaluation", "ranking_recompute", evaluation.evaluation_ranking)
    registry.register("evaluation", "weight_sensitivity", evaluation.evaluation_sensitivity)
    registry.register("evaluation", "extreme_inputs", robustness.extreme_inputs)
    registry.register("differential_equation", "baseline_comparison", regression.regression_baseline)
    registry.register("differential_equation", "parameter_recompute", dynamics.dynamics_parameters)
    registry.register("differential_equation", "integration_recompute", dynamics.dynamics_integration)
    registry.register("differential_equation", "boundary_check", dynamics.dynamics_boundary)
    registry.register("differential_equation", "conservation_check", dynamics.dynamics_conservation)
    registry.register("differential_equation", "step_sensitivity", dynamics.dynamics_step_sensitivity)
    registry.register("differential_equation", "extreme_inputs", robustness.extreme_inputs)
    registry.register("graph", "baseline_comparison", graph.graph_baseline)
    registry.register("graph", "connectivity_certificate", graph.graph_connectivity)
    registry.register("graph", "shortest_path_certificate", graph.graph_shortest_path)
    registry.register("graph", "flow_certificate", graph.graph_flow)
    registry.register("graph", "min_cut_certificate", graph.graph_min_cut)
    registry.register("graph", "extreme_inputs", robustness.extreme_inputs)
    return registry
