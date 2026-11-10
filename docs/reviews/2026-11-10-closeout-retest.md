# C2 最小交接修复与最后复测

封档注记：本文保留复测时的审计状态。用户已于 2026-11-10 批准按最小闭环未完成的事实封档；最终状态以 [封档记录](../compute_first_guidance_plan.md) 为准，下文历史建议不再作为活动任务。

日期：2026-11-10。基线为 `88759a6`，用户已明确批准参数交接修复及最后一次有界复测。本文记录验证证据；执行范围与后续步骤只维护于 `docs/compute_first_guidance_plan.md`。

## 修复验收

目标行为：约束生成看到的数值与执行参数相同，保留坐标和来源；未知运行时数值仍用工件引用。架构输出使用已声明工件，错误在架构可重试阶段反馈。

RED：`test_guidance_contract_agents.py` 与 `test_guidance_recovery.py` 的 6 项选中测试失败，证据 `backend/.local/c2-handoff-formal-red.xml`。静态参数误走空运行时引用、运行时目录丢失结构、未声明输出未触发第二次调用，均对应实际缺失行为，不是导入或语法错误。包含旧目录缓存迁移检查。

GREEN：`FormulationContext.from_core` 直接接收 `IndexedLinearDataSpec`，深拷贝规范参数；生产调用方传入已物化数据。提示说明百分比已转比例、来源单位不要求再次换算。模型合同提供工件声明集合，生成阶段拒绝未声明输出，编译阶段保留复验。原始模型响应没有修补。

针对性命令（仓库根）：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest backend/tests/test_guidance_contract_agents.py backend/tests/test_guidance_recovery.py -k 'formulation_context or exposes_runtime_parameter or architecture_recovers_undeclared or constraint_cache_rejects' -q -p no:cacheprovider
```

结果：6 passed，`backend/.local/c2-handoff-formal-green.xml`。上下文变化使现有请求摘要不同，旧目录缓存拒绝；相同快照复用、值改变重新请求，不新增兼容分支或缓存旁路。

REFACTOR：复用规范参数合同，移除只含 ID/来源的旧参数目录和字符串化运行时引用；编译器原有工件集合逻辑由生成与编译共用。没有额外拆文件或重排主流程。

完整命令（`backend` 目录）：

```powershell
$env:LITELLM_LOCAL_MODEL_COST_MAP='True'
$env:OPENBLAS_NUM_THREADS='1'
$env:OMP_NUM_THREADS='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests .local/test_c2_handoff_red.py -q -p no:cacheprovider --junitxml=.local/c2-handoff-full-green.xml
```

结果：**925 passed，43.88 秒，无 skip/xfail**。包括正式后端 923 项与首跑原响应的 2 项回放；后者仍依赖本地首跑证据，不算可移植正式测试。Windows asyncio 测试需允许本地 socketpair 通信。

中间失败保留：从仓库根调用完整测试曾因 `scripts` 导入路径中止，改用后端目录；随后 8 项旧类别测试夹具的输出未声明，被新检查提前拒绝。仅将夹具声明从 result 改为其实际输出 method_result，数学类别断言保持，记录 `c2-handoff-regression-before-fixture.xml`。

## 真实复测

已按修复提交 `ba369e1` 执行并自然结束为 **blocked**。复用 `backend/.local/closeout-portfolio-20261110/ledger.json`，首跑 7 次、183415 tokens、603.625 秒保持，没有清账或更换任务。

修复证据使用已保存的 `backend/.local/c2-handoff-full-green.xml`；命令在仓库根执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 backend/scripts/run_closeout.py --root backend/.local/closeout-portfolio-20261110 --live --repair-evidence backend/.local/c2-handoff-full-green.xml
```

该命令是本轮执行记录；两轮机会已用完，不能把它当作现在可以追加运行的命令。

| 指标 | run-02 | 两轮累计 |
| --- | --- | --- |
| 实际 API 请求 | 7 | 14 |
| 已知 tokens | 128476 | 311891 |
| 未知用量调用 | 0 | 0 |
| 运行秒数 | 363.562 | 967.187 |
| 数学审核 API / 求解 | 0 / 未进入 | 0 / 未进入 |
| 已验证结果 | 0 | 0 |

本轮拆为一个子题、提取 47 项事实并登记 3 处理率口径疑义。来源解释及复核消解 2 处、保留 1 处。架构首响应 length 后恢复成功，选择纯索引模型；参数阶段通过，19 项参数原响应离线物化的值包含银行利率 0.05、S1 收益率 0.28、资金 10000、风险上限 200。此离线核对没有修改或替代模型响应。

剩余 88109 tokens 不足以满足约束请求的保守预留，运行器在调用供应商之前拒绝，错误为 `total token budget cannot reserve this request`。合同诊断中的一次 `provider_calls` 是请求包装器的尝试计数；没有对应实际外发，真实调用数量以预算账本及 api 请求记录为准。没有约束响应、数学审核或求解结果，不声称百分比修复已通过真实数学验收。纯索引架构没有图输出，也不能由这一轮推断输出校验改变了模型选择。

任务、生产代码和配置哈希未改变；凭据扫描通过，运行锁已释放。`guidance.md` 是阻断诊断，审核 PASS 仅说明阻断披露合格。`result_registry.json` 的 `verified_results=[]`，冻结子任务仍未完成。

持久证据：`backend/closeout/closeout_run_receipts.json` 保存两轮代码版本、冻结预算、逐调用用量、阻断原因以及原请求响应 SHA。原始明细仍保留在本地两个运行目录。此回执不是数学复现包，也不满足 C3/C4。按事前停止条件不追加第三轮；建议用户按未完成闭环的事实封档。

## 现状摘要

1. 核心流仍为原件→拆题→来源许可→ModelIR→审核→求解→验证→报告。
2. 参数交接已修复；真实约束生成尚未发生，工程通过不等于数学通过。
3. 本次完成交接修复及最后复测，保留全部用量与失败事实。
4. C2.1 与 C2.2 执行完成；C2 闭环未完成，C3/C4 未验收，项目未封档。
5. 下一项为用户决定是否按未完成事实封档，不追加 API 请求。
