# 2026-11-08 DeepSeek Flash 真实题复测

结论：D1 的复跑与产物审查完成，真实题求解未通过。默认标准配置在拆题时截断；预算对照运行完成五题生成尝试、独立审核和阻断 Guidance，但五题均未形成可执行模型，未生成最优采购量或成本。D2 完整数学审核尚无所需模型和数值证据。

## 范围与运行条件

- 用户明确授权 DeepSeek API，随后指定 `deepseek-flash`；20 次生成响应的 model 均为 `deepseek-flash`，没有使用 Pro 生成。
- 官方端点 `https://api.deepseek.com`；凭据从已有本地配置读取，未写入复测脚本或请求记录。两次运行前后持久化配置哈希一致。
- 使用当前 `build_default_guidance_pipeline`，从原始 DOCX 重新登记输入。未更换提示词、模型校验、算子或业务代码；本地脚本仅记录调用及显式试验预算。
- 基线任务：`20260715-135019-066fc4b9`。输入文件：`inputs/question/2022-51MCM-Problem A.docx`。
- 原件 SHA-256：`ab89becccda1027280bbe5de69905708a306b63b5e5e215aa99ec43d616d9700`。复制件哈希一致。
- 24 个输入块、5 张表、104 个周需求；需求合计 4065，前八周为 `11, 5, 4, 7, 16, 6, 5, 7`。成本表和需求表均被登记为可用来源。

| 运行 | 配置 | 生成调用 | 耗时 | 结果 |
| --- | --- | ---: | ---: | --- |
| `20261108-134516-d1-deepseek-flash` | standard，原有输出上限 | 2 | 95.734 秒 | 拆题失败，两个响应均 `finish_reason=length` |
| `20261108-134743-d1-deepseek-flash` | 项目现有 strict 超时预算，进程内输出上限 32768 | 18 | 1049.531 秒 | 无截断，最终 `blocked` |

首轮拆题输出上限为 8192。两次响应的推理 token 分别为 5024、7979，最终 JSON 均不完整。第二轮抽取事实的一次响应达到 24816 completion tokens，其中 reasoning tokens 为 18000。预算不足有直接截断证据；两轮同时存在随机生成差异及超时预算变化，不能据此统计成功率或声称仅调大上限即可修复项目。

API 返回的用量合计：262498 prompt tokens、214932 completion tokens，总计 477430；其中 reasoning tokens 为 163393，已包含在 completion tokens 内。此处是接口用量统计，不是账单金额。

## 逐题与产物审查

第二轮 ProblemSpec 含 5 个子问题、56 条事实、9 项歧义。每题的架构草稿均生成，索引核心均在两次生成后失败。没有进入约束生成阶段；最终 canonical ModelIR 的五题全部为 blocked。

| 子题 | 最终生成阻断 | 参数阶段事实可见性 |
| --- | --- | --- |
| Q1 | 五项成本来源不能精确解析；重试还将 `for_each` 的绑定方向写反 | 0 条 |
| Q2 | 需求与购买、保养、训练成本来源不能精确解析 | 0 条 |
| Q3 | 需求与成本向量来源不能精确解析 | 0 条 |
| Q4 | 重试剩余需求向量来源失败：`#table-5:weekly_demand` 不是登记的切片引用 | 本题事实仍可见 |
| Q5 | 阶梯成本、保养训练成本、第105周直接购买价格等来源失败 | 详见请求及逐题诊断，未将其归因于 Q1–Q3 的空事实列表 |

- `model_review.json`：五题均 blocked；`approved_model_ir.json` 无可执行子题。
- `verification_report.json`：`execution_verified=not_assessed`、`evidence_supported=blocked`、`model_adequate=blocked`。
- `result_registry.json`：0 个 verified 结果，5 个 blocked 结果；`parameter_registry.json` 为空。
- 未生成 `execution_plan.json`、`execution_manifest.json`、`compilation_result.json`、Notebook。求解器未运行，不能审核最优性、状态转移数值或 Q5 成本差额。
- `guidance.md` 明确展示逐题阻断。`guidance_audit_report.json` 的 PASS 表示阻断文档通过呈现审核，不能解释为数学通过；最终任务状态仍为 blocked。
- 使用项目现有工作区导出函数生成 ZIP，清单标记 `partial`，执行状态为 `unknown`。缺少执行计划、算子版本、代码导出、完整注册表组和图表，与实际未执行状态一致。验证了 ZIP CRC 及清单中所有文件的 SHA-256。

