"""H1 注册合同、表头及恢复边界的行为回归。"""

from pathlib import Path

import pandas as pd
import pytest

from app.guidance.model_ir import ModelIR
from app.guidance.model_ir_compiler import ModelIRCompiler
from app.guidance.operator_catalog import build_default_operator_registry


def _reader_model(*, inputs=None, parameters=None, output="daily_table"):
    return ModelIR.model_validate({
        "model_id": "reader_contract_probe",
        "subproblems": [{
            "execution_status": "solvable",
            "subproblem_id": "problem1",
            "objective": "Read the declared observations without changing their meaning.",
            "assumptions": [],
            "fact_bindings": [],
            "execution_components": [{
                "kind": "operator_graph", "component_id": "reader",
                "variables": [{
                    "id": "count", "role": "observed", "symbol": "n",
                    "unit": "unspecified", "shape": ["sample"],
                }],
                "operators": [{
                    "node_id": "read", "operator_id": "data.read_excel_table",
                    "inputs": inputs if inputs is not None else {"source": "observations"},
                    "outputs": [output], "parameters": parameters or {},
                }],
            }],
            "data": [{"id": "observations", "source_ref": "data/book.xlsx",
                      "fields": {"count": "Count"}}],
            "equations": [],
            "candidate_models": [
                {"id": role, "role": role, "operator_node_ids": ["read"],
                 "selection_metric": "data fidelity",
                 "applicability_boundary": "Declared worksheet observations."}
                for role in ("baseline", "recommended")
            ],
            "validation": {
                "model_families": ["regression"],
                "identifiability_checks": ["read_declared_columns"],
                "evidence_checks": ["compare_source_rows"],
                "robustness_checks": ["preserve_row_order"],
            },
            "final_answers": [{
                "id": "table_answer", "item": "Loaded observations", "value_ref": output,
                "status": "evidence_verified", "evidence_ids": [output],
            }],
        }],
    })


def _compile(**kwargs):
    return ModelIRCompiler(registry=build_default_operator_registry()).compile(
        _reader_model(**kwargs)
    )


def test_existing_compiler_rejects_raw_source_ref_and_sheet_ports():
    result = _compile(inputs={"source_ref": "observations", "sheet": "observations"})
    assert result.status == "blocked"
    assert any(item.code == "input_schema_mismatch" for item in result.diagnostics)


def test_existing_compiler_accepts_an_output_alias():
    result = _compile(output="daily_table_alias")
    assert result.status == "compiled", result.diagnostics
    assert result.execution_plan.graph.nodes[0].outputs == ["daily_table_alias"]


def test_reader_contract_rejects_unsupported_feature_generation_before_execution():
    result = _compile(parameters={"sheet": 0, "derive_features": ["word_length"]})
    assert result.status == "blocked", (
        "An unimplemented transformation must not silently survive compilation."
    )


def test_reader_contract_rejects_invalid_parameter_type_before_execution():
    result = _compile(parameters={"sheet": {"guessed_name": "Sheet1"}})
    assert result.status == "blocked", (
        "The executable contract must reject an invalid worksheet selector."
    )


def test_reader_obeys_explicit_second_row_header(tmp_path: Path):
    workbook = tmp_path / "book.xlsx"
    # 最小双层表头夹具，避免提交官方原题附件或依赖忽略目录。
    pd.DataFrame([
        ["Daily observations", None],
        ["Date", "Count"],
        ["2022-01-07", 100],
        ["2022-01-08", 120],
    ]).to_excel(workbook, header=False, index=False)
    operator = build_default_operator_registry().runtime("data.read_excel_table")
    result = operator.invoke(
        inputs={"source": "book.xlsx"},
        parameters={"header_row": 1, "columns": ["Date", "Count"]},
        random_seed=None, work_dir=tmp_path,
    )
    assert result == {"table": {"Date": ["2022-01-07", "2022-01-08"],
                                "Count": [100, 120]}}


def test_published_reader_parameters_are_executable_json_schema():
    from jsonschema import Draft202012Validator

    definition = build_default_operator_registry().get("data.read_excel_table")
    schema = definition.model_dump(mode="json").get("parameters_schema")
    assert schema is not None, "The model needs the executable parameter contract, not just an ID."
    validator = Draft202012Validator(schema)
    assert not list(validator.iter_errors({"sheet": 0, "columns": ["Date"]}))
    assert list(validator.iter_errors({"sheet": {"name": "Sheet1"}}))
    assert list(validator.iter_errors({"derive_features": ["word_length"]}))


