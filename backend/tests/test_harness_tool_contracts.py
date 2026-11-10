"""H1 的执行合同反例；目标实现前保留真实 RED，不改生产行为。"""

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


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="H1.2: parameter contract pending; replay with --runxfail")
def test_reader_contract_rejects_unsupported_feature_generation_before_execution():
    result = _compile(parameters={"sheet": 0, "derive_features": ["word_length"]})
    assert result.status == "blocked", (
        "An unimplemented transformation must not silently survive compilation."
    )


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="H1.2: parameter contract pending; replay with --runxfail")
def test_reader_contract_rejects_invalid_parameter_type_before_execution():
    result = _compile(parameters={"sheet": {"guessed_name": "Sheet1"}})
    assert result.status == "blocked", (
        "The executable contract must reject an invalid worksheet selector."
    )


@pytest.mark.xfail(strict=True, raises=KeyError, reason="H1.3: explicit header selection pending; replay with --runxfail")
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
    result = operator.executor(
        inputs={"source": "book.xlsx"},
        parameters={"header_row": 1, "columns": ["Date", "Count"]},
        random_seed=None, work_dir=tmp_path,
    )
    assert result == {"table": {"Date": ["2022-01-07", "2022-01-08"],
                                "Count": [100, 120]}}
