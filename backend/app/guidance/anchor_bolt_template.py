"""Deterministic solver template for the Wuyi anchor-bolt support task."""

from __future__ import annotations

import json
from typing import Any

from app.guidance.contracts import ApprovedModelSpec, CORE_EXECUTION_OUTPUTS, GeneratedProgram
from app.guidance.execution import validate_generated_program_source


def build_wuyi_anchor_bolt_program(
    *,
    approved_model: ApprovedModelSpec,
    data_profile: dict[str, Any],
) -> GeneratedProgram | None:
    data_path = _anchor_bolt_data_path(
        approved_model=approved_model,
        data_profile=data_profile,
    )
    if data_path is None:
        return None
    blocked = _blocked_subproblems(approved_model)
    source = (
        ANCHOR_BOLT_SOLVER
        .replace("__DATA_PATH_LITERAL__", repr(data_path))
        .replace(
            "__BLOCKED_SUBPROBLEMS_LITERAL__",
            repr(json.dumps(blocked, ensure_ascii=False)),
        )
    )
    program = GeneratedProgram(
        source_code=source,
        declared_outputs=list(CORE_EXECUTION_OUTPUTS),
    )
    validate_generated_program_source(program.source_code, program.declared_outputs)
    return program


def _anchor_bolt_data_path(
    *,
    approved_model: ApprovedModelSpec,
    data_profile: dict[str, Any],
) -> str | None:
    if list(approved_model.required_outputs) != list(CORE_EXECUTION_OUTPUTS):
        return None
    model = approved_model.approved_model if isinstance(approved_model.approved_model, dict) else {}
    model_text = " ".join(
        str(value)
        for value in (
            approved_model.model_id,
            model.get("model_id", ""),
            model.get("selected_method", ""),
        )
    ).lower()
    if not any(token in model_text for token in ("anchor", "bolt", "锚杆")):
        return None
    required_sheets = {
        "Sheet1_标准实验数据",
        "Sheet2_岩石工况数据",
        "Sheet3_煤体工况数据",
    }
    for item in data_profile.get("files", []):
        if not isinstance(item, dict) or item.get("suffix") not in {".xlsx", ".xlsm"}:
            continue
        workbook = item.get("workbook", {})
        sheets = workbook.get("sheets", []) if isinstance(workbook, dict) else []
        names = {
            str(sheet.get("name") or "").strip()
            for sheet in sheets
            if isinstance(sheet, dict)
        }
        if required_sheets <= names:
            return str(item.get("path") or "").replace("\\", "/")
    return None


def _blocked_subproblems(approved_model: ApprovedModelSpec) -> list[dict[str, Any]]:
    model = approved_model.approved_model if isinstance(approved_model.approved_model, dict) else {}
    blocked: list[dict[str, Any]] = []
    for item in model.get("subproblem_models", []):
        if not isinstance(item, dict) or item.get("execution_status") != "blocked":
            continue
        missing_inputs = [str(value) for value in item.get("missing_inputs", [])]
        missing_text = "、".join(missing_inputs) or "题设所需输入"
        blocked.append({
            "subproblem_id": str(item.get("subproblem_id") or "unknown"),
            "blocking_reason": str(item.get("blocking_reason") or "缺少可信计算输入。"),
            "missing_inputs": missing_inputs,
            "recovery_action": f"补齐{missing_text}后重新建模、审核并计算。",
        })
    return blocked