def test_executor_revalidates_parameters_in_a_tampered_execution_plan(tmp_path: Path):
    from app.guidance.operator_execution import OperatorGraphExecutor

    (tmp_path / "data").mkdir()
    pd.DataFrame({"Count": [100, 120]}).to_excel(tmp_path / "data/book.xlsx", index=False)
    registry = build_default_operator_registry()
    model = _reader_model(inputs={"source": "data/book.xlsx"})
    plan = ModelIRCompiler(registry=registry).compile(model).execution_plan
    baseline = OperatorGraphExecutor(registry=registry).execute(
        execution_plan=plan, model_ir=model, work_dir=tmp_path,
    )
    assert baseline.status == "passed", baseline.failure
    plan.graph.nodes[0].parameters["derive_features"] = ["word_length"]
    manifest = OperatorGraphExecutor(registry=registry).execute(
        execution_plan=plan, model_ir=model, work_dir=tmp_path,
    )
    assert manifest.status == "failed", "Loading a plan must not bypass the parameter contract."
    assert manifest.executed_node_ids == []
    assert "derive_features" in manifest.failure.message


def test_legacy_execution_plan_without_operator_contract_digest_is_rejected():
    from app.guidance.model_ir_compiler import ExecutionPlan

    payload = _compile().execution_plan.model_dump(mode="json")
    payload.pop("operator_contract_sha256", None)
    with pytest.raises(ValueError, match="operator_contract_sha256"):
        ExecutionPlan.model_validate(payload)


@pytest.mark.parametrize("change", ["input_ports", "output_count", "contract"])
def test_restored_plan_cannot_bypass_ports_or_changed_parameter_contract(tmp_path, change):
    from app.guidance.operator_execution import OperatorGraphExecutor
    from app.guidance.operator_parameters import ExcelParameters

    pd.DataFrame({"Count": [100, 120]}).to_excel(tmp_path / "book.xlsx", index=False)
    model = _reader_model(inputs={"source": "book.xlsx"})
    registry = build_default_operator_registry()
    compiler = ModelIRCompiler(registry=registry)
    plan = compiler.compile(model).execution_plan
    executor = OperatorGraphExecutor(registry=registry)
    first = executor.execute(execution_plan=plan, model_ir=model, work_dir=tmp_path)
    assert first.status == "passed"
    same = executor.execute(execution_plan=plan, model_ir=model, work_dir=tmp_path)
    assert same.reused_node_ids == ["read"]
    if change == "input_ports":
        plan.graph.nodes[0].inputs["sheet"] = "book.xlsx"
    elif change == "output_count":
        plan.graph.nodes[0].outputs.append("unregistered_output")
    else:
        class UpdatedExcelParameters(ExcelParameters):
            row_limit: int | None = 1
        # 即便维护者没有提升版本号，合同变化也必须使旧计划和缓存失效。
        registry.get("data.read_excel_table").parameter_model = UpdatedExcelParameters
    result = executor.execute(execution_plan=plan, model_ir=model, work_dir=tmp_path)
    assert result.status == "failed"
    assert result.executed_node_ids == result.reused_node_ids == []
    if change == "contract":
        assert "contract digest mismatch" in result.failure.message
        new_plan = compiler.compile(model).execution_plan
        refreshed = executor.execute(execution_plan=new_plan, model_ir=model, work_dir=tmp_path)
        assert refreshed.status == "passed"
        assert refreshed.reused_node_ids == []


def test_mapping_schema_and_runtime_agree_on_exclusive_projection():
    from jsonschema import Draft202012Validator

    definition = build_default_operator_registry().get("artifact.extract_mapping")
    validator = Draft202012Validator(definition.parameters_schema)
    for parameters in ({}, {"path": "a", "fields": {"value": "a"}}, {"fields": {}}):
        assert list(validator.iter_errors(parameters))
        with pytest.raises(ValueError):
            definition.validate_call(inputs={"artifact": {}}, parameters=parameters)
    for parameters in ({"path": "a"}, {"fields": {"value": "a"}}):
        assert not list(validator.iter_errors(parameters))
        definition.validate_call(inputs={"artifact": {}}, parameters=parameters)


def test_parameter_defaults_are_published_and_compiled_from_the_same_contract():
    registry = build_default_operator_registry()
    definition = registry.get("data.read_excel_table")
    plan = ModelIRCompiler(registry=registry).compile(_reader_model()).execution_plan
    normalized = plan.graph.nodes[0].parameters
    for name, field in definition.parameters_schema["properties"].items():
        assert normalized[name] == field["default"]
    assert len(registry.definitions()) == 22
    for definition in registry.definitions():
        with pytest.raises(ValueError, match="undeclared_probe"):
            definition.parameter_model.model_validate({"undeclared_probe": True})
