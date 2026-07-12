import asyncio
import json

import pytest
from openpyxl import Workbook


REQUIRED_OUTPUTS = [
    "solver_results.json",
    "parameter_registry.json",
    "result_registry.json",
    "figure_registry.json",
]


def _approved_anchor_model():
    from app.guidance.contracts import ApprovedModelSpec

    solvable = []
    for subproblem_id, objective in (
        ("1.1", "按不同直径分别建立 T=K P d 模型并给出 K。"),
        ("1.2", "分别利用岩石和煤体工况数据判断临界预紧力矩。"),
    ):
        solvable.append({
            "execution_status": "solvable",
            "subproblem_id": subproblem_id,
            "objective": objective,
            "selected_method": "约束回归与分段模型比较",
            "rationale": "真实工作簿提供了可复算观测。",
            "equations": ["T=K P d", "P=a+bT+c max(0,T-Tc)"],
            "parameters": [{
                "parameter_id": f"theta_{subproblem_id}",
                "symbol": "theta",
                "meaning": "回归参数组",
                "unit": "mixed",
                "source_type": "estimated",
                "source_detail": "附件表1-3",
                "estimation_method": "最小二乘与BIC",
                "constraints": ["设计矩阵满秩并检查模型选择稳定性"],
            }],
            "constraints": ["预测预紧力非负"],
            "solve_steps": ["按工况分别拟合，禁止合并岩石和煤体序列。"],
            "expected_results": ["分直径K", "分工况临界性结论"],
        })
    blocked = [
        {
            "execution_status": "blocked",
            "subproblem_id": subproblem_id,
            "objective": "建立失效约束模型。",
            "blocking_reason": reason,
            "missing_inputs": missing,
            "recovery_action": "补充缺失附录后重新建模与计算。",
            "expected_results": ["阻断原因与恢复动作"],
        }
        for subproblem_id, reason, missing in (
            ("2.1", "缺少附录2物理模型与工程约束。", ["附录2"]),
            ("2.2", "依赖被阻断的2.1。", ["附录2", "2.1模型"]),
            ("3.1", "缺少附录3偏心受力模型。", ["附录3"]),
            ("3.2", "依赖被阻断的2.1和3.1。", ["附录2", "附录3"]),
            ("problem4", "缺少各失效约束及其参数。", ["附录2", "附录3"]),
        )
    ]
    blocked[1]["recovery_action"] = "使用简化工程经验公式估算"
    return ApprovedModelSpec(
        review_status="approved",
        model_id="anchor_bolt_modeling",
        required_outputs=REQUIRED_OUTPUTS,
        approved_model={
            "model_id": "anchor_bolt_modeling",
            "selected_method": "分直径回归与分工况临界性检验",
            "subproblem_models": [*solvable, *blocked],
        },
    )


def _write_anchor_workbook(path):
    workbook = Workbook()
    sheet1 = workbook.active
    sheet1.title = "Sheet1_标准实验数据"
    sheet1.append(["锚杆直径 /mm", "预紧力矩 T /N·m", "预紧力 P/kN"])
    forces = {
        18: [12.68, 33.67, 65.10, 67.34, 89.61, 96.90, 115.74],
        20: [10.46, 25.91, 47.47, 55.56, 69.44, 80.21, 93.09],
        22: [9.31, 23.07, 39.41, 46.62, 59.81, 72.92, 82.43],
    }
    for diameter, values in forces.items():
        for index, (torque, force) in enumerate(zip(range(50, 351, 50), values)):
            sheet1.append([diameter if index == 0 else None, torque, force])

    condition_data = {
        "Sheet2_岩石工况数据": [
            [25, 5.09, 4.35, 3.73, 3.64, 3.00],
            [50, 12.08, 9.48, 8.42, 8.37, 7.26],
            [75, 19.01, 15.79, 14.01, 13.23, 11.73],
            [100, 27.57, 23.24, 20.06, 19.08, 16.53],
            [125, 35.15, 31.25, 27.00, 26.53, 23.12],
            [150, 46.08, 40.11, 33.34, 32.71, 28.18],
            [175, 56.72, 43.45, 39.55, 38.63, 34.08],
            [200, 64.19, 50.52, 46.19, 43.36, 37.84],
            [225, 65.53, 55.90, 50.31, 48.35, 40.77],
            [250, 76.58, 62.95, 55.94, 51.57, 44.26],
            [275, 80.87, 69.15, 59.67, 56.84, 48.44],
            [300, 87.81, 78.09, 65.64, 63.29, 54.22],
        ],
        "Sheet3_煤体工况数据": [
            [25, 5.57, 4.14, 3.79, 3.41, 2.20],
            [50, 11.40, 10.00, 8.76, 7.23, 5.02],
            [75, 17.98, 15.32, 12.46, 11.01, 7.47],
            [100, 24.29, 20.03, 17.78, 14.34, 10.60],
            [125, 30.06, 26.24, 21.74, 18.88, 14.32],
            [150, 38.35, 31.51, 28.19, 23.96, 16.93],
            [175, 42.53, 34.38, 29.65, 24.31, 18.09],
            [200, 47.70, 37.92, 33.20, 27.49, 18.39],
            [225, 50.60, 41.74, 35.66, 33.77, 22.38],
            [250, 59.42, 46.40, 39.82, 35.77, 24.44],
            [275, 64.00, 52.77, 43.12, 39.41, 25.20],
            [300, 70.18, 55.25, 49.33, 45.28, 26.87],
            [325, 75.14, 63.40, 54.79, 46.65, 31.47],
            [350, 81.95, 65.90, 61.28, 52.06, 34.26],
            [375, 86.81, 72.48, 65.17, 52.50, 37.64],
        ],
    }
    for name, rows in condition_data.items():
        sheet = workbook.create_sheet(name)
        sheet.append(["预紧力矩 T/N·m", "测点1", "测点2", "测点3", "测点4", "测点5"])
        for row in rows:
            sheet.append(row)
    workbook.save(path)


