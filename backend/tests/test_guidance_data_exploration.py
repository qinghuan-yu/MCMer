"""H1.3：布局无关的数据探索、明确的变换及原件恢复。"""

from datetime import datetime
import hashlib
import json

from openpyxl import Workbook
import pytest

from app.guidance.operator_catalog import build_default_operator_registry


def _write_book(path, rows, *, sheet="Observations"):
    book = Workbook()
    page = book.active
    page.title = sheet
    for row in rows:
        page.append(row)
    book.save(path)
    book.close()


def _call(name, root, *, inputs, parameters=None):
    runtime = build_default_operator_registry().runtime(name)
    assert runtime is not None, f"missing registered data exploration capability: {name}"
    return runtime.invoke(inputs=inputs, parameters=parameters or {}, random_seed=None, work_dir=root)


def test_inspection_exposes_raw_cells_and_hash_without_guessing_headers(tmp_path):
    path = tmp_path / "observations.xlsx"
    _write_book(path, [["Title", None], ["Date", "Count"], [datetime(2022, 1, 7), 100]])
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = _call("data.inspect_file", tmp_path, inputs={"source": path.name},
                   parameters={"preview_rows": 2, "preview_columns": 2})["inspection"]
    assert result["source_ref"] == path.name
    assert result["sha256"] == before
    sheet = result["sheets"][0]
    assert (sheet["name"], sheet["rows"], sheet["columns"]) == ("Observations", 3, 2)
    assert sheet["preview"][0] == {"coordinate": "A1", "value": "Title", "type": "text"}
    assert sheet["truncated"] is True
    assert len(sheet["preview"]) == 4
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("title_rows,left_padding", [(0, 0), (2, 1), (4, 3)])
def test_explicit_layout_reads_only_selected_region_and_preserves_json_values(tmp_path, title_rows, left_padding):
    from openpyxl.utils import get_column_letter

    path = tmp_path / "observations.xlsx"
    rows = [["unrelated title"] for _ in range(title_rows)]
    rows += [[None] * left_padding + row for row in [
        ["Date", "Code", "Count"],
        [datetime(2022, 1, 7), "NA", 100],
        [datetime(2022, 1, 8), "None", None],
    ]]
    rows.append(["unrelated footer"])
    _write_book(path, rows)
    region = f"{get_column_letter(left_padding+1)}{title_rows+1}:{get_column_letter(left_padding+3)}{title_rows+3}"
    result = _call("data.read_excel_table", tmp_path, inputs={"source": path.name}, parameters={
        "sheet": "Observations", "header_row": title_rows, "cell_range": region,
    })["table"]
    assert result == {"Date": ["2022-01-07T00:00:00", "2022-01-08T00:00:00"],
                      "Code": ["NA", "None"], "Count": [100, None]}
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("change,expected", [
    ({"header_row": 5}, "header_row"),
    ({"cell_range": "B3:A1"}, "cell_range"),
    ({"cell_range": "A1:Z9"}, "bounds"),
    ({"rename": {"A": "B"}}, "duplicate"),
    ({"columns": ["A", "A"]}, "duplicate"),
    ({"rename": {"missing": "C"}}, "unknown"),
    ({"matrix_columns": ["A"], "matrix_name": "B"}, "collision"),
])
def test_reading_rejects_ambiguous_or_out_of_bounds_transforms(tmp_path, change, expected):
    _write_book(tmp_path / "book.xlsx", [["A", "B"], [1, 2], [3, 4]])
    with pytest.raises(ValueError, match=expected):
        _call("data.read_excel_table", tmp_path, inputs={"source": "book.xlsx"}, parameters=change)


def test_duplicate_source_headers_are_rejected_instead_of_silently_renamed(tmp_path):
    _write_book(tmp_path / "book.xlsx", [["Count", "Count"], [1, 2]])
    with pytest.raises(ValueError, match="duplicate"):
        _call("data.read_excel_table", tmp_path, inputs={"source": "book.xlsx"})


def test_blank_headers_require_an_explicit_region_or_correct_header(tmp_path):
    _write_book(tmp_path / "book.xlsx", [[None, "Count"], [None, 2]])
    with pytest.raises(ValueError, match="blank header"):
        _call("data.read_excel_table", tmp_path, inputs={"source": "book.xlsx"})


def test_named_column_selection_can_exclude_unrelated_blank_header_columns(tmp_path):
    _write_book(tmp_path / "book.xlsx", [["Count", None], [2, None]])
    result = _call("data.read_excel_table", tmp_path, inputs={"source": "book.xlsx"}, parameters={"columns": ["Count"]})
    assert result == {"table": {"Count": [2]}}


def test_empty_tail_policy_is_explicit_and_does_not_remove_internal_missing_rows(tmp_path):
    _write_book(tmp_path / "book.xlsx", [["Count"], [1], [None], [3], [None]])
    result = _call("data.read_excel_table", tmp_path, inputs={"source": "book.xlsx"}, parameters={
        "cell_range": "A1:A5", "trim_trailing_empty_rows": False,
    })["table"]
    assert result["Count"] == [1, None, 3, None]


