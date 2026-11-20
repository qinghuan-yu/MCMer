# 建模指导方案

## 可信度摘要

| 验证层 | 状态 | 说明 |
| --- | --- | --- |
| 执行正确性 | `passed` | execution plan hash and node coverage verified |
| 证据可追溯性 | `passed` | registered outputs trace to executed nodes and any cited sources are verified |
| 题面事实覆盖 | `passed` | required fact IDs and model paths are covered for executable subproblems; this checks references, not mathematical equivalence |
| 数学对应审核 | `passed` | problem1: independent reviewer accepted 15 mathematical requirements under the bound source snapshots |
| 数值复核 | `passed` | all declared numerical validators passed for the supplied model |
| 模型充分性 | `passed` | source_coverage: required fact IDs and model paths are covered for executable subproblems; this checks references, not mathematical equivalence, mathematical_review: problem1: independent reviewer accepted 15 mathematical requirements under the bound source snapshots, numerical_validation: all declared numerical validators passed for the supplied model |

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
| obj_coef_x | — | problem1 | 1.0 | — | ModelIR | problem_spec.json#locked_facts/fact-objective-coefficient-x | 来源绑定 |
| obj_coef_y | — | problem1 | 10.0 | — | ModelIR | problem_spec.json#locked_facts/fact-objective-coefficient-y | 来源绑定 |
| con1_coef_x | — | problem1 | 1.0 | — | ModelIR | problem_spec.json#locked_facts/fact-constraint-1-coefficient-x | 来源绑定 |
| con1_coef_y | — | problem1 | 7.0 | — | ModelIR | problem_spec.json#locked_facts/fact-constraint-1-coefficient-y | 来源绑定 |
| con1_rhs | — | problem1 | 17.5 | — | ModelIR | problem_spec.json#locked_facts/fact-constraint-1-rhs | 来源绑定 |
| con2_coef_x | — | problem1 | 1.0 | — | ModelIR | problem_spec.json#locked_facts/fact-constraint-2-coefficient-x | 来源绑定 |
| con2_rhs | — | problem1 | 3.5 | — | ModelIR | problem_spec.json#locked_facts/fact-constraint-2-rhs | 来源绑定 |

## 模型选择理由

- `problem1` · baseline `continuous_lp_relaxation_bound`，适用范围为仅用于给出上界和检验最优性；其解可能不是整数，不能直接作为题目要求的可行解。。
- `problem1` · recommended `indexed_integer_linear_program`，适用范围为适用于本题这类变量少、系数和右端常数明确的线性整数规划；由系统编译器生成 MILP 并调用优化器求解。。
- `problem1` · alternative `finite_integer_enumeration_check`，适用范围为仅用于小规模复核；候选范围由本题两条不等式约束推出，不适用于更大规模或更一般的整数规划。。

## 结论状态登记

| claim_id | 状态 | 结论 | 证据 | 依赖假设 | 风险 |
| --- | --- | --- | --- | --- | --- |
| answer_x | evidence_verified | 一个最优解中的 x 值 | answer_x | 待确认 | No additional modeling assumptions are registered. |
| answer_y | evidence_verified | 一个最优解中的 y 值 | answer_y | 待确认 | No additional modeling assumptions are registered. |
| answer_objective | evidence_verified | 对应的最大目标值 x+10y | answer_objective | 待确认 | No additional modeling assumptions are registered. |
| answer_feasibility | evidence_verified | 最优解对非负整数域和两条不等式约束的满足情况 | answer_feasibility | 待确认 | No additional modeling assumptions are registered. |
| answer_optimality | evidence_verified | 最优性说明：整数候选枚举与连续松弛上界共同支持的求解结论 | answer_optimality | 待确认 | No additional modeling assumptions are registered. |

## 最终答案与应填表格

| 子问题 | 输出项 | 数值 | 状态 | 证据 |
| --- | --- | --- | --- | --- |
| problem1 | objective_value | 23.0 | evidence_verified | `result_registry.json#answer_x` |
| problem1 | x | 3.0 | evidence_verified | `result_registry.json#answer_x` |
| problem1 | y | 2.0 | evidence_verified | `result_registry.json#answer_x` |
| problem1 | objective_value | 23.0 | evidence_verified | `result_registry.json#answer_y` |
| problem1 | x | 3.0 | evidence_verified | `result_registry.json#answer_y` |
| problem1 | y | 2.0 | evidence_verified | `result_registry.json#answer_y` |
| problem1 | objective_value | 23.0 | evidence_verified | `result_registry.json#answer_objective` |
| problem1 | x | 3.0 | evidence_verified | `result_registry.json#answer_objective` |
| problem1 | y | 2.0 | evidence_verified | `result_registry.json#answer_objective` |
| problem1 | objective_value | 23.0 | evidence_verified | `result_registry.json#answer_feasibility` |
| problem1 | x | 3.0 | evidence_verified | `result_registry.json#answer_feasibility` |
| problem1 | y | 2.0 | evidence_verified | `result_registry.json#answer_feasibility` |
| problem1 | objective_value | 23.0 | evidence_verified | `result_registry.json#answer_optimality` |
| problem1 | x | 3.0 | evidence_verified | `result_registry.json#answer_optimality` |
| problem1 | y | 2.0 | evidence_verified | `result_registry.json#answer_optimality` |