## 离线定位：事实覆盖与后续事实可见性耦合

真实调用记录显示 Q1–Q3 的架构算子登记了各题事实，随后 `model_generation.py` 经 `_remaining_locked_facts` 将已登记事实从核心阶段上下文移除。Q1 的 `004_request.json` 中 `locked_facts=[]`，核心阶段仍需构建引用这些成本事实的参数。

使用 Q1 第二次核心响应作离线对照，先将五项成本绑定到既有 LF014–LF018，并修正 `for_each` 为 `{"w":"week"}`，随后保持该参数合同不变，仅改变校验器可见的事实：

| 对照 | 可见事实 | `_validate_indexed_core_stage` 结果 |
| --- | ---: | --- |
| 原生成结果 | 0 | 拒绝：来源与索引错误 |
| 正确来源引用、正确索引映射 | 0 | 拒绝：成本事实不可解析 |
| 同一份修正合同，恢复 Q1 本题事实 | 22 | 通过 |

这证明本次运行中事实覆盖记录会限制后续事实引用。它不是完整模型修复：实验未生成约束、未执行算子图，也未解决 Q4/Q5 的全部来源问题。在线运行的任何生成产物均未被此实验替换。

证据为第二轮工作区内 `offline_fact_scope_probe.json`；复现脚本为 `backend/.local/audit_d1_deepseek_20261108.py`。

## 数学解释复核及边界

事实 LF024 已登记“四舍五入”，但 UNC002 又将 half_up 与 half_even 列为可选歧义。对非负损毁数，题目给出的四舍五入应按 half_up 执行，不能把银行家舍入作为等价解释。

以“当周投入数量等于附件中的需求、损毁率10%”为条件做 Decimal 精确计算，两种舍入在 14 周不同，half_even 累计少计14台损毁。例如第2周需求5，10%为0.5，half_up为1，half_even为0。该对照没有推导最优采购或成本，也未声称在线模型实际使用了 half_even；在线模型尚未执行。完整列表保存为 `input_and_rounding_audit.json`。

Q1–Q4 状态方程、整数损毁、训练容量、阶梯成本顺序以及 Q5 两方案初始状态与成本差额均未获得可执行模型证据，D2 保持未完成。

## 复现与工件

在仓库的 `backend` 目录运行下列命令。前两条会再次产生 API 调用并创建新任务目录；最后一条仅审查本报告对应的两次既有运行。

```powershell
..\.venv\Scripts\python.exe -u .local/run_d1_deepseek_20261108.py
..\.venv\Scripts\python.exe -u .local/run_d1_deepseek_20261108.py --mode strict --max-tokens 32768
..\.venv\Scripts\python.exe .local/audit_d1_deepseek_20261108.py
```

两次运行均位于 `backend/project/work_dir/<运行 ID>/`。主要记录包括 `run_metadata.json`（含 Git HEAD）、`run_events.jsonl`、`llm_calls/*_request.json`、`llm_calls/*_response.json`、`contract_diagnostics/`、`audit_summary.json`。第二轮另有五份 `modeling_failure_problem*.json`、Guidance、三层报告和 `workspace.zip`。

现状摘要：核心流为原件→证据→ProblemSpec→ModelIR生成→审核→阻断Guidance；最危险的耦合是将事实覆盖记录同时用于下游事实可见性。本轮新增本地复测脚本、审查记录和工作区产物，更新唯一长线计划，未修改后端/前端业务代码。D1复测审查完成，下一未完成项为D2；恢复其前提需要先解决本轮生成阻断，模块边界替换须依仓库规则另行明确授权。
