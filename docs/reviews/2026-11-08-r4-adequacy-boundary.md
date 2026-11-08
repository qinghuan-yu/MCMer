# R4.1 数学充分性边界验收

日期：2026-11-08。分支：`codex/modeling-core-refactor`。唯一执行计划为 `docs/compute_first_guidance_plan.md`；本文记录R4.1的证据与限制。

## 结果与范围

R4.1完成：来源覆盖、数学对应审核、数值复核分别记录，整体充分性由三者确定性派生。缺少数学对应证据时，数值通过不会把模型充分性提升为passed，也不会据此将备选方案标记为excluded。指导文档分别显示三项状态。

当前独立Reviewer仍执行原有的逐子题批准、假设审核和修订闭环；其批准用于允许执行，尚未携带可供充分性报告消费的逐项数学对应证据。因此执行后的数学审核保持not_assessed，正常数值结果仍可作为partial结果保留。接入并核验这些证据属于R4.2，本轮没有完成R4整体或D2。

## 实证与RED → GREEN

新增测试 `backend/tests/test_guidance_mathematical_adequacy.py` 使用现有的有限库存夹具，经真实ModelIR编译、算子执行、MILP求解及ResultVerificationStage；没有模拟求解器结果。测试中注册的Reviewer记录只有approved决定，不包含数学对应证据。来源覆盖使用真实 `validate_model_fact_coverage`：每项事实均引用一个存在的ModelIR节点，但该引用不能证明关系已完整表达。

| 模型 | 目标值 | 独立检查 | 最终来源覆盖 / 数值复核 / 数学审核 |
| --- | ---: | --- | --- |
| 原模型 | 26 | 与既有有限库存基准相符 | passed / passed / not_assessed |
| 删除初始及逐期库存平衡 | 0 | 初期库存违反5−2−1的守恒关系 | passed / passed / not_assessed |
| 到货延迟由一期改为零 | 26 | 到货量不等于上一期采购量；目标值相同仍有时序错误 | passed / passed / not_assessed |
| 删除采购活动容量关系 | 24 | 正采购量对应零setup活动，漏计活动费用 | passed / passed / not_assessed |

四个模型均通过原有全部MILP数值校验，最终报告均为partial，model_adequate均为not_assessed。原模型也未取得独立数学对应证据，不能因测试已知其参考值就改变产品结论。

- 首轮 `.local/r4-adequacy-red-20261108` 因夹具原有final_answer引用数据源而非执行节点导致证据层失败，不作为目标行为RED证据；仅在新测试构造中将引用改为实际solve节点。
- 有效RED：`.local/r4-adequacy-red2-20261108`，3 failed。原模型、库存平衡遗漏和零延迟三个场景均在 `model_adequate.status` 断言失败：实际passed、预期not_assessed；执行和证据层已通过。原始验证报告及数值结果均保留。
- GREEN：`.local/r4-adequacy-green-20261108`，相同3项测试通过。随后增加采购活动遗漏、九项状态覆盖合同与三项来源篡改测试。
- 呈现RED：`.local/r4-rendering-red-20261108`，46 passed、1 failed，失败原因是指导文档没有独立显示题面事实覆盖、数学对应审核和数值复核；补齐共用呈现函数后通过。
- 最终回归：backend目录下运行 `..\.venv\Scripts\python.exe -B -m pytest tests -q -x -p no:cacheprovider --tb=short --basetemp=.local/r4-full-20261108`，环境变量 `PYTHONDONTWRITEBYTECODE=1`，**567 passed，16.19秒**。覆盖Guidance、Polish、core、部分结果隔离及公共任务结果合同。

以上临时目录均位于 `backend/.local/`，保存本轮回放证据。新增17项测试；既有直接构造内部报告的测试已迁移到新结构，未放宽原数值检查。

10个变更文件通过严格UTF-8、无BOM、LF检查，Python AST及 `git diff --check` 通过。未修改前端及公共协议，未重复前端构建。

## 替换边界与保留行为

运行反例证实原 `_assess_model_adequacy` 将数值校验器全部通过直接解释为充分性通过。按已批准的R4.1，将这一汇总职责替换为三个必填子层：

- `source_coverage`：重新核对ProblemSpec与已批准模型的子题集合、事实ID和绑定路径。缺少事实证据时未评估，引用不全或路径错误时失败；不推断数学等价。
- `mathematical_review`：明确记录尚未取得独立数学对应证据，保持未评估。执行前Reviewer阻断则记录blocked。
- `numerical_validation`：保留原注册校验器及基线、整数、界限、残差等结果，检查对象仍是模型声明及编译矩阵。

`ModelAdequacyLayer`从子层状态重新计算自身状态、原因和证据引用；外部传入passed不能覆盖子层failed、blocked或not_assessed。`VerificationReport`继续根据执行、证据、充分性及子题覆盖派生整体状态。执行前诊断和指导文档已直接迁移，没有旧报告结构的兼容分支。

改动限于Guidance内部报告合同、验证汇总与呈现，内部ModelIR、求解器、独立Reviewer和公共API/任务结果协议保持原有行为。已核对 `core.workflow.run_workflow` 的Guidance/Polish分流、`guidance.workflow` 的阶段组装，以及Polish对core共享写作/导出的调用；本次不替换这些边界。

## 限制与下一步

本轮只验证离线合同和结论边界，没有调用DeepSeek、重新生成R3真实题模型或宣称自动识别所有数学遗漏。已知错误在没有独立数学证据时保持未评估，不能因此声称已完成数学审核。R4.2须让Reviewer对题面要求与模型关系提供可核对、绑定当前模型的逐项证据，并用R3真实模型及已知错误变体验证；缺失或失效的证据不得提升审核状态。

现状摘要：核心流为原件→事实/覆盖→参数物化→联合数学声明→ModelIR→执行→来源/数学/数值分层报告；最危险的剩余边界是独立数学对应证据尚未接入；本次消除了数值通过直接升级充分性的错误汇总；完成R4.1，下一未完成项R4.2。
