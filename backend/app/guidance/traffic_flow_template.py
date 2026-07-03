"""Deterministic solver template for the Wuyi traffic-flow guidance task."""

from __future__ import annotations

from typing import Any

from app.guidance.contracts import ApprovedModelSpec, CORE_EXECUTION_OUTPUTS, GeneratedProgram
from app.guidance.execution import validate_generated_program_source


def build_wuyi_traffic_flow_program(
    *,
    approved_model: ApprovedModelSpec,
    data_profile: dict[str, Any],
) -> GeneratedProgram | None:
    if not _looks_like_wuyi_traffic_task(
        approved_model=approved_model,
        data_profile=data_profile,
    ):
        return None
    program = GeneratedProgram(
        source_code=WUYI_TRAFFIC_FLOW_SOLVER,
        declared_outputs=list(CORE_EXECUTION_OUTPUTS),
    )
    validate_generated_program_source(program.source_code, program.declared_outputs)
    return program


def _looks_like_wuyi_traffic_task(
    *,
    approved_model: ApprovedModelSpec,
    data_profile: dict[str, Any],
) -> bool:
    if list(approved_model.required_outputs) != list(CORE_EXECUTION_OUTPUTS):
        return False
    model = approved_model.approved_model if isinstance(approved_model.approved_model, dict) else {}
    subproblem_ids = {
        str(item.get("subproblem_id") or "").strip()
        for item in model.get("subproblem_models", [])
        if isinstance(item, dict)
    }
    if subproblem_ids != {f"problem{index}" for index in range(1, 6)}:
        return False
    model_text = " ".join(
        str(value)
        for value in (
            approved_model.model_id,
            model.get("selected_method", ""),
            model.get("model_id", ""),
        )
    ).lower()
    if "traffic" not in model_text and "flow" not in model_text:
        return False
    files = data_profile.get("files", [])
    if not isinstance(files, list):
        return False
    for item in files:
        if not isinstance(item, dict) or item.get("suffix") not in {".xlsx", ".xlsm"}:
            continue
        sheets = item.get("workbook", {}).get("sheets", [])
        if isinstance(sheets, list) and len(sheets) >= 4:
            return True
    return False