## 分问题建模步骤


**problem1**

- 建模目标：在 x、y 为非负整数且满足 x+7y<=17.5、x<=3.5 的条件下，最大化 x+10y，并报告一个最优解的 x、y 与目标值。
- 索引模型参数：obj_coef_x, obj_coef_y, con1_coef_x, con1_coef_y, con1_rhs, con2_coef_x, con2_rhs。
- 约束：constraint_1, constraint_2。
- 求解节点：`solve_indexed_model`。
- 输出 `answer_x`：一个最优解中的 x 值。
- 输出 `answer_y`：一个最优解中的 y 值。
- 输出 `answer_objective`：对应的最大目标值 x+10y。
- 输出 `answer_feasibility`：最优解对非负整数域和两条不等式约束的满足情况。
- 输出 `answer_optimality`：最优性说明：整数候选枚举与连续松弛上界共同支持的求解结论。

## 必要计算结果

参数与约束见审核后的 ModelIR。各项输出按执行清单中的求解器及节点登记。
- `answer_x`：已登记结果
- `answer_y`：已登记结果
- `answer_objective`：已登记结果
- `answer_feasibility`：已登记结果
- `answer_optimality`：已登记结果

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
| fact-objective-sense | 题目目标为最大化目标函数。 | maximize | problem1 | inputs/question/question.txt#paragraph-2 | problem1:/execution_components/0/model/objective |
| fact-objective-coefficient-x | 目标函数 x + 10y 中变量 x 的系数为 1。 | 1 无量纲 | problem1 | inputs/question/question.txt#paragraph-2 | problem1:/execution_components/0/model/parameters/0, problem1:/execution_components/0/model/objective |
| fact-objective-coefficient-y | 目标函数 x + 10y 中变量 y 的系数为 10。 | 10 无量纲 | problem1 | inputs/question/question.txt#paragraph-2 | problem1:/execution_components/0/model/parameters/1, problem1:/execution_components/0/model/objective |
| fact-variable-domain | 变量 x 和 y 均为非负整数。 | nonnegative_integer | problem1 | inputs/question/question.txt#paragraph-2 | problem1:/execution_components/0/model/variables/0, problem1:/execution_components/0/model/variables/1 |
| fact-constraint-1-relation | 第一条约束为小于等于关系：x + 7y ≤ 17.5。 | less_than_or_equal | problem1 | inputs/question/question.txt#paragraph-3 | problem1:/execution_components/0/model/constraints/0 |
| fact-constraint-1-coefficient-x | 约束 x + 7y ≤ 17.5 中 x 的系数为 1。 | 1 无量纲 | problem1 | inputs/question/question.txt#paragraph-3 | problem1:/execution_components/0/model/parameters/2, problem1:/execution_components/0/model/constraints/0 |
| fact-constraint-1-coefficient-y | 约束 x + 7y ≤ 17.5 中 y 的系数为 7。 | 7 无量纲 | problem1 | inputs/question/question.txt#paragraph-3 | problem1:/execution_components/0/model/parameters/3, problem1:/execution_components/0/model/constraints/0 |
| fact-constraint-1-rhs | 约束 x + 7y ≤ 17.5 的右端常数为 17.5。 | 17.5 | problem1 | inputs/question/question.txt#paragraph-3 | problem1:/execution_components/0/model/parameters/4, problem1:/execution_components/0/model/constraints/0 |
| fact-constraint-2-relation | 第二条约束为小于等于关系：x ≤ 3.5。 | less_than_or_equal | problem1 | inputs/question/question.txt#paragraph-3 | problem1:/execution_components/0/model/constraints/1 |
| fact-constraint-2-coefficient-x | 约束 x ≤ 3.5 中 x 的系数为 1。 | 1 无量纲 | problem1 | inputs/question/question.txt#paragraph-3 | problem1:/execution_components/0/model/parameters/5, problem1:/execution_components/0/model/constraints/1 |
| fact-constraint-2-rhs | 约束 x ≤ 3.5 的右端常数为 3.5。 | 3.5 | problem1 | inputs/question/question.txt#paragraph-3 | problem1:/execution_components/0/model/parameters/6, problem1:/execution_components/0/model/constraints/1 |
| fact-output-report-one-optimal | 题目要求求一个最优解，并报告 x、y 及对应的目标值。 | report x, y and objective value of one optimal solution | problem1 | inputs/question/question.txt#paragraph-4 | problem1:/execution_components/0/model/objective |
| fact-output-one-optimal-solution-selection | 题目要求求一个最优解，并报告该解的 x、y 及对应目标值；输出要求是报告一个最优解，不要求报告全部最优解或规定多个最优解之间的选择次序。 | 报告一个最优解 | problem1 | inputs/question/question.txt#paragraph-4 | problem1:/execution_components/0/model/objective |

### 题意歧义

- ProblemSpec 未登记题意歧义。
