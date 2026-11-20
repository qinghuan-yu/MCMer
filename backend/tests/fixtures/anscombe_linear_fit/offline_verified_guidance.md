# 建模指导方案

## 可信度摘要

| 验证层 | 状态 | 说明 |
| --- | --- | --- |
| 执行正确性 | `passed` | execution plan hash and node coverage verified |
| 证据可追溯性 | `passed` | registered outputs trace to executed nodes and any cited sources are verified |
| 题面事实覆盖 | `passed` | required fact IDs and model paths are covered for executable subproblems; this checks references, not mathematical equivalence |
| 数学对应审核 | `passed` | sp1: independent reviewer accepted 12 mathematical requirements under the bound source snapshots |
| 数值复核 | `passed` | all declared numerical validators passed for the supplied model |
| 模型充分性 | `passed` | source_coverage: required fact IDs and model paths are covered for executable subproblems; this checks references, not mathematical equivalence, mathematical_review: sp1: independent reviewer accepted 12 mathematical requirements under the bound source snapshots, numerical_validation: all declared numerical validators passed for the supplied model |

流程聚合状态为 `verified`；该值只用于流程路由，不代表整题数学结论已经完成。

## 问题理解

依据题目拆解契约完成当前子问题的建模与计算。
- 观测数据/inputs：以数据与来源章节、参数注册表和 result_registry.source_data 登记为准。
- 未知目标/outputs：以已审核模型的目标、参数、状态变量和最终答案表为准。
- 时间/数据口径：以题设、附件字段、样本/period/time 单位和参数表登记为准。

## 数据与来源

- 来源文件/数据表：参数表和结果表登记的 source_ref/source_data。
- 观测数据/输入字段：只把题设或附件中直接观测到的量写成 data evidence；未观测的目标、状态变量或支路量必须在可辨识性分析中标为 unknown。
- 时间/样本口径：按题面、附件字段和参数表登记的 time/period/sample/unit 统一；若题面给的是采样序号，就禁止在正文混用分钟制延迟。
- 预处理与可信状态：清洗、单位换算、缺失值处理和代表性假设必须追踪到参数与来源表或 result_registry，不得只写“系统已登记”。

## 参数与来源表

| parameter_id | 符号 | 含义 | 数值 | 单位 | 来源类型 | 来源 | 可信状态 |
|---|---|---|---|---|---|---|---|

## 模型选择理由

- `sp1` 的模型拟议方案（baseline）`cm_ols_baseline`，模型所述适用范围为“Full-rank constant+identity OLS with all 11 complete observations; coefficients are descriptive for this dataset.”。
- `sp1` 的模型拟议方案（recommended）`cm_ols_loo_recommended`，模型所述适用范围为“Same OLS specification; leave-one-out is a diagnostic only and does not alter the reported full-sample coefficients.”。

## 结论状态登记

| claim_id | 状态 | 结论 | 证据 | 依赖假设 | 风险 |
| --- | --- | --- | --- | --- | --- |
| final_intercept | candidate_design | 结果 final_intercept 取自节点 fit_ols 的登记输出。 | final_intercept | The observations worksheet has x1 and y1 as numeric complete columns for all 11 data rows after the header row., Ordinary least squares minimizes the sum of squared residuals over the full 11-observation sample with a constant and identity basis, without regularization or robust loss., The fitted line is descriptive for this observation set and is not intended as a causal or out-of-sample predictive model. | Conditional result: valid only under the registered modeling assumptions and their alternative-scenario checks. |
| final_slope | candidate_design | 结果 final_slope 取自节点 fit_ols 的登记输出。 | final_slope | The observations worksheet has x1 and y1 as numeric complete columns for all 11 data rows after the header row., Ordinary least squares minimizes the sum of squared residuals over the full 11-observation sample with a constant and identity basis, without regularization or robust loss., The fitted line is descriptive for this observation set and is not intended as a causal or out-of-sample predictive model. | Conditional result: valid only under the registered modeling assumptions and their alternative-scenario checks. |
| final_combined_ols | candidate_design | 结果 final_combined_ols 取自节点 fit_ols 的登记输出。 | final_combined_ols | The observations worksheet has x1 and y1 as numeric complete columns for all 11 data rows after the header row., Ordinary least squares minimizes the sum of squared residuals over the full 11-observation sample with a constant and identity basis, without regularization or robust loss., The fitted line is descriptive for this observation set and is not intended as a causal or out-of-sample predictive model. | Conditional result: valid only under the registered modeling assumptions and their alternative-scenario checks. |

