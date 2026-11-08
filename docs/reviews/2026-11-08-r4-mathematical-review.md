# R4.2 数学对应审核接入与 Flash 实测

日期：2026-11-08。分支：`codex/modeling-core-refactor`。唯一执行计划为 `docs/compute_first_guidance_plan.md`；本文保存 R4.2 的证据与限制。

## 结果与范围

R4.2 完成了逐项数学审核、审核快照绑定和验证层消费。缺失、过期或未接受的审核证据不能得到数学审核通过。后端完整回归 **582 passed，23.51 秒**。

真实实测使用 `https://api.deepseek.com` 的 `deepseek-flash`，共 5 个案例、6 次请求。原题 Q1 的 25 项数学对应检查通过；库存平衡遗漏和零到货延迟变体被准确指出；漏计指导者训练费变体因 JSON 非法及随后截断而未生成有效审核。三个已知错误均未获执行批准，但只有前两个取得有效的错误识别证据。

本次重放 R3 已保存的模型、拆题结果和原件快照，未从 DOCX 重新生成模型，也未修改 API 输出以通过审核。原题整体报告仍为 partial，同族题有假设文字不一致和 Reviewer 误判；R4 的结论边界已验证，自动审核可靠性、整题端到端流程和 D2 尚未验收。

## 合同与运行边界

- 每个可解子题的数学要求由其全部 `required_fact_ids` 加 `$objective`、`$formulation` 组成。审核记录状态、相对本子题的具体模型路径和解释；拒绝重复/未知要求、跨子题路径、不存在路径及 approved 中的 failed 检查。
- Reviewer 同时接收 ProblemSpec、InputCatalog 原始段落/表格、EvidenceProfile 和 ModelIR，逐项核对事实、目标、状态守恒、时序、活动及费用。缺少检查允许保留执行结果，但数学审核只能是 not_assessed。
- 系统对原 ModelIR、可执行投影、ProblemSpec、EvidenceProfile、InputCatalog、审核及可选修订请求计算规范化 JSON SHA-256。摘要由程序生成，LLM 不提供摘要。
- 验证时重新读取这些快照，校验摘要及审核语义。快照缺失/改变时数学审核为 not_assessed；仅 JSON 排版改变不会失效。验证不会重新生成丢失的 EvidenceProfile。
- 继续保留整数、界限、残差和目标重算，以及执行前的假设审核和修订闭环。数学审核通过表示已登记的独立 Reviewer 接受对应关系，不是形式化证明。

修改集中于 `model_review_contracts.py`、`model_review_validation.py`、`reviewer.py`、`stage_contracts.py`、`stages.py` 和 `verification_stage.py`，直接迁移内部调用与测试。公共 API/任务协议和求解器未改；完整回归覆盖 Guidance、Polish、core。当前未提供 CodeGraph 工具，证据来自上述实际阶段调用、合同测试和运行工件。

## RED → GREEN 与离线验证

新增 `backend/tests/test_guidance_mathematical_review.py`，15 项测试覆盖真实审核阶段至结果验证的正向闭环、六类快照变更、证据缺失/格式变化，以及非法或拒绝的数学检查。

- 初次 RED：`backend/.local/r42-review-red-20261108`，旧合同拒绝新增 `mathematical_checks`，尚不能承载数学对应证据。
- 初次 GREEN：`backend/.local/r42-review-green-20261108`，当时 12 项通过；随后补齐快照及合同用例。
- 独立反例 RED：`backend/.local/r42-missing-snapshot-red-20261108`。删除 EvidenceProfile 后，旧验证路径静默重建快照并错误保留 passed。修复为验证阶段严格读取已有快照。
- 合同 GREEN：`backend/.local/r42-contracts-green-20261108`，48 passed。
- 最终完整回归：在 backend 下运行 `..\.venv\Scripts\python.exe -B -m pytest tests -q -x -p no:cacheprovider --tb=short --basetemp=.local/r42-final-offline-20261108`，环境 `PYTHONDONTWRITEBYTECODE=1`，**582 passed，23.51 秒**。完整回归后未再修改生产代码。

六类变更分别是原模型、批准投影、ProblemSpec、审核结论、EvidenceProfile、InputCatalog。可选修订请求也参与摘要绑定，但本轮未单独增加其变更测试。

本轮12个变更文件通过严格UTF-8、无BOM、LF检查，Python AST和`git diff --check`通过；唯一计划只有一个CURRENT，已移至R5.1。未修改前端及公共协议，未重复前端构建。

## 真实 API 回放

脚本：`backend/.local/run_r42_review_flash.py`。独立原文/数值核对：`backend/.local/audit_r42_replays.py`，复用 R3 的原件参考和独立动态规划结果。

源运行：原题 `backend/.local/20261108-204026-r3-q1-flash`；同族题 `backend/.local/20261108-204107-r3-q1-flash`。为审核已有单子题，回放只裁剪 ProblemSpec 的子题及事实作用域，保留事实内容；原始文件不变。错误变体均记录明确 mutation metadata。

第一批：`backend/.local/20261108-221757-r42-review-flash`，包含 original 和 variant。第二批：`backend/.local/20261108-222326-r42-review-flash`，包含三个错误变体。每案保存请求、响应、用量、审核诊断、模型及来源快照、独立求解和数值复核；批准案例继续实际执行 CalculationStage 和 ResultVerificationStage。未批准案例的求解是单独诊断实验，不是绕过产品审核。

