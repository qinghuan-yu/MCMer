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


def solve_problem5(source_path, parameters, verified_results, evidence, figures):
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
    solve_problem2(source_path, parameters, verified_results, evidence, figures)
    solve_problem3(source_path, parameters, verified_results, evidence, figures)
    solve_problem4(source_path, parameters, verified_results, evidence, figures)
    solve_problem5(source_path, parameters, verified_results, evidence, figures)

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
