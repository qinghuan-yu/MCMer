# Guider 长线目标：高可信建模指导流水线

> 本文件是项目唯一长线计划。任何代码修改前必须完整阅读本文件，并确认当前步骤。

## 强制执行协议

- **修改前必读**：每次修改代码前，必须完整阅读本计划中的当前步骤。
- **测试先行**：新功能和缺陷修复必须遵循 Red-Green-Refactor；生产代码前必须先看到目标测试因缺失行为而失败。
- **重构而非修补**：若现有模块与目标行为冲突，禁止添加 `if/else`、开关、兼容分支或转发适配器。必须停止实现，提交模块级重构提案并获得用户批准。
- **即时更新状态**：每完成一个子任务，立即将对应复选框更新为 `[x]`，并确保只有一个 `CURRENT` 步骤。
- **删除旧计划**：不得在 `docs/` 中保留被本文件取代的路线图、优化计划或指导计划。
- **强制现状摘要**：每次修改后必须报告核心数据流、致命耦合点、本次变化、已完成复选框和下一步骤。

## 当前步骤

**CURRENT：Phase 2 - 建立模型审核闭环。**

- [x] 将仓库执行规则固化到根目录 `AGENTS.md`。
- [x] 删除与本计划冲突的旧版路线图文档。
- [x] 提交 `GuidancePipeline` 模块级重构提案并获得用户批准。
- [x] 将最终链路收敛为六个必要业务阶段，并冻结“不新增非必要依赖”规则。
- [x] RED：先写端到端阶段顺序测试，证明当前 Pipeline 缺少拆题、建模、审核、计算、复核和指导输出契约。
- [x] RED：审核状态不是 approved 时，计算阶段必须完全不运行。
- [x] RED：计算阶段必须一次生成并执行完整 `solve.py`，不得逐步工具调用。
- [x] GREEN：整体替换临时算术主链，不保留旧新双轨或 feature flag。
- [ ] 审核失败只允许返回建模阶段一次。
- [ ] 第二次仍未通过则产出 blocked guidance，不进入计算。
- [ ] 用不可辨识参数、错误单位和数据不足三个 fixture 验证阻断。

## Go / No-Go

- **判断**：Go，但停止把简易公式计算器扩展为通用求解器。
- **理由**：最终产品必须帮助使用者面对真实题目快速找准建模方向，并给出可复现的必要计算；可信度注册表是护栏，不是产品主链。

## 目标结果

输入真实赛题及附件后，系统按以下顺序产出一份高可信 `guidance.md`：

```text
题目与数据解析
  -> 题目拆解 problem_spec.json
  -> 建模方案 model_spec.json
  -> 独立模型审核 approved_model_spec.json
  -> 完整求解程序 solve.py
  -> 本地一次性执行 execution_manifest.json
  -> 数值与结果复核 verification_report.json
  -> 必要说明图片 figures/
  -> 参数与结果注册表
  -> 建模指导 guidance.md
  -> 机器可信度审计 guidance_audit_report.json
```

最终指导文件必须说明：问题类型、建模方向、备选方法、选择理由、假设、符号、公式、参数来源、数据处理、求解步骤、必要结果、图片解读、复核结论、局限、阻断项和复现方式。

## 边界

### 包含

- 题目拆解、模型提出、独立审核、程序生成、本地执行、结果复核、图表、Markdown 组织与审计。
- Python 作为首个完整求解运行时。
- 文件化阶段契约，任何阶段都可单独检查和重跑。
- 参数与数值必须追踪到题设、附件、代码或明确假设。

### 非目标

- 不直接生成可提交论文。
- 不为 guidance 生成 DOCX。
- 不恢复多轮 CoderAgent `execute_code` 循环。
- 不把所有数学模型压缩成算术表达式 DSL。
- 第一阶段不同时支持 MATLAB、云端沙箱和分布式任务。

### 延后