| 案例 | 诊断目标值 | Flash 审核 | 独立核对与结论 |
| --- | ---: | --- | --- |
| 原题八周 Q1 | 7625 | approved；25 项 passed | 费用/逐期状态通过；产品数学审核 passed，整体仍 partial |
| 六期同族题 | 1388 | revision_required；32 项 | 数值通过；假设文字与正文/公式不一致；另有一条回流误判，见下文 |
| 删除模块初始及递推平衡 | 495 | revision_required；32 项 | 精确指出只剩非负界、缺少初始 30 及库存递推；独立检查失败 |
| 到货延迟改为零 | 1388 | revision_required；32 项 | 精确指出当期采购当期可用；第 5 期载具可用量 9，原文要求 8；目标值相同仍错误 |
| 漏计指导者训练费 | 1311 | 合同生成失败，未批准 | 独立重算费用 1623，且出现没有受训者的指导活动；无法计为有效审核命中 |

五组模型都通过事实引用覆盖及诊断 MILP 数值检查，再次证明这些检查不足以判断数学对应正确性。

### 原题为什么仍是 partial

`original/verification_report.json` 中执行层 passed、来源覆盖 passed、数学审核 passed，但证据层 failed：四项 final_answer 的 `evidence_ids` 包含 `question.table.4`、`question.table.5`，当前验证器将它们当作未执行节点。需要核对结果证据字段对原始来源与执行证据的语义，不能删除引用以消除失败。

模型还同时声明 `optimization` 和 `mixed_integer_linear_optimization`。后者的目标重算、守恒/可行性、整数、界限/gap、基线五项均通过；前者另要求基线、敏感性、极端输入、多起点、可行性和界限证书，当前未提供对应证据。最终数值层因证据层失败而 blocked。需要先验证分类及校验责任，不能直接跳过这些检查。

### 同族题审核意见的独立裁决

费用假设声称 8 元只对新模块收费，而实际公式对新模块和指导者各收 8 元。合成题正文明确两者都收费，公式与正文相符，假设文字不符；指导者下一期可用的假设说明也未与正文及实际转移统一。保留 revision_required 有依据，但不应按错误假设修改正确费用公式。

Reviewer 还声称模型缺少保养后的模块回流、服务模块永久消失。该判断错误：`/subproblems/0/execution_components/0/model/constraints/9/terms/3` 实际含左侧 `-module_service[t-1]`，等价于：

```text
A[t+1] = A[t] - S[t] + S[t-1] + T[t]
```

独立重算第 2–5 期该恒等式残差均为零。裁决保存在 `variant/review_adjudication.json`，原始审核未改，也未据此误判修改模型。审核能指出错误不代表其每条修订建议都正确。

### 失败、重试与用量

每次实验请求的 `max_tokens=32768`、请求超时 300 秒，均为本地实验覆盖，持久配置哈希保持不变。漏费案例第一次 `finish_reason=stop`，但 JSON 在第 15679 列缺逗号而无法解析；原始文本中虽出现漏费分析，不能当有效合同使用。现有合同请求器自动完整重试一次，第二次 `finish_reason=length`，32768 completion tokens 全用于 reasoning，最终内容为空。程序抛出 ContractGenerationError，未写入批准结果。没有额外手动补发请求或修复输出。

| 请求 | 秒 | prompt tokens | completion tokens | total tokens | finish_reason |
| --- | ---: | ---: | ---: | ---: | --- |
| original 1 | 169.641 | 23206 | 26208 | 49414 | stop |
| variant 1 | 155.156 | 19573 | 21319 | 40892 | stop |
| missing_balance 1 | 158.203 | 19138 | 23945 | 43083 | stop |
| zero_delay 1 | 176.844 | 19573 | 25862 | 45435 | stop |
| missing_activity 1 | 184.844 | 19491 | 24754 | 44245 | stop |
| missing_activity 2 | 236.172 | 19580 | 32768 | 52348 | length |
| 合计 | — | 120561 | 154856 | 275417 | 6 次 |

其中 reasoning 共 131144 tokens，已包含在 completion 中，不重复累计。用户已明确授权本次及后续同范围 R4 复测；实际请求仅使用 deepseek-flash。原始实验工件留在忽略目录，不提交密钥、配置、请求响应全集或临时脚本。

## 阶段出口与剩余限制

R4.1–R4.2 的阶段出口成立：来源覆盖、数学审核、数值检查独立报告，已知错误不能被整体标成数学已验证；两个有效审核准确指出注入错误，第三个合同失败并保持未批准。该结果不代表稳定识别所有数学错误，尤其不能掩盖本次 Reviewer 误判及预算耗尽。

R5.1 接续处理模型修改/失败重试时的完整依赖工件失效与独立结果保留。本轮只保证审核快照改变后审核失效，尚未验收新模型与旧执行工件的全面失效管理。R5.2 使用本轮 JSON 失败、空输出和 length 的真实证据区分恢复原因；不全局增大预算。

D2 前必须核对并验证原题结果证据引用和验证类别问题，复测同族题假设/原文一致性及 Reviewer 回流误判，完成重新从原件建模到审核执行的闭环，再进入 Q1–Q5 数学验收。原题仍为已有费用/时点解释下的条件解；完整写作/导出和整题依赖流程尚未验收。

现状摘要：原件→事实/参数→联合数学声明→ModelIR→逐项审核及快照绑定→执行→来源/数学/数值分层报告。最危险的边界是将 Reviewer 解释当数学证明，以及新模型复用旧执行证据；本次接入并验证数学审核，完成 R4.2；下一未完成项为 R5.1。
