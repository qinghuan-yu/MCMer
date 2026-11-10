"""离线验收 H1.3 的公开附件读取；不调用模型，也不宣称整题求解。"""

from __future__ import annotations

import argparse
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import shutil
import sys

from openpyxl import Workbook, load_workbook

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

from app.guidance.model_ir_compiler import ModelIRCompiler
from app.guidance.model_ir_drafts import IndexedLinearCoreDraft
from app.guidance.operator_catalog import build_default_operator_registry
from app.guidance.operator_execution import OperatorGraphExecutor
from test_harness_tool_contracts import _reader_model


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)


def verify(root: Path, workbook: Path, finance: Path):
    root.mkdir(parents=True, exist_ok=False)
    (root / "data").mkdir()
    source = root / "data/book.xlsx"
    shutil.copyfile(workbook, source)
    finance_files = [finance / name for name in (
        "problem_spec.json", "evidence_profile.json",
        "contract_diagnostics/GuidanceModeler_problem1_indexed_core.json",
    )]
    snapshots = {str(path): digest(path) for path in [workbook, *finance_files]}
    registry = build_default_operator_registry()

    def invoke(name, inputs, parameters):
        return registry.runtime(name).invoke(inputs=inputs, parameters=parameters, random_seed=None, work_dir=root)

    inspection = invoke("data.inspect_file", {"source": "data/book.xlsx"}, {})["inspection"]
    selection = {"sheet": "Sheet1", "header_row": 1,
                 "cell_range": f"B2:M{inspection['sheets'][0]['rows']}"}
    table = invoke("data.read_excel_table", {"source": "data/book.xlsx"}, selection)["table"]
    profile = invoke("data.profile_table", {"table": table}, {"order_by": "Date"})["profile"]

    # 独立逐单元格核对原件；不通过生产 reader 生成参考答案。
    book = load_workbook(workbook, read_only=True, data_only=False)
    try:
        raw = list(book["Sheet1"].iter_rows(min_row=2, max_row=book["Sheet1"].max_row,
                                          min_col=2, max_col=13, values_only=True))
        assert not any(isinstance(value, str) and value.startswith("=") for row in raw for value in row)
    finally:
        book.close()
    while raw and all(value is None for value in raw[-1]):
        raw.pop()
    headers, observations = raw[0], raw[1:]
    assert len(observations) == 359
    expected = {name: [row[index].isoformat() if isinstance(row[index], (date, datetime)) else row[index]
                       for row in observations] for index, name in enumerate(headers)}
    assert table == expected
    assert profile["row_count"] == 359
    assert profile["order"]["direction"] == "descending"

    # 改工作表名、标题行数和左右位置，保持原件记录内容和顺序。
    variant = Workbook()
    page = variant.active
    page.title = "Relocated observations"
    for label in ["Layout probe", "A second title", "A third title"]:
        page.append([label])
    for row in raw:
        page.append([None, None, *row])
    variant.save(root / "data/relocated.xlsx")
    variant.close()
    relocated = invoke("data.read_excel_table", {"source": "data/relocated.xlsx"}, {
        "sheet": "Relocated observations", "header_row": 3, "cell_range": "C4:N363",
    })["table"]
    assert relocated == expected

    model = _reader_model(inputs={"source": "data/book.xlsx"}, parameters=selection)
    compiled = ModelIRCompiler(registry=registry).compile_to_file(model, output_path=root / "execution_plan.json")
    assert compiled.status == "compiled", compiled.diagnostics
    executor = OperatorGraphExecutor(registry=registry)
    manifest = executor.execute(execution_plan=compiled.execution_plan, model_ir=model, work_dir=root)
    assert manifest.status == "passed", manifest.failure
    reused = executor.execute(execution_plan=compiled.execution_plan, model_ir=model, work_dir=root)
    assert reused.reused_node_ids == ["read"]
    assert manifest.input_files_sha256["data/book.xlsx"] == snapshots[str(workbook)]

    counts = table["Number of  reported results"]
    words = table["Word"]
    percent_names = list(headers[5:])
    percent_totals = [sum(table[name][index] for name in percent_names) for index in range(359)]
    anomalies = {
        "word_length_not_five": [{"excel_row": index + 3, "word": word} for index, word in enumerate(words) if len(word) != 5],
        "percent_total_not_100_count": sum(value != 100 for value in percent_totals),
        "percent_total_range": [min(percent_totals), max(percent_totals)],
        "percent_total_beyond_nearest_integer_rounding": [
            {"excel_row": index + 3, "total": total} for index, total in enumerate(percent_totals)
            if abs(total - 100) > len(percent_names) * 0.5
        ],
        "note": "Seven independently rounded percentages allow at most 3.5 total points of error; larger deviations need source review. No cells were corrected.",
    }
    facts = read_json(finance_files[0])["locked_facts"]
    evidence = read_json(finance_files[1])
    attempts = read_json(finance_files[2])["attempts"]
    core = None
    for attempt in attempts:
        try:
            core = IndexedLinearCoreDraft.model_validate(json.loads(attempt["raw_response"]))
            break
        except (ValueError, KeyError, TypeError):
            continue
    assert core is not None, "No complete original indexed-core response is available"
    parameter_results = []
    for parameter in core.parameters:
        try:
            promoted = core.model_copy(update={"parameters": [parameter]}).promote_core(locked_facts=facts, evidence_profile=evidence)
            parameter_results.append({"id": parameter.id, "status": "passed",
                                      "values": [value.value for value in promoted.parameters[0].values]})
        except ValueError as error:
            parameter_results.append({"id": parameter.id, "status": "blocked", "error": str(error)})
    blocked = [item for item in parameter_results if item["status"] == "blocked"]
    assert len(parameter_results) == 18 and len(blocked) == 1
    assert blocked[0]["id"] == "M" and "no numeric value" in blocked[0]["error"]
    assert all(digest(Path(path)) == value for path, value in snapshots.items())
    report = {
        "scope": "Offline data-entry acceptance only; no API calls, no model-generated repair, no numerical solution claim.",
        "source_sha256": snapshots, "operator_contract_sha256": registry.contract_sha256(),
        "record_count": len(counts), "selected_region": selection,
        "all_cells_equal_independent_read": True, "relocated_layout_equal": True,
        "execution_status": manifest.status, "reused_node_ids": reused.reused_node_ids,
        "anomalies": anomalies, "finance_original_response": parameter_results,
    }
    for name, value in [("inspection", inspection), ("table", table), ("profile", profile), ("acceptance", report)]:
        write_json(root / f"{name}.json", value)
    print(json.dumps({"output": str(root), "record_count": 359, "layout_probe": "passed",
                      "anomalies": anomalies, "finance_numeric_parameters": 17, "symbolic_M": blocked[0]["error"]}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workbook", type=Path, default=BACKEND / ".local/20261110-public-contest-baseline/sources/Problem_C_Data_Wordle.xlsx")
    parser.add_argument("--finance-dir", type=Path, default=BACKEND / ".local/20261110-public-contest-finance/live/cumcm1998a_portfolio")
    args = parser.parse_args()
    verify(args.output_dir.resolve(), args.workbook.resolve(), args.finance_dir.resolve())