def _data_profile():
    return {
        "files": [{
            "path": "data/A-attachment.xlsx",
            "suffix": ".xlsx",
            "workbook": {"sheets": [
                {"name": "Sheet1_标准实验数据"},
                {"name": "Sheet2_岩石工况数据"},
                {"name": "Sheet3_煤体工况数据"},
            ]},
        }],
    }


def test_template_program_generator_blocks_unregistered_problem_before_execution(tmp_path):
    from app.guidance.contracts import ApprovedModelSpec
    from app.guidance.program_templates import (
        TemplateProgramGenerator,
        UnsupportedProgramTemplateError,
    )

    generator = TemplateProgramGenerator()
    model = ApprovedModelSpec(
        review_status="approved",
        model_id="unknown-model",
        required_outputs=REQUIRED_OUTPUTS,
        approved_model={"subproblem_models": []},
    )

    with pytest.raises(UnsupportedProgramTemplateError, match="unknown-model"):
        asyncio.run(generator.generate(
            approved_model=model,
            task={},
            work_dir=tmp_path,
            data_profile={"files": []},
        ))

    assert not hasattr(generator, "llm")


def test_anchor_template_separates_conditions_and_passes_typed_verification(tmp_path):
    from app.guidance.artifacts import ParameterRegistry, ResultRegistry
    from app.guidance.execution import LocalProgramExecutor
    from app.guidance.program_templates import TemplateProgramGenerator
    from app.guidance.stages import ResultVerificationStage

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write_anchor_workbook(data_dir / "A-attachment.xlsx")
    approved_model = _approved_anchor_model()
    (tmp_path / "approved_model_spec.json").write_text(
        json.dumps(approved_model.model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "model_review.json").write_text(
        json.dumps({
            "model_id": approved_model.model_id,
            "status": "approved",
            "dimensional_consistency": "consistent; T uses N·m, P uses kN, d uses mm",
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    program = asyncio.run(TemplateProgramGenerator().generate(
        approved_model=approved_model,
        task={"work_dir": str(tmp_path)},
        work_dir=tmp_path,
        data_profile=_data_profile(),
    ))
    assert "combined_torques" not in program.source_code
    assert "np.concatenate" not in program.source_code
    assert "简化工程经验公式" not in program.source_code

    manifest = asyncio.run(LocalProgramExecutor().execute(
        task_id="anchor-template",
        work_dir=tmp_path,
        approved_model=approved_model,
        program=program,
    ))
    report_path = asyncio.run(ResultVerificationStage().run(
        task_id="anchor-template",
        task={"work_dir": str(tmp_path)},
        input_path=tmp_path / "execution_manifest.json",
        output_path=tmp_path / "verification_report.json",
    ))

    assert manifest.status == "passed"
    assert json.loads(report_path.read_text(encoding="utf-8"))["status"] == "verified"
    parameters = ParameterRegistry.model_validate_json(
        (tmp_path / "parameter_registry.json").read_text(encoding="utf-8")
    )
    values = {item.id: float(item.value) for item in parameters.parameters}
    assert values["param_K_18"] == pytest.approx(0.1620031061)
    assert values["param_K_20"] == pytest.approx(0.1830690460)
    assert values["param_K_22"] == pytest.approx(0.1898679182)
    assert values["param_Tc_rock_candidate"] == pytest.approx(200.0)

    results = ResultRegistry.model_validate_json(
        (tmp_path / "result_registry.json").read_text(encoding="utf-8")
    )
    claims = {item.id: item.claim_text for item in results.verified_results}
    metrics = {item.id: item.metrics for item in results.verified_results}
    source_data = {item.id: item.source_data for item in results.verified_results}
    assert "175" in claims["result_rock_breakpoint"]
    assert "200" in claims["result_rock_breakpoint"]
    assert metrics["result_rock_breakpoint"]["candidate_lower"] == pytest.approx(175.0)
    assert metrics["result_rock_breakpoint"]["candidate_upper"] == pytest.approx(200.0)
    assert "不能唯一识别" in claims["result_coal_breakpoint"]
    assert "325" not in claims["result_coal_breakpoint"]
    assert "Sheet3_煤体工况数据" in source_data["result_coal_breakpoint"]
    problem_context = next(
        item for item in results.summary.reader_decision_guide
        if item.topic == "problem_context"
    )
    assert "无时间轴" in problem_context.judgment
    answer_ids = {
        row.subproblem_id
        for table in results.summary.final_answer_tables
        for row in table.rows
        if "临界性结论" in row.answer_item
    }
    assert answer_ids == {"1.2-rock", "1.2-coal"}