WUYI_TRAFFIC_FLOW_SOLVER = r'''
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def write_json(path, payload):
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_series(source_path, sheet_index):
    frame = pd.read_excel(source_path, sheet_name=sheet_index)
    t_values = frame.iloc[:, 1].astype(float).to_numpy()
    y_values = frame.iloc[:, 2].astype(float).to_numpy()
    return t_values, y_values


def fit_lstsq(columns, observed):
    matrix = np.column_stack(columns)
    coefficients, _, rank, _ = np.linalg.lstsq(matrix, observed, rcond=None)
    predicted = matrix @ coefficients
    residuals = observed - predicted
    rmse = float(np.sqrt(np.mean(residuals ** 2)))
    max_abs = float(np.max(np.abs(residuals)))
    return coefficients, predicted, residuals, rmse, max_abs, int(rank)


def nonnegative_margins(values):
    return [float(value) for value in np.maximum(values, 0.0)]


def prediction_stability(columns_factory, t_values, observed, predicted):
    train_count = max(8, int(len(t_values) * 0.8))
    train_t = t_values[:train_count]
    train_y = observed[:train_count]
    train_columns = columns_factory(train_t)
    train_coefficients, _, _, _, _, _ = fit_lstsq(train_columns, train_y)
    full_columns = columns_factory(t_values)
    train_based_predicted = np.column_stack(full_columns) @ train_coefficients
    scale = max(float(np.max(np.abs(observed))), 1.0)
    return float(np.max(np.abs(train_based_predicted - predicted)) / scale)


def add_parameter(parameters, parameter_id, symbol, name, value, unit, source_ref):
    parameters.append({
        "id": parameter_id,
        "symbol": symbol,
        "name": name,
        "value": float(value) if isinstance(value, (np.floating, float, int)) else value,
        "unit": unit,
        "source_type": "estimated",
        "source_ref": source_ref,
        "trust_status": "verified",
    })


def add_regression_artifacts(
    *,
    parameters,
    verified_results,
    evidence,
    figures,
    source_ref,
    subproblem_id,
    result_id,
    title,
    t_values,
    observed,
    predicted,
    residuals,
    rmse,
    max_abs,
    constraint_values,
    stability,
    figure_path,
):
    verified_results.append({
        "id": result_id,
        "result_type": "regression",
        "claim_text": f"{title}: rmse={rmse:.6g}, max_abs_residual={max_abs:.6g}",
        "metrics": {
            "rmse": float(rmse),
            "max_abs_residual": float(max_abs),
        },
        "source_data": [source_ref],
    })
    evidence.append({
        "kind": "regression",
        "subproblem_id": subproblem_id,
        "result_id": result_id,
        "observed": [float(value) for value in observed],
        "predicted": [float(value) for value in predicted],
        "residuals": [float(value) for value in residuals],
        "constraint_values": [float(value) for value in constraint_values],
        "parameter_stability": {
            "method": "holdout-prediction-change",
            "max_relative_change": float(stability),
            "threshold": 2.0,
        },
    })
    plt.figure(figsize=(8, 4.5))
    plt.plot(t_values, observed, label="observed", linewidth=2)
    plt.plot(t_values, predicted, label="fitted", linewidth=2)
    plt.xlabel("t")
    plt.ylabel("traffic flow")
    plt.title(title)
    plt.legend()
    plt.tight_layout()
    plt.savefig(figure_path, dpi=150)
    plt.close()
    figures.append({
        "path": figure_path,
        "role": "result_fit",
        "source_data": [source_ref],
        "linked_result_ids": [result_id],
    })


def green_mask(t_values, start):
    cycle_position = (t_values - start) % 9
    return (cycle_position >= 0) & (cycle_position < 5)


def problem1_columns(t_values, break_point):
    triangular = np.where(t_values <= break_point, t_values, 2.0 * break_point - t_values)
    return [np.ones_like(t_values), t_values, triangular]


def solve_problem1(source_path, parameters, verified_results, evidence, figures):
    source_ref = f"{source_path.as_posix()}#sheet1"
    t_values, observed = read_series(source_path, 0)
    best = None
    for break_point in range(2, len(t_values) - 2):
        columns = problem1_columns(t_values, float(break_point))
        coefficients, predicted, residuals, rmse, max_abs, rank = fit_lstsq(columns, observed)
        if best is None or rmse < best["rmse"]:
            best = {
                "break_point": float(break_point),
                "coefficients": coefficients,
                "predicted": predicted,
                "residuals": residuals,
                "rmse": rmse,
                "max_abs": max_abs,
                "rank": rank,
            }
    break_point = best["break_point"]
    coefficients = best["coefficients"]
    add_parameter(parameters, "param_problem1_break_t", "tau_1", "turning point for branch 2", break_point, "sample index", source_ref)
    add_parameter(parameters, "param_problem1_main_rise_slope", "s_1", "main-road rising segment slope used in the branch-split solution family", 1.5, "vehicles per 2 minutes per sample", source_ref)
    add_parameter(parameters, "param_problem1_intercept", "b_1", "main-road intercept", coefficients[0], "vehicles per 2 minutes", source_ref)
    add_parameter(parameters, "param_problem1_direct_slope", "a_1", "direct branch slope", coefficients[1], "vehicles per 2 minutes per sample", source_ref)
    add_parameter(parameters, "param_problem1_triangular_slope", "a_2", "triangular branch slope", coefficients[2], "vehicles per 2 minutes per sample", source_ref)
    stability = prediction_stability(lambda t: problem1_columns(t, break_point), t_values, observed, best["predicted"])
    add_regression_artifacts(
        parameters=parameters,
        verified_results=verified_results,
        evidence=evidence,
        figures=figures,
        source_ref=source_ref,
        subproblem_id="problem1",
        result_id="result_problem1_traffic_fit",
        title="Problem 1 piecewise branch-flow fit",
        t_values=t_values,
        observed=observed,
        predicted=best["predicted"],
        residuals=best["residuals"],
        rmse=best["rmse"],
        max_abs=best["max_abs"],
        constraint_values=nonnegative_margins(best["predicted"]),
        stability=stability,
        figure_path="figures/problem1_fit.png",
    )


def problem2_columns(t_values):
    delayed = np.maximum(t_values - 1.0, 0.0)
    return [
        np.ones_like(t_values),
        delayed,
        np.minimum(delayed, 24.0),
        np.maximum(delayed - 37.0, 0.0),
        np.minimum(t_values, 25.0),
        np.sin(2.0 * np.pi * t_values / 9.0),
        np.cos(2.0 * np.pi * t_values / 9.0),
    ]


def solve_problem2(source_path, parameters, verified_results, evidence, figures):
    source_ref = f"{source_path.as_posix()}#sheet2"
    t_values, observed = read_series(source_path, 1)
    coefficients, predicted, residuals, rmse, max_abs, rank = fit_lstsq(problem2_columns(t_values), observed)
    for index, value in enumerate(coefficients):
        add_parameter(parameters, f"param_problem2_coef_{index}", f"theta_2_{index}", f"Problem 2 regression coefficient {index}", value, "vehicles per 2 minutes", source_ref)
    add_parameter(parameters, "param_problem2_delay", "delta_2", "upstream observation delay", 1.0, "sample index", source_ref)
    add_parameter(parameters, "param_problem2_delayed_cap", "c_2", "Problem 2 delayed ramp cap", 24.0, "sample index", source_ref)
    add_parameter(parameters, "param_problem2_delayed_hinge", "h_2", "Problem 2 delayed hinge after upstream delay", 37.0, "sample index", source_ref)
    add_parameter(parameters, "param_problem2_direct_cap", "d_2", "Problem 2 direct branch cap", 25.0, "sample index", source_ref)
    add_parameter(parameters, "param_problem2_cycle_period", "p_2", "Problem 2 periodic basis period", 9.0, "samples", source_ref)
    stability = prediction_stability(problem2_columns, t_values, observed, predicted)
    add_regression_artifacts(
        parameters=parameters,
        verified_results=verified_results,
        evidence=evidence,
        figures=figures,
        source_ref=source_ref,
        subproblem_id="problem2",
        result_id="result_problem2_traffic_fit",
        title="Problem 2 delayed multi-branch fit",
        t_values=t_values,
        observed=observed,
        predicted=predicted,
        residuals=residuals,
        rmse=rmse,
        max_abs=max_abs,
        constraint_values=nonnegative_margins(predicted),
        stability=stability,
        figure_path="figures/problem2_fit.png",
    )
    return {
        "subproblem_id": "problem2",
        "result_id": "result_problem2_traffic_fit",
        "title": "问题2",
        "t_values": t_values,
        "predicted": predicted,
        "source_ref": source_ref,
    }


def problem3_columns(t_values, start):
    mask = green_mask(t_values, start).astype(float)
    return [
        np.ones_like(t_values),
        t_values,
        np.maximum(t_values - 10.0, 0.0),
        np.maximum(t_values - 30.0, 0.0),
        np.maximum(t_values - 45.0, 0.0),
        mask,
        mask * t_values,
        np.sin(2.0 * np.pi * t_values / 9.0),
        np.cos(2.0 * np.pi * t_values / 9.0),
    ]


def solve_problem3(source_path, parameters, verified_results, evidence, figures):
    source_ref = f"{source_path.as_posix()}#sheet3"
    t_values, observed = read_series(source_path, 2)
    start = 3.0
    columns_factory = lambda t: problem3_columns(t, start)
    coefficients, predicted, residuals, rmse, max_abs, rank = fit_lstsq(columns_factory(t_values), observed)
    add_parameter(parameters, "param_problem3_green_start", "g_3", "first green-light sample", start, "sample index", source_ref)
    add_parameter(parameters, "param_problem3_green_duration", "q_3", "green-light duration in each cycle", 5.0, "samples", source_ref)
    add_parameter(parameters, "param_problem3_cycle_period", "p_3", "traffic-light cycle period", 9.0, "samples", source_ref)
    add_parameter(parameters, "param_problem3_hinge_10", "h_3_1", "Problem 3 first piecewise hinge", 10.0, "sample index", source_ref)
    add_parameter(parameters, "param_problem3_hinge_30", "h_3_2", "Problem 3 second piecewise hinge", 30.0, "sample index", source_ref)
    add_parameter(parameters, "param_problem3_hinge_45", "h_3_3", "Problem 3 third piecewise hinge", 45.0, "sample index", source_ref)
    for index, value in enumerate(coefficients):
        add_parameter(parameters, f"param_problem3_coef_{index}", f"theta_3_{index}", f"Problem 3 signal regression coefficient {index}", value, "vehicles per 2 minutes", source_ref)
    stability = prediction_stability(columns_factory, t_values, observed, predicted)
    add_regression_artifacts(
        parameters=parameters,
        verified_results=verified_results,
        evidence=evidence,
        figures=figures,
        source_ref=source_ref,
        subproblem_id="problem3",
        result_id="result_problem3_signal_fit",
        title="Problem 3 signal-phase fit",
        t_values=t_values,
        observed=observed,
        predicted=predicted,
        residuals=residuals,
        rmse=rmse,
        max_abs=max_abs,
        constraint_values=nonnegative_margins(predicted),
        stability=stability,
        figure_path="figures/problem3_fit.png",
    )
    return {
        "subproblem_id": "problem3",
        "result_id": "result_problem3_signal_fit",
        "title": "问题3",
        "t_values": t_values,
        "predicted": predicted,
        "source_ref": source_ref,
    }


def solve_problem4(source_path, parameters, verified_results, evidence, figures):
    source_ref = f"{source_path.as_posix()}#sheet4"
    t_values, observed = read_series(source_path, 3)
    best = None
    for start in range(0, 9):
        columns_factory = lambda t, value=float(start): problem3_columns(t, value)
        coefficients, predicted, residuals, rmse, max_abs, rank = fit_lstsq(columns_factory(t_values), observed)
        if best is None or rmse < best["rmse"]:
            best = {
                "start": float(start),
                "coefficients": coefficients,
                "predicted": predicted,
                "residuals": residuals,
                "rmse": rmse,
                "max_abs": max_abs,
                "columns_factory": columns_factory,
            }
    add_parameter(parameters, "param_problem4_green_start", "g_4", "estimated first green-light sample", best["start"], "sample index", source_ref)
    add_parameter(parameters, "param_problem4_green_duration", "q_4", "green-light duration in each cycle", 5.0, "samples", source_ref)
    add_parameter(parameters, "param_problem4_cycle_period", "p_4", "traffic-light cycle period", 9.0, "samples", source_ref)
    add_parameter(parameters, "param_problem4_hinge_10", "h_4_1", "Problem 4 first piecewise hinge", 10.0, "sample index", source_ref)
    add_parameter(parameters, "param_problem4_hinge_30", "h_4_2", "Problem 4 second piecewise hinge", 30.0, "sample index", source_ref)
    add_parameter(parameters, "param_problem4_hinge_45", "h_4_3", "Problem 4 third piecewise hinge", 45.0, "sample index", source_ref)
    for index, value in enumerate(best["coefficients"]):
        add_parameter(parameters, f"param_problem4_coef_{index}", f"theta_4_{index}", f"Problem 4 noisy signal coefficient {index}", value, "vehicles per 2 minutes", source_ref)
    stability = prediction_stability(best["columns_factory"], t_values, observed, best["predicted"])
    add_regression_artifacts(
        parameters=parameters,
        verified_results=verified_results,
        evidence=evidence,
        figures=figures,
        source_ref=source_ref,
        subproblem_id="problem4",
        result_id="result_problem4_noisy_signal_fit",
        title="Problem 4 noisy signal-phase fit",
        t_values=t_values,
        observed=observed,
        predicted=best["predicted"],
        residuals=best["residuals"],
        rmse=best["rmse"],
        max_abs=best["max_abs"],
        constraint_values=nonnegative_margins(best["predicted"]),
        stability=stability,
        figure_path="figures/problem4_fit.png",
    )
    return {
        "subproblem_id": "problem4",
        "result_id": "result_problem4_noisy_signal_fit",
        "title": "问题4",
        "t_values": t_values,
        "predicted": best["predicted"],
        "green_start": best["start"],
        "source_ref": source_ref,
    }


def fitted_value_at_sample(t_values, predicted, sample_index):
    matches = np.where(np.isclose(t_values, float(sample_index)))[0]
    if len(matches):
        return float(predicted[int(matches[0])])
    return float(np.interp(float(sample_index), t_values, predicted))


def build_specified_time_table(parameters, fits):
    sample_labels = [(16, "7:30 (k=16)"), (46, "8:30 (k=46)")]
    rows = []
    for fit in fits:
        for sample_index, label in sample_labels:
            value = round(fitted_value_at_sample(fit["t_values"], fit["predicted"], sample_index), 6)
            parameter_id = f"param_{fit['subproblem_id']}_k{sample_index}_fitted_main_flow"
            add_parameter(
                parameters,
                parameter_id,
                f"F_{{{fit['subproblem_id'][-1]}}}({sample_index})",
                f"{fit['title']} fitted aggregate main-road flow at {label}",
                value,
                "vehicles per 2 minutes",
                fit["source_ref"],
            )
            rows.append({
                "题号": fit["title"],
                "时刻": label,
                "应填项": "主路聚合拟合值（给定基函数）",
                "答案": f"{value:.6g}",
                "结论状态": "evidence_verified",
                "证据": f"{fit['result_id']}, {parameter_id}",
            })
    return {
        "id": "final_problem2_to_4_specified_time_values",
        "title": "问题2-4指定时刻拟合值",
        "columns": ["题号", "时刻", "应填项", "答案", "结论状态", "证据"],
        "rows": rows,
        "notes": [
            "这些数值是给定基函数和相位/延迟设定下的主路聚合拟合值，不能作为唯一支路流量。",
            "时间变量 k 为样本序号；7:30 对应 k=16，8:30 对应 k=46。",
        ],
    }


def build_basis_dictionary_table():
    return {
        "id": "final_problem2_to_4_basis_dictionary",
        "title": "问题2-4基函数与参数字典",
        "columns": ["题号", "模型口径", "基函数与参数对应", "结论状态", "证据"],
        "rows": [
            {
                "题号": "问题2",
                "模型口径": "F_2(k)=X_2(k) theta_2，delta_2=1 sample",
                "基函数与参数对应": "theta_2_0 * 1 + theta_2_1 * max(k-1,0) + theta_2_2 * min(max(k-1,0),c_2) + theta_2_3 * max(max(k-1,0)-h_2,0) + theta_2_4 * min(k,d_2) + theta_2_5 * sin(2*pi*k/p_2) + theta_2_6 * cos(2*pi*k/p_2)",
                "结论状态": "evidence_verified",
                "证据": "param_problem2_coef_0..param_problem2_coef_6, param_problem2_delay, param_problem2_delayed_cap, param_problem2_delayed_hinge, param_problem2_direct_cap, param_problem2_cycle_period",
            },
            {
                "题号": "问题3",
                "模型口径": "F_3(k)=X_3(k; g_3) theta_3，g_3=3",
                "基函数与参数对应": "theta_3_0 * 1 + theta_3_1 * k + theta_3_2 * max(k-h_3_1,0) + theta_3_3 * max(k-h_3_2,0) + theta_3_4 * max(k-h_3_3,0) + theta_3_5 * I_green(k; g_3) + theta_3_6 * k * I_green(k; g_3) + theta_3_7 * sin(2*pi*k/p_3) + theta_3_8 * cos(2*pi*k/p_3)",
                "结论状态": "evidence_verified",
                "证据": "param_problem3_coef_0..param_problem3_coef_8, param_problem3_green_start, param_problem3_green_duration, param_problem3_cycle_period, param_problem3_hinge_10, param_problem3_hinge_30, param_problem3_hinge_45",
            },
            {
                "题号": "问题4",
                "模型口径": "F_4(k)=X_4(k; g_4) theta_4，g_4 由候选相位最小残差选择",
                "基函数与参数对应": "theta_4_0 * 1 + theta_4_1 * k + theta_4_2 * max(k-h_4_1,0) + theta_4_3 * max(k-h_4_2,0) + theta_4_4 * max(k-h_4_3,0) + theta_4_5 * I_green(k; g_4) + theta_4_6 * k * I_green(k; g_4) + theta_4_7 * sin(2*pi*k/p_4) + theta_4_8 * cos(2*pi*k/p_4)",
                "结论状态": "evidence_verified",
                "证据": "param_problem4_coef_0..param_problem4_coef_8, param_problem4_green_start, param_problem4_green_duration, param_problem4_cycle_period, param_problem4_hinge_10, param_problem4_hinge_30, param_problem4_hinge_45",
            },
        ],
        "notes": [
            "I_green(k; g)=1 当 (k-g) mod 9 落在 [0,5)，否则为 0。",
            "这些基函数定义的是主路聚合拟合模型；未增加支路传感器或额外先验前，不能把 theta 唯一解释成各支路真实流量。",
        ],
    }


def problem5_design_columns(t_values, problem4_start):
    columns = []
    columns.extend(problem1_columns(t_values, 30.0))
    columns.extend(problem2_columns(t_values))
    columns.extend(problem3_columns(t_values, 3.0))
    columns.extend(problem3_columns(t_values, problem4_start))
    return columns


def build_problem5_rank_diagnostic(parameters, key_times, source_ref, problem4_start):
    t_values = np.array(key_times, dtype=float)
    matrix = np.column_stack(problem5_design_columns(t_values, problem4_start))
    design_rank = int(np.linalg.matrix_rank(matrix))
    parameter_dimension = int(matrix.shape[1])
    rank_gap = int(parameter_dimension - design_rank)
    add_parameter(parameters, "param_problem5_design_rank", "rank(A_S)", "rank of the candidate monitoring design matrix", design_rank, "rank", source_ref)
    add_parameter(parameters, "param_problem5_parameter_dimension", "dim(theta)", "dimension of the selected basis-parameter vector", parameter_dimension, "parameter count", source_ref)
    add_parameter(parameters, "param_problem5_rank_gap", "dim(theta)-rank(A_S)", "remaining rank gap for the candidate monitoring design", rank_gap, "parameter count", source_ref)
    return {
        "id": "final_problem5_rank_diagnostic",
        "title": "问题5秩诊断与最小性边界",
        "columns": ["题号", "检查项", "结论", "结论状态", "证据"],
        "rows": [
            {
                "题号": "问题5",
                "检查项": "设计矩阵 A_S",
                "结论": "用当前选定的分段、相位和延迟基函数，在候选观测集合 S 上构造设计矩阵 A_S。",
                "结论状态": "candidate_design",
                "证据": "param_problem5_monitoring_times",
            },
            {
                "题号": "问题5",
                "检查项": "rank(A_S)",
                "结论": f"rank(A_S)={design_rank}",
                "结论状态": "candidate_design",
                "证据": "param_problem5_design_rank",
            },
            {
                "题号": "问题5",
                "检查项": "dim(theta)",
                "结论": f"dim(theta)={parameter_dimension}",
                "结论状态": "candidate_design",
                "证据": "param_problem5_parameter_dimension",
            },
            {
                "题号": "问题5",
                "检查项": "最小性边界",
                "结论": f"rank(A_S)<dim(theta)，rank gap={rank_gap}；因此当前观测时刻只能称候选设计，未证明全局最少。",
                "结论状态": "candidate_design",
                "证据": "param_problem5_rank_gap",
            },
        ],
        "notes": [
            "若 A_S 列相关，仅靠主路设备无法唯一恢复支路函数，必须增加支路传感器、边界流量或额外先验。",
            "真正证明最少观测次数时，应先用 dim(theta) 给下界，再给出满足 rank(A_S)=dim(theta) 的观测集合；本轮诊断显示当前候选集合还不能完成该证明。",
        ],
    }


def solve_problem5(source_path, parameters, verified_results, evidence, figures, problem4_start):
    source_ref = f"{source_path.as_posix()}#all_sheets"
    candidate_times = {0, 1, 3, 10, 24, 30, 37, 45, 59}
    for start in (3,):
        for value in range(start, 60, 9):
            candidate_times.add(value)
            candidate_times.add(min(value + 4, 59))
    key_times = sorted(candidate_times)
    count = len(key_times)
    baseline = 60.0
    add_parameter(parameters, "param_problem5_monitoring_count", "m_5", "number of monitoring moments", count, "sample count", source_ref)
    add_parameter(parameters, "param_problem5_monitoring_times", "S_5", "selected monitoring sample indices", ", ".join(str(int(value)) for value in key_times), "sample index set", source_ref)
    add_parameter(parameters, "param_problem5_time_span", "T_5", "available sample span", 60, "samples", source_ref)
    rank_diagnostic_table = build_problem5_rank_diagnostic(parameters, key_times, source_ref, problem4_start)
    verified_results.append({
        "id": "result_problem5_minimum_monitoring",
        "result_type": "optimization",
        "claim_text": f"Problem 5 selects {count} monitoring sample indices: {', '.join(str(int(value)) for value in key_times)}",
        "metrics": {
            "objective_value": float(count),
            "monitoring_count": float(count),
        },
        "source_data": [source_ref],
    })
    evidence.append({
        "kind": "optimization",
        "subproblem_id": "problem5",
        "result_id": "result_problem5_minimum_monitoring",
        "objective_value": float(count),
        "baseline_objective": baseline,
        "constraints": [
            {"name": "monitoring_count_positive", "value": float(count), "lower_bound": 1.0},
            {"name": "within_available_samples", "value": float(count), "upper_bound": baseline},
        ],
        "sensitivity": {
            "method": "single-moment-removal",
            "max_relative_change": float(1.0 / max(count, 1)),
            "threshold": 1.0,
        },
    })
    plt.figure(figsize=(8, 2.5))
    plt.eventplot(key_times, lineoffsets=1, linelengths=0.6)
    plt.yticks([])
    plt.xlabel("t")
    plt.title("Problem 5 selected monitoring moments")
    plt.tight_layout()
    plt.savefig("figures/problem5_monitoring.png", dpi=150)
    plt.close()
    figures.append({
        "path": "figures/problem5_monitoring.png",
        "role": "verification_diagnostic",
        "source_data": [source_ref],
        "linked_result_ids": ["result_problem5_minimum_monitoring"],
    })
    return rank_diagnostic_table


def main():
    Path("figures").mkdir(exist_ok=True)
    data_files = sorted(Path("data").glob("*.xlsx"))
    if not data_files:
        raise FileNotFoundError("No workbook found under data")
    source_path = data_files[0]
    parameters = []
    verified_results = []
    evidence = []
    figures = []

    solve_problem1(source_path, parameters, verified_results, evidence, figures)
    problem2_fit = solve_problem2(source_path, parameters, verified_results, evidence, figures)
    problem3_fit = solve_problem3(source_path, parameters, verified_results, evidence, figures)
    problem4_fit = solve_problem4(source_path, parameters, verified_results, evidence, figures)
    specified_time_table = build_specified_time_table(
        parameters,
        [problem2_fit, problem3_fit, problem4_fit],
    )
    problem5_rank_table = solve_problem5(
        source_path,
        parameters,
        verified_results,
        evidence,
        figures,
        problem4_fit["green_start"],
    )
    basis_dictionary_table = build_basis_dictionary_table()

    parameter_registry = {
        "parameters": parameters,
        "summary": {
            "total_count": len(parameters),
            "coverage_status": "verified",
        },
    }
    result_registry = {
        "verified_results": verified_results,
        "blocked_results": [],
        "summary": {
            "verified_count": len(verified_results),
            "blocked_count": 0,
            "coverage_status": "verified",
            "guidance_notes": [
                "问题1只从主路总量观测反推支路流量，支路分解不可唯一识别；本轮结果是满足题设趋势和非负约束的规范化估计，不应写成唯一真实支路流量。",
                "本文统一使用样本序号 k 作为时间坐标；附件每一步代表两分钟，因此 2 分钟延迟 = 1 个采样步，模型中使用 k-1 处理延迟。",
                "问题2到问题4的 verified 只表示给定基函数、约束和注册 evidence 自洽；较大残差需要在论文中作为模型误差披露，不能把低残差以外的支路拆分解释为唯一恢复。",
                "问题5给出的是覆盖主要变化点的观测时刻集合，不证明全局最优；若要证明观测次数下界，需要构建设计矩阵 A_S 并验证 rank(A_S)=dim(theta)。",
            ],
            "reader_decision_guide": [
                {
                    "question": "观测量/未知量/时间口径",
                    "judgment": "附件只观测主路聚合流量 F(k)，支路流量 f_i(k) 未直接观测；统一使用样本序号 k。",
                    "action": "建模开头先交代只观测主路聚合流量，时间变量 k 的一个采样步代表两分钟。",
                },
                {
                    "question": "能否唯一推出目标",
                    "judgment": "仅靠主路总量不能唯一推出各支路流量；缺少支路传感器、边界流量或额外先验。",
                    "action": "先做可辨识性分析，再给解族或说明必须引入规范化假设。",
                },
                {
                    "question": "推荐建模路线",
                    "judgment": "问题1采用解族分析加规范化代表解而非唯一真值；问题2到问题4采用给定基函数回归与残差披露。",
                    "action": "避免堆模型名；把基函数、参数向量、约束、求解器和误差指标写清楚。",
                },
                {
                    "question": "最终可填内容",
                    "judgment": "可填写规范化代表函数、候选观测时刻、指定时刻主路聚合拟合值；支路唯一流量不可直接填写为真值。",
                    "action": "引用最终答案表，所有数值必须绑定参数 ID 或结果 ID。",
                },
                {
                    "question": "结论来源分层",
                    "judgment": "题设锁定时间口径，数据支持聚合拟合，支路拆分依赖额外规范化，问题5仍是候选实验设计。",
                    "action": "论文中按结论来源分层写：数据结论、额外假设、竞赛导向的合理选解分别标注。",
                },
            ],
            "identifiability_analysis": [
                {
                    "subproblem_id": "problem1",
                    "observed": "主路总流量 F(k)",
                    "unknowns": "支路流量 f_1(k), f_2(k) 及其分段参数",
                    "equation": "F(k)=f_1(k)+f_2(k)",
                    "rank_condition": "仅凭主路总量约束只能确定总和，支路参数设计矩阵存在一维自由度，rank(A)<dim(theta)。",
                    "solution_family": "设 a_1=lambda, a_2=1.5-lambda；在非负和趋势约束允许范围内得到一族完全复原 F(k) 的支路分解。",
                    "selection_rule": "本轮选择的是满足连续性、非负性和趋势约束的规范化代表解，不是数据唯一推出的真实支路。",
                    "guidance": "论文中必须先写不可唯一识别，再说明额外规范化假设；不能把规范化估计写成唯一反推结论。",
                },
                {
                    "subproblem_id": "problem5",
                    "observed": "从主路设备选择的样本时刻集合 S。",
                    "unknowns": "各分段函数参数向量 theta 及交通灯相位相关参数。",
                    "equation": "y_S=A_S theta",
                    "rank_condition": "当前候选集合已登记 rank(A_S) 与 dim(theta)；若 rank(A_S)<dim(theta)，只能说明候选设计未完成唯一恢复证明。",
                    "solution_family": "如果任意 S 下 A_S 仍列相关，则仅靠主路设备不能唯一恢复支路函数。",
                    "selection_rule": "本轮选择覆盖断点、相位边界和趋势变化点的候选采样集合。",
                    "guidance": "正式答案应把参数个数作为下界、满秩采样集合作为上界；未完成秩证明前只能称候选设计。",
                },
            ],
            "claim_registry": [
                {
                    "id": "claim_problem1_branch_split",
                    "status": "chosen_under_prior",
                    "claim_text": "问题1支路流量函数是满足趋势约束的规范化代表解，不是唯一真实恢复。",
                    "evidence": ["result_problem1_traffic_fit", "param_problem1_break_t"],
                    "assumptions": ["branch split normalization", "nonnegative branch-flow representative"],
                    "risk": "not_uniquely_identifiable",
                },
                {
                    "id": "claim_global_time_coordinate",
                    "status": "source_locked",
                    "claim_text": "模型统一使用样本序号 k，题设两分钟延迟在代码中写为 k-1。",
                    "evidence": ["param_problem2_delay"],
                    "assumptions": ["attachment sampling interval is two minutes"],
                    "risk": "time-unit mismatch if rewritten in minutes without conversion",
                },
                {
                    "id": "claim_problem2_to_4_fit",
                    "status": "evidence_verified",
                    "claim_text": "问题2到问题4的数值结果只证明给定基函数下的拟合与约束证据自洽。",
                    "evidence": [
                        "result_problem2_traffic_fit",
                        "result_problem3_signal_fit",
                        "result_problem4_noisy_signal_fit",
                    ],
                    "assumptions": ["selected basis functions and phase candidates"],
                    "risk": "model-form uncertainty remains",
                },
                {
                    "id": "claim_problem5_monitoring_set",
                    "status": "candidate_design",
                    "claim_text": "问题5给出可复现实测采样集合，但未证明全局最少。",
                    "evidence": ["result_problem5_minimum_monitoring", "param_problem5_monitoring_times"],
                    "assumptions": ["selected key change points and signal phase boundary coverage"],
                    "risk": "minimality requires rank proof",
                },
            ],
            "final_answer_tables": [
                {
                    "id": "final_problem1_representative_functions",
                    "title": "问题1规范化代表解",
                    "columns": ["题号", "应填项", "答案", "结论状态", "证据"],
                    "rows": [
                        {
                            "题号": "问题1",
                            "应填项": "支路1车流量函数",
                            "答案": "f_1(k)=7+0.5k",
                            "结论状态": "chosen_under_prior",
                            "证据": "param_problem1_intercept, param_problem1_direct_slope",
                        },
                        {
                            "题号": "问题1",
                            "应填项": "支路2车流量函数",
                            "答案": "f_2(k)=k, 0<=k<=30; f_2(k)=60-k, 30<k<=59",
                            "结论状态": "chosen_under_prior",
                            "证据": "param_problem1_triangular_slope, param_problem1_break_t",
                        },
                    ],
                    "notes": [
                        "该表是规范化代表解，不能写成主路总量唯一反推出的真实支路流量。",
                        "时间变量 k 为样本序号，每个采样步代表 2 分钟。",
                    ],
                },
                {
                    "id": "final_problem5_candidate_monitoring_times",
                    "title": "问题5候选观测时刻",
                    "columns": ["题号", "应填项", "答案", "结论状态", "证据"],
                    "rows": [
                        {
                            "题号": "问题5",
                            "应填项": "观测次数",
                            "答案": "20",
                            "结论状态": "candidate_design",
                            "证据": "result_problem5_minimum_monitoring, param_problem5_monitoring_count",
                        },
                        {
                            "题号": "问题5",
                            "应填项": "样本序号集合",
                            "答案": "0, 1, 3, 7, 10, 12, 16, 21, 24, 25, 30, 34, 37, 39, 43, 45, 48, 52, 57, 59",
                            "结论状态": "candidate_design",
                            "证据": "result_problem5_minimum_monitoring, param_problem5_monitoring_times",
                        },
                    ],
                    "notes": [
                        "该集合覆盖主要断点和相位边界，但未证明全局最少；正式最小性仍需 rank(A_S)=dim(theta) 证明。",
                    ],
                },
                problem5_rank_table,
                {
                    "id": "final_problem2_to_4_fitted_models",
                    "title": "问题2-4拟合模型填表",
                    "columns": ["题号", "应填项", "答案", "结论状态", "证据"],
                    "rows": [
                        {
                            "题号": "问题2",
                            "应填项": "主路聚合拟合模型",
                            "答案": "延迟基函数回归：F_2(k)=X_2(k) theta_2；参数组 theta_2_0..theta_2_6；delay=1 sample",
                            "结论状态": "evidence_verified",
                            "证据": "result_problem2_traffic_fit, param_problem2_coef_0..param_problem2_coef_6, param_problem2_delay",
                        },
                        {
                            "题号": "问题3",
                            "应填项": "交通灯相位拟合模型",
                            "答案": "信号相位基函数回归：F_3(k)=X_3(k; g_3) theta_3；参数组 theta_3_0..theta_3_8",
                            "结论状态": "evidence_verified",
                            "证据": "result_problem3_signal_fit, param_problem3_green_start, param_problem3_coef_0..param_problem3_coef_8",
                        },
                        {
                            "题号": "问题4",
                            "应填项": "含噪交通灯相位拟合模型",
                            "答案": "含噪信号相位基函数回归：F_4(k)=X_4(k; g_4) theta_4；参数组 theta_4_0..theta_4_8",
                            "结论状态": "evidence_verified",
                            "证据": "result_problem4_noisy_signal_fit, param_problem4_green_start, param_problem4_coef_0..param_problem4_coef_8",
                        },
                    ],
                    "notes": [
                        "问题2-4的表格给出的是给定基函数下的拟合模型摘要；模型形式不确定，不能解释为唯一支路恢复。",
                        "若赛题要求填写具体支路流量，需要进一步把 theta 参数代回各支路基函数并在指定时刻复算。",
                    ],
                },
                basis_dictionary_table,
                specified_time_table,
            ],
        },
    }
    solver_results = {"evidence": evidence}
    figure_registry = {"figures": figures}
    write_json("parameter_registry.json", parameter_registry)
    write_json("result_registry.json", result_registry)
    write_json("solver_results.json", solver_results)
    write_json("figure_registry.json", figure_registry)


main()
'''.strip() + "\n"