## 最终答案与应填表格

| 子问题 | 输出项 | 数值 | 状态 | 证据 |
| --- | --- | --- | --- | --- |
| sp1 | intercept | 3.000090909090909 | candidate_design | `result_registry.json#final_intercept` |
| sp1 | slope | 0.5000909090909094 | candidate_design | `result_registry.json#final_slope` |
| sp1 | intercept | 3.000090909090909 | candidate_design | `result_registry.json#final_combined_ols` |
| sp1 | slope | 0.5000909090909094 | candidate_design | `result_registry.json#final_combined_ols` |

## 分问题建模步骤


**sp1**

- 建模目标：Estimate intercept and slope in y1 = intercept + slope*x1 by ordinary least squares using all 11 observations, with a descriptive fit to this dataset.
- 已执行算子 `data.inspect_file`，节点 `inspect_workbook`，输出 inspection。
  - 模型登记的算子参数 `{"preview_rows": 12, "preview_columns": 2, "preview_sheets": 1}`。
- 已执行算子 `data.read_excel_table`，节点 `read_observations`，输出 observations_table。
  - 模型登记的算子参数 `{"sheet": "observations", "header_row": 0, "cell_range": "A1:B12", "columns": ["x1", "y1"], "trim_trailing_empty_rows": true}`。
- 已执行算子 `artifact.extract_vector`，节点 `extract_x1`，输出 x1。
  - 模型登记的算子参数 `{"path": "x1"}`。
- 已执行算子 `artifact.extract_vector`，节点 `extract_y1`，输出 y1。
  - 模型登记的算子参数 `{"path": "y1"}`。
- 已执行算子 `regression.basis_least_squares`，节点 `fit_ols`，输出 fit。
  - 模型登记的算子参数 `{"basis": [{"kind": "constant"}, {"kind": "identity"}]}`。
- 已执行算子 `diagnostics.leave_one_out`，节点 `loo_diag`，输出 loo_diagnostic。
  - 模型登记的算子参数 `{"zero_intercept": false}`。
- 模型拟定的适用边界：Same OLS specification; leave-one-out is a diagnostic only and does not alter the reported full-sample coefficients.
- 模型拟定的输出项 `final_intercept`，说明原文为“intercept (at least four decimal places)”。
- 模型拟定的输出项 `final_slope`，说明原文为“slope (at least four decimal places)”。
- 模型拟定的输出项 `final_combined_ols`，说明原文为“Combined OLS result: intercept and slope”。

## 必要计算结果

参数与约束见审核后的 ModelIR。各项输出按执行清单中的求解器及节点登记。
- `final_intercept`：结果 final_intercept 取自节点 fit_ols 的登记输出。
- `final_slope`：结果 final_slope 取自节点 fit_ols 的登记输出。
- `final_combined_ols`：结果 final_combined_ols 取自节点 fit_ols 的登记输出。

## 图片解读


## 复核与稳健性

- 执行正确性：`passed`。
- 证据可追溯性：`passed`。
- 题面事实覆盖：`passed`。
- 数学对应审核：`passed`。
- 数值复核：`passed`。
- 模型充分性：`passed`。
- 可辨识性与计算可行性：以模型审核结论和本轮数值复核状态为准。
- 误差阈值/基线解释：RMSE、残差、目标值和约束裕度只说明结果在 `verification_report.json` 登记的复算准则、baseline/threshold 或容许条件下自洽；若没有明确阈值或基线，不能把 `verified` 写成真实机理充分证明。
- 输入覆盖复核：1/1 项通过。
- 本次验证记录未登记单独的敏感性检验。
- 假设与局限 `sp1/asm_numeric_complete`：The observations worksheet has x1 and y1 as numeric complete columns for all 11 data rows after the header row.；依据：data/observations.xlsx#observations, inputs/question/question.txt#paragraph-1；替代情景：If non-numeric or missing entries are found, perform documented data-quality review before fitting.；拟议敏感性检查：Verify row count equals 11 and profile missing counts are zero before fitting.。
- 假设与局限 `sp1/asm_ols_rss`：Ordinary least squares minimizes the sum of squared residuals over the full 11-observation sample with a constant and identity basis, without regularization or robust loss.；依据：inputs/question/question.txt#paragraph-2, lf_estimation_ols；替代情景：A robust or regularized regression would be an alternative if influential outliers or shrinkage were desired.；拟议敏感性检查：Compute leave-one-out intercept and slope estimates and compare their stability.。
- 假设与局限 `sp1/asm_descriptive_scope`：The fitted line is descriptive for this observation set and is not intended as a causal or out-of-sample predictive model.；依据：lf_result_usage, inputs/question/question.txt#paragraph-2；替代情景：For prediction, use a separate train/test or rolling validation design.；拟议敏感性检查：Report the observed x1 range together with the descriptive interpretation.。

