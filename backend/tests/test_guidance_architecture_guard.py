from pathlib import Path


GUIDANCE_PACKAGE = Path(__file__).parents[1] / "app" / "guidance"


def test_production_guidance_has_no_whole_problem_template_entrypoints() -> None:
    forbidden_paths = [
        GUIDANCE_PACKAGE / "program_templates.py",
        *GUIDANCE_PACKAGE.glob("*_template.py"),
    ]
    existing = sorted(
        path.relative_to(GUIDANCE_PACKAGE).as_posix()
        for path in set(forbidden_paths)
        if path.exists()
    )

    assert existing == [], (
        "whole-problem template entrypoints must be replaced by Model IR operators: "
        + ", ".join(existing)
    )

    workflow_source = (GUIDANCE_PACKAGE / "workflow.py").read_text(encoding="utf-8")
    assert "TemplateProgramGenerator" not in workflow_source


def test_unknown_capability_is_blocked_as_missing_operator_without_llm() -> None:
    from app.guidance.model_ir import (
        CandidateModel,
        DataReference,
        EquationSpec,
        FinalAnswerSpec,
        ModelIR,
        OperatorInvocation,
        SolvableModelIR,
        SymbolExpression,
        ValidationPlan,
        VariableSpec,
    )
    from app.guidance.model_ir_compiler import ModelIRCompiler
    from app.guidance.operators import OperatorRegistry

    model_ir = ModelIR(
        schema_version="1.0",
        model_id="unseen_inverse_problem",
        subproblems=[
            SolvableModelIR(
                execution_status="solvable",
                subproblem_id="problem1",
                objective="Estimate a latent field from indirect observations.",
                variables=[
                    VariableSpec(
                        id="observations",
                        role="observed",
                        symbol="y",
                        unit="dimensionless",
                        shape=["sample"],
                    ),
                    VariableSpec(
                        id="latent_field",
                        role="state",
                        symbol="x",
                        unit="dimensionless",
                        shape=["sample"],
                    ),
                ],
                data=[
                    DataReference(
                        id="measurements",
                        source_ref="data/measurements.csv",
                        fields={"observations": "value"},
                    )
                ],
                equations=[
                    EquationSpec(
                        id="inverse_relation",
                        left=SymbolExpression(symbol="observations"),
                        right=SymbolExpression(symbol="latent_field"),
                    )
                ],
                operators=[
                    OperatorInvocation(
                        node_id="inverse_step",
                        operator_id="inverse.spectral_reconstruction",
                        inputs={"observations": "data.measurements"},
                        outputs=["latent_field"],
                    )
                ],
                candidate_models=[
                    CandidateModel(
                        id="direct_baseline",
                        role="baseline",
                        operator_node_ids=["inverse_step"],
                        selection_metric="reconstruction_error",
                    ),
                    CandidateModel(
                        id="spectral_candidate",
                        role="recommended",
                        operator_node_ids=["inverse_step"],
                        selection_metric="reconstruction_error",
                    ),
                ],
                validation=ValidationPlan(
                    identifiability_checks=["operator_rank"],
                    evidence_checks=["reconstruction_error"],
                    robustness_checks=["perturbation"],
                ),
                final_answers=[
                    FinalAnswerSpec(
                        id="latent_answer",
                        item="Estimated latent field",
                        value_ref="latent_field",
                        status="evidence_verified",
                        evidence_ids=["latent_field"],
                    )
                ],
            )
        ],
    )
    compiler = ModelIRCompiler(registry=OperatorRegistry())

    result = compiler.compile(model_ir)

    assert result.status == "blocked"
    assert result.execution_plan is None
    assert len(result.blocked_subproblems) == 1
    blocked = result.blocked_subproblems[0]
    assert blocked.subproblem_id == "problem1"
    assert blocked.missing_operator_ids == ["inverse.spectral_reconstruction"]
    assert blocked.recovery_action == (
        "Register and verify the missing operators, then recompile the approved Model IR."
    )
    assert not hasattr(compiler, "llm")