def test_formula_inspection_preserves_expression_without_claiming_a_computed_value(tmp_path):
    _write_book(tmp_path / "book.xlsx", [["Count"], ["=1+2"]])
    result = _call("data.inspect_file", tmp_path, inputs={"source": "book.xlsx"})["inspection"]
    assert result["sheets"][0]["preview"][1] == {"coordinate": "A2", "value": "=1+2", "type": "formula"}


@pytest.mark.parametrize("name", ["data.inspect_file", "data.read_excel_table"])
def test_file_tools_reject_workspace_escape(tmp_path, name):
    with pytest.raises(ValueError, match="escapes"):
        _call(name, tmp_path, inputs={"source": "../unapproved.xlsx"})


def test_profile_reports_missing_mixed_types_duplicates_and_order_without_cleaning(tmp_path):
    table = {"Date": ["2022-01-08", "2022-01-07", "2022-01-07", None],
             "Count": [120, "unknown", "unknown", None]}
    before = json.dumps(table)
    summary = _call("data.profile_table", tmp_path, inputs={"table": table},
                    parameters={"order_by": "Date"})["profile"]
    assert summary["row_count"] == 4
    assert summary["duplicate_row_count"] == 1
    assert summary["columns"]["Count"]["types"] == {"number": 1, "text": 2, "missing": 1}
    assert summary["columns"]["Count"]["mixed_types"] is True
    assert summary["order"] == {"column": "Date", "direction": "descending", "missing_count": 1}
    assert json.dumps(table) == before
    json.dumps(summary, allow_nan=False)


def test_actual_read_file_changes_invalidate_cached_output_and_record_transforms(tmp_path):
    from app.guidance.model_ir_compiler import ModelIRCompiler
    from app.guidance.operator_execution import OperatorGraphExecutor
    from test_harness_tool_contracts import _reader_model

    _write_book(tmp_path / "actual.xlsx", [["Count"], [100]])
    # 实际读取文件可能来自上游工件，不能仅靠 subproblem.data 的声明缓存。
    model = _reader_model(inputs={"source": "actual.xlsx"}, parameters={"rename": {"Count": "n"}})
    registry = build_default_operator_registry()
    plan = ModelIRCompiler(registry=registry).compile(model).execution_plan
    executor = OperatorGraphExecutor(registry=registry)
    first = executor.execute(execution_plan=plan, model_ir=model, work_dir=tmp_path)
    assert first.status == "passed"
    same = executor.execute(execution_plan=plan, model_ir=model, work_dir=tmp_path)
    assert same.reused_node_ids == ["read"]
    _write_book(tmp_path / "actual.xlsx", [["Count"], [200]])
    changed = executor.execute(execution_plan=plan, model_ir=model, work_dir=tmp_path)
    assert changed.status == "passed"
    assert changed.reused_node_ids == []
    assert changed.input_files_sha256["actual.xlsx"] == hashlib.sha256((tmp_path / "actual.xlsx").read_bytes()).hexdigest()
    assert plan.graph.nodes[0].parameters["rename"] == {"Count": "n"}


def test_execution_verifier_checks_actual_file_inputs_without_promoting_source_registration(tmp_path):
    from app.guidance.model_ir_compiler import ModelIRCompiler
    from app.guidance.model_review_contracts import ApprovedModelIR
    from app.guidance.operator_execution import OperatorGraphExecutor
    from test_guidance_result_provenance import _verify
    from test_harness_tool_contracts import _reader_model

    _write_book(tmp_path / "actual.xlsx", [["Count"], [100]])
    model = _reader_model(inputs={"source": "actual.xlsx"})
    registry = build_default_operator_registry()
    plan = ModelIRCompiler(registry=registry).compile_to_file(model, output_path=tmp_path / "execution_plan.json").execution_plan
    OperatorGraphExecutor(registry=registry).execute(execution_plan=plan, model_ir=model, work_dir=tmp_path)
    (tmp_path / "approved_model_ir.json").write_text(ApprovedModelIR(
        review_status="approved", model_id=model.model_id,
        executable_subproblem_ids=["problem1"], approved_model_ir=model,
    ).model_dump_json(), encoding="utf-8")
    report = _verify(tmp_path)
    assert report["execution_verified"]["status"] == "passed", report["execution_verified"]
    assert report["model_adequate"]["mathematical_review"]["status"] == "not_assessed"
    _write_book(tmp_path / "actual.xlsx", [["Count"], [999]])
    report = _verify(tmp_path)
    assert report["execution_verified"]["status"] == "failed"


def test_symbolic_fact_reports_no_numeric_value_instead_of_a_unit_mismatch():
    from app.guidance.model_ir_drafts import IndexedLinearCoreDraft

    draft = IndexedLinearCoreDraft.model_validate({"index_sets": [{"id": "asset", "values": [1]}], "parameters": [{
        "source_kind": "static", "id": "budget", "index_sets": [],
        "source_fact_ids": ["F_budget"], "binding": {"kind": "fact", "fact_id": "F_budget"},
    }]})
    with pytest.raises(ValueError, match="no numeric value.*M"):
        draft.promote_core(locked_facts=[{"fact_id": "F_budget", "value": "M", "unit": "元"}])