## 阻断项与待确认项

- 当前复核未记录阻断项；仍需人工确认题设假设、参数来源和约束口径。

## 论文转化建议

将模型选择、核心假设、参数来源、约束条件和数值复核分别写入论文，禁止把待确认前提写成既定事实。

## 可复现附件说明

- `approved_model`：`approved_model_ir.json`
- `model_review`：`model_review.json`
- `execution_manifest`：`execution_manifest.json`
- `execution_plan`：`execution_plan.json`
- `parameter_registry`：`parameter_registry.json`
- `result_registry`：`result_registry.json`
- `figure_registry`：`figure_registry.json`

## 题面事实追踪

| fact_id | 题面事实 | 值/单位 | 适用子问题 | InputCatalog 来源 | ModelIR 路径 |
| --- | --- | --- | --- | --- | --- |
| lf_dataset_observations_schema | 数据集级事实：附件 observations.xlsx 的 observations 工作表包含列 x1 与 y1，首行为列名，共 11 对观测；原始单元格完整保存在 EvidenceProfile/InputCatalog 登记的该 source_ref 中。 | x1,y1; n=11 dataset | sp1 | data/observations.xlsx, inputs/question/question.txt#paragraph-1 | sp1:/execution_components/0/operators/0, sp1:/execution_components/0/operators/1 |
| lf_observations_count_11 | observations 工作表共有 11 对参与建模的观测。 | 11 pairs | sp1 | data/observations.xlsx, inputs/question/question.txt#paragraph-1 | sp1:/execution_components/0/operators/0, sp1:/execution_components/0/operators/1 |
| lf_all_observations_used | 拟合必须使用 observations 工作表中的全部观测，不得剔除或抽样。 | all observations | sp1 | inputs/question/question.txt#paragraph-2, inputs/question/question.txt#paragraph-1 | sp1:/execution_components/0/operators/1, sp1:/execution_components/0/operators/4, sp1:/execution_components/0/operators/5 |
| lf_model_equation | 拟合模型为带截距的一元线性直线 y1 = intercept + slope × x1。 | y1 = intercept + slope * x1 | sp1 | inputs/question/question.txt#paragraph-2 | sp1:/execution_components/0/operators/4 |
| lf_variable_roles | x1 是自变量，y1 是响应变量。 | x1=independent; y1=response | sp1 | inputs/question/question.txt#paragraph-1 | sp1:/execution_components/0/operators/1, sp1:/execution_components/0/operators/2, sp1:/execution_components/0/operators/3 |
| lf_model_has_intercept | 模型必须带截距项，截距参数名为 intercept。 | True boolean | sp1 | inputs/question/question.txt#paragraph-2 | sp1:/execution_components/0/operators/4 |
| lf_estimation_ols | 参数估计方法为普通最小二乘（OLS）。 | ordinary least squares | sp1 | inputs/question/question.txt#paragraph-2 | sp1:/execution_components/0/operators/4, sp1:/execution_components/0/operators/5 |
| lf_output_combined | 最终须以一份结果同时给出截距 intercept 和斜率 slope 的数值。 | True boolean | sp1 | inputs/question/question.txt#paragraph-2 | sp1:/execution_components/0/operators/4 |
| lf_min_decimal_places | intercept 和 slope 的数值均保留至少四位小数。 | 4 decimal places | sp1 | inputs/question/question.txt#paragraph-2 | sp1:/execution_components/0/operators/4 |
| lf_result_usage | 拟合结果用于描述这组观测。 | describe observations | sp1 | inputs/question/question.txt#paragraph-2 | sp1:/execution_components/0/operators/4 |

### 题意歧义

- ProblemSpec 未登记题意歧义。