- MATLAB/北太天元执行器。
- 多语言代码生成。
- 跨任务结果缓存和分布式长任务。
- 论文自动转化。

## 当前状态与漂移诊断

### 可保留

- guidance 独立 API 与 Markdown 下载。
- `parameter_registry.json`、`result_registry.json`、blocked/partial 披露。
- `guidance_audit_report.json` 和历史任务兼容。
- guidance 与旧 paper workflow 的运行隔离。

### 已发生的漂移

- **目标漂移**：近期链路从“完整建模指导”收缩成了“结构化算术计算”。
- **阶段漂移**：单个 planner 同时承担拆题、建模和求解设计，缺少独立审核。
- **验证漂移**：审计能证明文字可追踪，却不能证明模型合理或计算完整。
- **实现漂移**：`compute/runner.py` 被误当作通用建模求解器，真实回归、优化和仿真无法落地。

## 核心架构规则

1. 每个阶段只消费上一阶段的已保存契约，不依赖隐式对话记忆。
2. 模型审核未通过时不得生成求解程序。
3. 代码生成器一次生成完整 `solve.py`；执行器直接运行程序，不让 LLM 逐步调用工具。
4. 求解程序必须写出结构化结果、参数表、运行清单和必要图片。
5. 结果复核读取代码与产物，至少检查输入、公式口径、单位、约束、残差或稳健性。
6. guidance writer 只能引用审核通过的模型和已登记结果。
7. 简易公式 runner 只作为小计算辅助模块，不决定主链架构。
8. 只允许六个业务阶段：拆题、建模、模型审核、数值计算、结果复核、指导输出；不得为每个小动作新建 stage。
9. 优先复用现有依赖：Pydantic、LocalCodeInterpreter、NumPy、Pandas、SciPy、Matplotlib、openpyxl；新增依赖必须先证明现有能力无法满足需求并获得用户批准。
10. API、WebSocket 和 Markdown 下载契约保持不变；内部 Pipeline 直接替换，不引入兼容开关。

## 最小目标架构

```text
DecompositionStage
  -> problem_spec.json
ModelingStage
  -> model_spec.json
ModelReviewStage
  -> approved_model_spec.json
CalculationStage
  -> solve.py + execution_manifest.json + parameter_registry.json + result_registry.json + figures/
ResultVerificationStage
  -> verification_report.json
GuidanceStage
  -> guidance.md + guidance_audit_report.json
```

每个阶段只有一个公开入口，复杂性由阶段内部吸收。Pipeline 只负责顺序、进度和失败传播，不理解模型公式、求解代码或 Markdown 章节细节。

## 文件契约

### problem_spec.json

- 子问题、目标、对象、数据、约束、输出要求。
- 题设事实与待确认信息。
- 问题类型及候选建模方向。

### model_spec.json

- 每个子问题的候选模型与推荐模型。
- 假设、符号、公式、参数、目标函数、约束和求解步骤。
- 所需结果表和图表计划。

### approved_model_spec.json

- 审核状态：approved、revision_required 或 blocked。
- 对可辨识性、维度、约束、数据充分性和计算可行性的判断。
- 审核后的最终模型规格及必须修复项。

### execution_manifest.json

- `solve.py` 哈希、运行时间、退出状态、标准输出和错误摘要。
- 生成文件清单。
- 不得把程序成功退出等同于结果正确。

### verification_report.json

- 输入覆盖、公式一致性、单位、约束满足、残差/误差、敏感性与复算状态。
- verified、partial、blocked 结论及理由。

## 阶段计划

### Phase 1：六阶段真实垂直链路

**目的**：用五一 A 题问题一证明目标链路成立，不先追求题型通用化。

