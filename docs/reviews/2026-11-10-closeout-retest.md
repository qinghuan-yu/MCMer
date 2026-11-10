# C2 最小交接修复与最后复测

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

尚未执行。复用 `backend/.local/closeout-portfolio-20261110/ledger.json`，首跑 7 次、183415 tokens、603.625 秒保持。剩余最多 17 次请求、216585 tokens、1196.375 秒，另受请求前保守预留约束。只有一次 run-02；不清账、不更换任务、不追加第三轮。

修复证据使用已保存的 `backend/.local/c2-handoff-full-green.xml`；命令在仓库根执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 backend/scripts/run_closeout.py --root backend/.local/closeout-portfolio-20261110 --live --repair-evidence backend/.local/c2-handoff-full-green.xml
```

## 现状摘要

1. 核心流仍为原件→拆题→来源许可→ModelIR→审核→求解→验证→报告。
2. 参数交接已修复；LLM 生成数学仍须由独立资金/风险/费用检查裁决。
3. 本次完成参数快照、架构错误前置及调用方/测试迁移，保留失败证据。
4. C2.1 完成，C2 闭环未完成，项目未封档。
5. 下一项为 C2.2 最后一次有界复测。