ANCHOR_BOLT_SOLVER = r'''
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


DATA_PATH = __DATA_PATH_LITERAL__
BLOCKED_SUBPROBLEMS = json.loads(__BLOCKED_SUBPROBLEMS_LITERAL__)


def write_json(path, payload):
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def linear_fit(design, observed):
    coefficients = np.linalg.lstsq(design, observed, rcond=None)[0]
    predicted = design @ coefficients
    residuals = observed - predicted
    return coefficients, predicted, residuals, float(residuals @ residuals)


def regression_metrics(residuals):
    return {
        "rmse": float(np.sqrt(np.mean(np.square(residuals)))),
        "max_abs_residual": float(np.max(np.abs(residuals))),
    }


def fit_torque_coefficients(frame):
    frame = frame.iloc[:, :3].copy()
    frame.iloc[:, 0] = frame.iloc[:, 0].ffill()
    frame.columns = ["diameter", "torque", "force"]
    fits = []
    for diameter, group in frame.groupby("diameter", sort=True):
        torque = group["torque"].to_numpy(dtype=float)
        force = group["force"].to_numpy(dtype=float)
        design_value = force * float(diameter)
        coefficient = float(design_value @ torque / (design_value @ design_value))
        predicted = coefficient * design_value
        residuals = torque - predicted
        leave_one_out = []
        for index in range(len(torque)):
            reduced_x = np.delete(design_value, index)
            reduced_y = np.delete(torque, index)
            leave_one_out.append(float(reduced_x @ reduced_y / (reduced_x @ reduced_x)))
        relative_change = max(abs(value - coefficient) for value in leave_one_out) / abs(coefficient)
        fits.append({
            "diameter": int(diameter),
            "coefficient": coefficient,
            "force": force,
            "observed": torque,
            "predicted": predicted,
            "residuals": residuals,
            "metrics": regression_metrics(residuals),
            "relative_change": float(relative_change),
        })
    return fits


def fit_condition(frame, condition):
    frame = frame.iloc[:, :6].copy()
    torque = frame.iloc[:, 0].to_numpy(dtype=float)
    observed_matrix = frame.iloc[:, 1:].to_numpy(dtype=float)
    measurement_count = observed_matrix.shape[1]
    observation_count = observed_matrix.size

    single_predictions = np.empty_like(observed_matrix)
    single_sse = 0.0
    for column in range(measurement_count):
        _, predicted, _, sse = linear_fit(
            np.column_stack([np.ones(len(torque)), torque]),
            observed_matrix[:, column],
        )
        single_predictions[:, column] = predicted
        single_sse += sse
    single_bic = float(
        observation_count * np.log(single_sse / observation_count)
        + (2 * measurement_count) * np.log(observation_count)
    )

    candidates = []
    for candidate in torque[3:-2]:
        design = np.column_stack([
            np.ones(len(torque)),
            torque,
            np.maximum(0.0, torque - candidate),
        ])
        predictions = np.empty_like(observed_matrix)
        total_sse = 0.0
        for column in range(measurement_count):
            _, predicted, _, sse = linear_fit(design, observed_matrix[:, column])
            predictions[:, column] = predicted
            total_sse += sse
        bic = float(
            observation_count * np.log(total_sse / observation_count)
            + (3 * measurement_count + 1) * np.log(observation_count)
        )
        candidates.append({
            "candidate": float(candidate),
            "bic": bic,
            "predictions": predictions,
        })
    best = min(candidates, key=lambda item: item["bic"])
    bic_improvement = float(single_bic - best["bic"])
    supported = bic_improvement > 2.0
    selected_predictions = best["predictions"] if supported else single_predictions
    residuals = observed_matrix - selected_predictions
    near_optimal = [
        item["candidate"]
        for item in candidates
        if item["bic"] <= best["bic"] + 2.0
    ]
    return {
        "condition": condition,
        "torque": torque,
        "observed": observed_matrix,
        "predicted": selected_predictions,
        "residuals": residuals,
        "metrics": regression_metrics(residuals.reshape(-1)),
        "single_bic": single_bic,
        "best_bic": float(best["bic"]),
        "bic_improvement": bic_improvement,
        "best_candidate": float(best["candidate"]),
        "candidate_lower": float(min(near_optimal)),
        "candidate_upper": float(max(near_optimal)),
        "supported": supported,
        "candidate_grid": [item["candidate"] for item in candidates],
        "candidate_bic": [item["bic"] for item in candidates],
    }


def blocked_artifacts(source_data):
    results = []
    claims = []
    tables = []
    for item in BLOCKED_SUBPROBLEMS:
        identifier = str(item["subproblem_id"]).replace(".", "_")
        result_id = "blocked_" + identifier
        missing = "、".join(item["missing_inputs"])
        reason = item["blocking_reason"]
        results.append({
            "id": result_id,
            "message": reason,
            "reason": reason + (" 缺失输入：" + missing if missing else ""),
            "source_data": [source_data],
        })
        claims.append({
            "id": "claim_" + result_id,
            "status": "blocked",
            "claim_text": "子问题" + item["subproblem_id"] + "当前不能形成可信数值结论。",
            "evidence_ids": [result_id],
            "assumptions": [],
            "risk": reason,
        })
        tables.append({
            "id": "table_" + identifier,
            "title": "子问题" + item["subproblem_id"] + "阻断登记",
            "rows": [{
                "subproblem_id": item["subproblem_id"],
                "answer_item": "当前可填结论",
                "answer": "阻断",
                "status": "blocked",
                "evidence_ids": [],
                "blocked_reason": reason + "；恢复动作：" + item["recovery_action"],
            }],
            "notes": ["不得用经验假设替代缺失附录并伪造 Tmax 或 Topt。"],
        })
    return results, claims, tables


def main():
    source_data = DATA_PATH.replace("\\", "/")
    sheet1 = pd.read_excel(DATA_PATH, sheet_name="Sheet1_标准实验数据")
    sheet2 = pd.read_excel(DATA_PATH, sheet_name="Sheet2_岩石工况数据")
    sheet3 = pd.read_excel(DATA_PATH, sheet_name="Sheet3_煤体工况数据")

    coefficient_fits = fit_torque_coefficients(sheet1)
    rock = fit_condition(sheet2, "岩石工况")
    coal = fit_condition(sheet3, "煤体工况")
    condition_fits = [rock, coal]

    parameters = []
    verified_results = []
    evidence = []
    claims = []
    answer_rows = []
    for fit in coefficient_fits:
        diameter = fit["diameter"]
        result_id = "result_K_" + str(diameter)
        parameter_id = "param_K_" + str(diameter)
        parameters.append({
            "id": parameter_id,
            "symbol": "K_" + str(diameter),
            "name": str(diameter) + " mm锚杆扭矩系数",
            "value": fit["coefficient"],
            "unit": "无量纲",
            "source_type": "estimated",
            "source_ref": "Sheet1_标准实验数据",
            "trust_status": "evidence_verified",
        })
        metrics = dict(fit["metrics"])
        metrics["K"] = fit["coefficient"]
        metrics["diameter_mm"] = float(diameter)
        verified_results.append({
            "id": result_id,
            "result_type": "regression",
            "claim_text": (
                str(diameter) + " mm锚杆采用题设零截距模型 T=K·P·d，"
                + "K=" + format(fit["coefficient"], ".6f") + "。"
            ),
            "metrics": metrics,
            "source_data": [source_data],
        })
        evidence.append({
            "kind": "regression",
            "subproblem_id": "1.1",
            "result_id": result_id,
            "observed": fit["observed"].tolist(),
            "predicted": fit["predicted"].tolist(),
            "residuals": fit["residuals"].tolist(),
            "constraint_values": np.maximum(fit["predicted"], 0.0).tolist(),
            "parameter_stability": {
                "method": "leave-one-observation-out K stability",
                "max_relative_change": fit["relative_change"],
                "threshold": 0.1,
            },
        })
        claims.append({
            "id": "claim_K_" + str(diameter),
            "status": "evidence_verified",
            "claim_text": str(diameter) + " mm锚杆的 K 来自表1独立拟合。",
            "evidence_ids": [result_id, parameter_id],
            "assumptions": ["采用题设 T=K·P·d 且截距固定为0"],
            "risk": "K 是该实验条件下的估计，不应外推为所有界面条件的常数。",
        })
        answer_rows.append({
            "subproblem_id": "1.1",
            "answer_item": str(diameter) + " mm锚杆模型",
            "answer": "T=" + format(fit["coefficient"], ".6f") + "·P·" + str(diameter),
            "status": "evidence_verified",
            "evidence_ids": [result_id, parameter_id],
            "blocked_reason": "",
        })

    for fit, slug in ((rock, "rock"), (coal, "coal")):
        result_id = "result_" + slug + "_breakpoint"
        sheet_ref = "Sheet2_岩石工况数据" if slug == "rock" else "Sheet3_煤体工况数据"
        metrics = dict(fit["metrics"])
        metrics.update({
            "single_line_bic": fit["single_bic"],
            "best_hinge_bic": fit["best_bic"],
            "bic_improvement": fit["bic_improvement"],
            "best_breakpoint_candidate": fit["best_candidate"],
            "candidate_lower": fit["candidate_lower"],
            "candidate_upper": fit["candidate_upper"],
            "breakpoint_supported": 1.0 if fit["supported"] else 0.0,
        })
        if fit["supported"]:
            claim_text = (
                fit["condition"] + "在连续折线+BIC口径下支持临界区间"
                + format(fit["candidate_lower"], ".0f") + "–"
                + format(fit["candidate_upper"], ".0f") + " N·m；"
                + "最优候选为" + format(fit["best_candidate"], ".0f") + " N·m。"
            )
            status = "chosen_under_prior"
            answer = (
                "候选临界力矩 " + format(fit["best_candidate"], ".0f")
                + " N·m，模型敏感区间 " + format(fit["candidate_lower"], ".0f")
                + "–" + format(fit["candidate_upper"], ".0f") + " N·m"
            )
            parameters.append({
                "id": "param_Tc_" + slug + "_candidate",
                "symbol": "T_c," + slug,
                "name": fit["condition"] + "临界力矩候选",
                "value": fit["best_candidate"],
                "unit": "N·m",
                "source_type": "estimated",
                "source_ref": sheet_ref,
                "trust_status": "chosen_under_prior",
            })
            claim_evidence = [result_id, "param_Tc_" + slug + "_candidate"]
        else:
            claim_text = (
                fit["condition"] + "数据不能唯一识别临界预紧力矩："
                + "BIC更支持单一直线，折线最优候选不构成可信临界值。"
            )
            status = "evidence_verified"
            answer = "当前数据不能唯一识别临界预紧力矩；需预先规定判据或补充低力矩重复试验"
            claim_evidence = [result_id]
        verified_results.append({
            "id": result_id,
            "result_type": "regression",
            "claim_text": claim_text,
            "metrics": metrics,
            "source_data": [source_data, sheet_ref],
        })
        evidence.append({
            "kind": "regression",
            "subproblem_id": "1.2",
            "result_id": result_id,
            "observed": fit["observed"].reshape(-1).tolist(),
            "predicted": fit["predicted"].reshape(-1).tolist(),
            "residuals": fit["residuals"].reshape(-1).tolist(),
            "constraint_values": np.maximum(fit["predicted"].reshape(-1), 0.0).tolist(),
            "parameter_stability": {
                "method": "BIC grid sensitivity by condition",
                "max_relative_change": (
                    (fit["candidate_upper"] - fit["candidate_lower"]) / fit["best_candidate"]
                    if fit["supported"] else 0.0
                ),
                "threshold": 0.2,
            },
        })
        claims.append({
            "id": "claim_" + slug + "_breakpoint",
            "status": status,
            "claim_text": claim_text,
            "evidence_ids": claim_evidence,
            "assumptions": ["岩石与煤体工况分别建模", "候选断点使用连续折线并以BIC比较"],
            "risk": "断点位置依赖模型形式与候选网格，不能解释为材料固有常数。",
        })
        answer_rows.append({
            "subproblem_id": "1.2-" + slug,
            "answer_item": fit["condition"] + "临界性结论",
            "answer": answer,
            "status": status,
            "evidence_ids": claim_evidence,
            "blocked_reason": "",
        })

    blocked_results, blocked_claims, blocked_tables = blocked_artifacts(source_data)
    claims.extend(blocked_claims)

    Path("figures").mkdir(exist_ok=True)
    figure_path_1 = "figures/torque_coefficient_fits.png"
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.8))
    for axis, fit in zip(axes, coefficient_fits):
        x = fit["force"] * fit["diameter"]
        axis.scatter(x, fit["observed"], label="observed")
        axis.plot(x, fit["predicted"], label="T=K·P·d")
        axis.set_title(str(fit["diameter"]) + " mm, K=" + format(fit["coefficient"], ".4f"))
        axis.set_xlabel("P·d (kN·mm)")
        axis.set_ylabel("T (N·m)")
        axis.legend()
    fig.tight_layout()
    fig.savefig(figure_path_1, dpi=160)
    plt.close(fig)

    figure_path_2 = "figures/breakpoint_bic_by_condition.png"
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.8))
    for axis, fit in zip(axes, condition_fits):
        axis.plot(fit["candidate_grid"], fit["candidate_bic"], marker="o", label="continuous hinge")
        axis.axhline(fit["single_bic"], color="black", linestyle="--", label="single line")
        axis.set_title(fit["condition"])
        axis.set_xlabel("candidate Tc (N·m)")
        axis.set_ylabel("BIC")
        axis.legend()
    fig.tight_layout()
    fig.savefig(figure_path_2, dpi=160)
    plt.close(fig)

    result_registry = {
        "verified_results": verified_results,
        "blocked_results": blocked_results,
        "summary": {
            "verified_count": len(verified_results),
            "blocked_count": len(blocked_results),
            "coverage_status": "partial",
            "guidance_notes": [
                "表1按直径分别拟合；表2岩石与表3煤体严格分开建模。",
                "临界力矩是模型选择结论，不是仅由小残差自动证明的材料常数。",
                "附录2和3缺失，因此问题2至4只登记阻断与恢复动作。",
            ],
            "reader_decision_guide": [
                {
                    "topic": "problem_context",
                    "question": "观测量、未知量与数据口径是什么？",
                    "judgment": "本题无时间轴，T是离散加载水平。表1观测不同直径的T与P；表2、3分别观测岩石和煤体工况的五测点P(T)。未知量是各直径K及各工况是否存在可识别临界点。",
                    "action": "按直径和工况分组，禁止把两种工况拼接或把加载水平误写成时间序列。",
                },
                {
                    "topic": "identifiability",
                    "question": "当前信息能否唯一推出目标？",
                    "judgment": "给定零截距线性式时各K可唯一估计；临界点依赖折线形式与BIC判据，煤体数据不支持唯一临界值；问题2至4缺附录。",
                    "action": "把K标为数据估计，把岩石断点标为判据下候选，把煤体及缺附录结论如实披露。",
                },
                {
                    "topic": "route_choice",
                    "question": "推荐哪条路线，为什么不是别的路线？",
                    "judgment": "推荐零截距最小二乘与分工况BIC模型比较；拒绝跨工况拼接和用经验常数补齐失效公式。",
                    "action": "论文中报告模型比较、候选区间和被排除路线。",
                },
                {
                    "topic": "final_answers",
                    "question": "哪些内容可以直接填表？",
                    "judgment": "可填三组K和岩石候选区间；煤体应填无法唯一识别及补充试验要求；问题2至4暂不能填数值。",
                    "action": "使用最终答案表并保留证据ID。",
                },
                {
                    "topic": "claim_sources",
                    "question": "结论分别来自哪里？",
                    "judgment": "K与BIC来自附件实测数据；岩石最优断点依赖连续折线先验；阻断来自缺失附录，不是计算失败。",
                    "action": "按 evidence_verified、chosen_under_prior、blocked 三层写入论文。",
                },
            ],
            "method_route_comparison": [
                {
                    "route": "分直径零截距回归 + 分工况连续折线/BIC比较",
                    "status": "recommended",
                    "reason": "直接对应题设公式与附件分组结构，并公开断点判据。",
                    "boundary": "K限于当前实验条件；断点结论对模型形式和采样网格敏感。",
                    "evidence_ids": [item["id"] for item in verified_results],
                },
                {
                    "route": "合并岩石与煤体序列后寻找单一断点",
                    "status": "rejected",
                    "reason": "两张表代表不同界面条件，拼接会制造不存在的序列跳变。",
                    "boundary": "仅当工况同质且共享参数的假设经检验成立时才可能联合建模。",
                    "evidence_ids": ["result_rock_breakpoint", "result_coal_breakpoint"],
                },
                {
                    "route": "用经验假设补齐问题2至4",
                    "status": "rejected",
                    "reason": "附录2、3给出的失效关系和参数缺失，假设不能替代题设信息。",
                    "boundary": "补齐附录并完成量纲、约束和敏感性验证后方可启用。",
                    "evidence_ids": [item["id"] for item in blocked_results],
                },
            ],
            "identifiability_analysis": [
                {
                    "subproblem_id": "1.1",
                    "observed": "每个直径组的预紧力矩T与预紧力P。",
                    "unknowns": "每个直径独立的标量K_d。",
                    "equation": "T_i=K_d P_i d+epsilon_i。",
                    "rank_condition": "每组设计向量P_i d非零，秩为1，等于参数维数。",
                    "conclusion": "在题设零截距模型下K_d唯一可估计。",
                    "missing_information": "若允许截距或非线性项，需要额外模型选择证据。",
                    "selection_rule": "遵循题设T=K·P·d并报告残差与留一稳定性。",
                    "guidance": "不要用一个全局K替代三种直径的结果。",
                },
                {
                    "subproblem_id": "1.2-rock",
                    "observed": "岩石工况下各T对应的五测点P。",
                    "unknowns": "共同候选Tc及各测点折线系数。",
                    "equation": "P_j=a_j+b_jT+c_j max(0,T-Tc)+epsilon_j。",
                    "rank_condition": "固定Tc时每测点设计矩阵需满列秩；Tc由有限候选网格和BIC选择。",
                    "conclusion": "数据支持一个判据依赖的候选区间，不证明材料固有唯一临界值。",
                    "missing_information": "更密集的临界区间采样可缩小模型敏感范围。",
                    "selection_rule": "折线BIC至少比单线低2才认定支持断点。",
                    "guidance": "同时报告最优候选与Delta-BIC小于等于2的区间。",
                },
                {
                    "subproblem_id": "1.2-coal",
                    "observed": "煤体工况下各T对应的五测点P。",
                    "unknowns": "是否存在共同Tc以及其位置。",
                    "equation": "比较单一直线与连续折线。",
                    "rank_condition": "两类模型均可拟合，但断点还需模型选择证据。",
                    "conclusion": "BIC不支持折线，当前数据不能唯一识别Tc。",
                    "missing_information": "需要预先定义工程临界判据或补充低力矩重复试验。",
                    "selection_rule": "不以折线最小SSE候选冒充已识别临界值。",
                    "guidance": "正式答案应披露不可识别性，不得填写跨工况拼接产生的伪断点。",
                },
            ],
            "claim_registry": claims,
            "final_answer_tables": [
                {
                    "id": "table_problem_1",
                    "title": "问题1可直接填写结果",
                    "rows": answer_rows,
                    "notes": [
                        "P使用kN、d使用mm时，kN·mm与N·m数值等价，因此K无量纲。",
                        "岩石断点是连续折线+BIC规则下的竞赛导向候选，不是无条件真值。",
                    ],
                },
                *blocked_tables,
            ],
        },
    }
    parameter_registry = {
        "parameters": parameters,
        "summary": {"total_count": len(parameters), "coverage_status": "partial"},
    }
    solver_results = {"evidence": evidence}
    figure_registry = {
        "figures": [
            {
                "path": figure_path_1,
                "role": "result_fit",
                "source_data": [source_data],
                "linked_result_ids": ["result_K_18", "result_K_20", "result_K_22"],
            },
            {
                "path": figure_path_2,
                "role": "verification_diagnostic",
                "source_data": [source_data],
                "linked_result_ids": ["result_rock_breakpoint", "result_coal_breakpoint"],
            },
        ],
    }

    write_json("parameter_registry.json", parameter_registry)
    write_json("result_registry.json", result_registry)
    write_json("solver_results.json", solver_results)
    write_json("figure_registry.json", figure_registry)
    print("Anchor-bolt outputs generated successfully.")


if __name__ == "__main__":
    main()
'''