- [x] RED：阶段顺序测试要求严格执行拆题、建模、审核、计算、复核、指导输出。
- [x] RED：审核状态不是 approved 时，计算阶段必须完全不运行。
- [x] RED：计算阶段必须一次生成并执行完整 `solve.py`，不得逐步工具调用。
- [x] 实现 `DecompositionStage`，保存 `problem_spec.json`。
- [x] 实现 `ModelingStage`，保存含候选方法、选择理由、假设、公式、参数和图表需求的 `model_spec.json`。
- [x] 实现 `ModelReviewStage`，审核可辨识性、数据充分性、公式与单位，并保存 `approved_model_spec.json`。
- [x] 实现 `CalculationStage`，复用现有代码生成能力和 `LocalProgramExecutor`，生成参数表、结果表和必要图片。
- [x] 实现 `ResultVerificationStage`，检查输入、单位、约束、残差和复算结果。
- [x] 实现 `GuidanceStage`，输出参数来源表、建模细节、必要图片、复核结论和复现路径。
- [x] 删除临时 `LLMGuidancePlanner -> run_solve_spec` 主链，不保留运行开关。

**退出证据**：真实附件被读取；支路模型完成最小二乘拟合；关键参数、残差指标和说明图落盘；`guidance.md` 能从参数追到数据、代码和复核结论。

**已验证**：2026-06-19 使用历史任务中的原始 `附件(Attachment).xlsx` 完成实测；断点为 30，支路斜率约为 0.5 和 1.0，RMSE 为 `1.63e-14`，复核状态为 verified，guidance 审计为 PASS，并生成真实 Jupyter notebook 与 PNG。

**止损规则**：若完整程序仍需 LLM 在运行期间逐步补代码，停止后续开发，先修正代码生成契约。

### Phase 2：模型审核闭环

**目的**：让错误模型在计算前失败，而不是靠最终审计兜底。

- [ ] 审核失败只允许返回建模阶段一次。
- [ ] 第二次仍未通过则产出 blocked guidance，不进入计算。
- [ ] 用不可辨识参数、错误单位和数据不足三个 fixture 验证阻断。

**退出证据**：故意注入的模型错误均在执行 `solve.py` 前被定位。

### Phase 3：复核与图片质量

**目的**：保证计算结果不只是“程序成功运行”。

- [ ] 回归类结果检查拟合误差、残差和参数稳定性。
- [ ] 图片只保留数据概览、模型说明、结果拟合和复核诊断中确有必要的类型。
- [ ] guidance 中每张图绑定来源数据和结果 ID。

**退出证据**：篡改参数或结果时，复核报告阻断；删除图片时，guidance 审计指出缺失。

### Phase 4：真实题型扩展与旧链删除

**目的**：垂直样例稳定后再扩展，不提前抽象通用框架。

- [ ] 增加一个优化题 fixture。
- [ ] 增加一个预测或评价题 fixture。
- [ ] 删除 `_legacy_paper_writing_workflow` 及 guidance 不再使用的旧修复代码。
- [ ] 保留 polish 所需代码并迁入独立边界。

**退出证据**：三类真实题均走同一六阶段接口；guidance 调用图不再依赖旧写作 workflow。

## 验证规则

- 单元测试验证 schema、执行器、注册表和审计。
- 集成测试使用真实 XLSX/CSV 文件，不只测试内存算术。
- 端到端测试必须检查实际参数值、图表文件、复核结论和 Markdown 引用。
- 每阶段完成后运行完整后端测试和前端构建。
- Phase 1 不以 mock 全绿作为完成；必须包含一次真实 XLSX、真实 Jupyter 执行和真实 PNG 生成。

## 红蓝对抗

- **红队**：模型生成一份能运行但公式错误的程序，系统仍可能输出漂亮结果。
- **蓝队**：独立模型审核、结果复核、基线/残差/约束检查和注册表共同阻断。
- **残余风险**：审核模型本身也可能判断错误，因此必须保留代码、数据、参数来源和不确定性披露供人工复核。

## 第一执行步骤

提交 `GuidancePipeline` 模块级重构提案；批准后，用五一 A 题问题一证明完整链路能够真实读取附件、完成最小二乘拟合、产出参数表、残差指标、说明图和可复核 Markdown。
