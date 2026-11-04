# Guider 长线计划：可复查、数值精准的建模 Agent 工具

本文件是唯一长期执行计划；执行规则以 AGENTS.md 为准。

## 目标与授权

目标（用户 2026-11-04 设定）：使 MCMer 对现实问题与数学建模题目都能给出有效、数据计算科学可复查、相对精准的结果。“计算通过”不得被解释为“模型完整表达题意”；所有数值必须能从原始材料逐项复算。

GO：用户于 2026-11-04 批准按本计划重写。涉及 ModelIR 合同、生成合同或证据画像结构变更的步骤（标注【需批准】），实施前须先提交 RED 证据与替换方案并获用户确认。

## 已锁定不变量（前期阶段 A–C 压缩）

1. InputCatalog/EvidenceProfile 是题面与附件唯一证据入口，不得将已登记来源误报缺失。
2. ProblemSpec 使用强类型子题、事实、歧义与依赖；Draft 仅单向提升至 canonical，拒绝未知符号、跨作用域引用和依赖环。
3. ModelIR 使用类型化执行组件，支持预测工件到 MILP 的运行时物化，禁止任意生成代码或题名路由。
4. 建模、审核和执行按子题保留可解结果；三层验证不能把子集通过冒充整题完成。
5. 静态参数按确切文档、列/单元格和索引标签核对；拒绝错序、错列、未知来源与伪造 artifact 前缀。
6. 运行失败沿全部跳过节点传播；失败子题的参数、结果和图表不进入独立成功子题的公开注册表。
7. 注册器、领域实现、生成合同、阶段验证、呈现和共享写作支持分别拥有实现；调用方直接导入实际 owner，无兼容门面或循环依赖。
8. 测试只依赖入库 fixture：真实附件完整保存在 `backend/tests/fixtures/real_attachments/<case>/data/`，基线 `input_sha256` 校验原件；`test_fixture_reproducibility.py` 拒绝引用 `project/work_dir`。新 clone 后端全量 517 passed。

## 2026-11-04 已验证缺口（本计划的依据）

| 编号 | 缺口 | 复现证据 |
| --- | --- | --- |
| G-完整性 | 漏写业务方程仍全部通过 | `generic_multiperiod_inventory.json` 删除 `initial_balance`+`state_balance` 后目标 26→0、采购全 0；删延迟目标→1；删容量目标→15。三种变体运行时校验 ok，MILP 适用性评估 passed。顶层方程可为 `0 = 0`。 |
| G-数据 | Excel 未被完整物化 | `artifacts/evidence_profile.py` 每表仅采样至多 6 行；大表参数仍由 LLM 手抄；`validate_static_parameter_evidence` 在事实与画像均缺失时静默返回 `[]`。 |
| G-单位 | 优化主路径无单位/量纲 | `IndexedParameterSpec`、`IndexedVariableSpec` 无 unit 字段；其他路径仅字符串相等比较，无换算与量纲推导。 |
| G-覆盖 | 算子族窄 | 算子目录 20 项；无随机仿真/蒙特卡洛、非线性 ODE、一般 NLP、统计检验、敏感性分析算子（仅目录核对，尚无真实题实验）。 |

## 核心流与模块归属

```text
题面/附件 -> 证据目录(完整物化) -> ProblemSpec(需求义务) -> Modeling -> Review
  -> 编译(量纲+义务覆盖) -> 执行 -> 完整性探测/参考解对拍 -> 结果与图表注册表
  -> 独立验证 -> Guidance(可读方程预览+复算清单)/ZIP
```

## 当前步骤

CURRENT：F3。

## 活动任务

阶段 E（可复现基线）已完成并压缩为不变量 8。

### F：模型完整性（不再仅依赖 LLM Reviewer）

- [x] F1 RED：`tests/test_guidance_model_completeness.py` 锁定删平衡、删延迟、删容量三种变体必须被确定性层（事实/需求覆盖 → 语义 → 编译 → 执行 → 适用性）拒绝，完整模型为正对照；三项已由 F2a/F2b 转绿，无 xfail。
- [x] F2a 使用覆盖：`ModelIRSemanticValidator` 新增 `indexed_parameter_unused`（参数在目标/约束中无非零使用）与 `vacuous_equation`（方程不含任何符号）；约束非空已由 schema `terms min_length=1` 保证。删平衡、删容量变体转绿，fixture 移除 `0 = 0` 占位方程；后端 523 passed、1 xfailed（删延迟留给 F2b）。
- [x] F2b 结构需求义务：ProblemSpec 新增封闭类型 `modeling_requirements`（state_conservation、lagged_link、upper_capacity、demand_satisfaction；lag 仅限 lagged_link；来源与事实须已登记且同作用域），索引约束新增 `fulfills_requirement_ids`。`guidance/requirement_coverage.py` 按类型检查结构：守恒为同一状态 t 与更早期常数反号等式加字面边界；滞后为跨变量 offset=-lag 联动加目标边界；容量为引用参数的 less_equal；需求为引用参数的 equal/greater_equal；需求绑定事实时参数须登记同一事实。事实审计提示登记需求，约束阶段接收作用域内需求并以校验失败触发重生成；规范层在 generation_validation 与 stages 两处复核。后端 550 passed。边界：需求由 LLM 事实审计产生；仅覆盖索引线性模型，算子图路径留待 I 阶段。
- [ ] F3 退化解探测：执行后检查全零决策、目标为 0 而需求为正、约束全部松弛等退化信号，转为验证失败并给出原因。
- [ ] F4 独立参考解：小规模实例用独立构造（枚举或另一求解路径）对拍目标值与关键决策，不一致即阻断。
- [ ] F5 可读方程预览：由同一份 ModelIR 渲染首期、转移期、末期方程与参数来源表，进入 Guidance 供人工复核。

### G：数据统一物化

- [ ] G1 RED：真实 Excel 中第 6 行以后的参数可被精确绑定与核验；事实与画像均缺失时核验不得静默通过。
- [ ] G2 证据画像对已登记表格完整物化原始单元格（含工作表、坐标、文件哈希），采样仅用于提示词上下文。【需批准：证据画像结构变更】
- [ ] G3 参数由系统按引用读取填值，LLM 只写来源引用与索引映射；保留现有错序/错列/伪来源拒绝回归。【需批准：生成合同变更】

### H：单位与量纲

- [ ] H1 RED：同一约束中混用不同量纲（如元与万元、小时与分钟）的模型必须被拒绝。
- [ ] H2 为索引参数与变量增加单位，编译期做量纲一致性检查；换算因子显式记录来源。【需批准：ModelIR 合同变更；引入新依赖须说明】

### I：按真实题补齐能力

- [ ] I1 选一道现实题（非线性动态或随机类），确认阻断原因后再补对应算子，每个新算子必须同时有独立验证器和反例测试。
- [ ] I2 敏感性分析：对关键参数扰动输出结论稳定性，作为 Guidance 标准段落。

### D：真实题数学验收

- [ ] D1 复跑 2022 五一 A 题（基线任务 20260715-135019-066fc4b9），审查证据、ProblemSpec、ModelIR、执行计划、数值、三层报告和 Guidance。
- [ ] D2 人工核对 Q1–Q4 状态转移、整数损毁、训练容量、阶梯成本及 Q5 两方案口径，不以测试绿色或求解器 success 代替数学审核。
- [ ] D3 对 I1 选定的现实题做同等端到端验收与人工复算。

## 模块导航

| 职责 | 实现归属 |
| --- | --- |
| 证据入口与画像 | artifacts/input_catalog.py、artifacts/evidence_profile.py |
| 静态参数核验 | guidance/parameter_evidence.py |
| 结构需求义务 | guidance/requirement_coverage.py |
| 算子注册与数学实现 | guidance/operator_catalog.py + operator_runtimes/ |
| 模型充分性注册与领域校验 | guidance/model_validators.py + model_validation/ |
| Canonical、生成草稿、审核合同 | guidance/model_ir.py、model_ir_drafts.py、model_review_contracts.py |
| 编译与语义校验 | guidance/model_ir_compiler.py、model_ir_semantics.py、indexed_linear.py |
| 拆题、建模、审核与生成校验 | guidance/decomposer.py、modeler.py、reviewer.py、model_generation.py、generation_validation.py |
| 阶段编排、结果验证、Guidance 打包 | guidance/stages.py、verification_stage.py、guidance_stage.py |
| Guidance 主渲染与段落/诊断 | guidance/renderer.py + guidance_rendering/ |
| 路由与共享写作/图表支持 | core/workflow.py、workflow_runtime.py、writing_support.py、chart_support.py |

## 执行约束

每次代码修改前完整阅读本文件，确认当前未完成步骤；行为修复先运行最小失败测试。子任务完成立即勾选并移动唯一 CURRENT，完成阶段压缩为不变量。修改后报告现状摘要：核心流、危险耦合、本次变化、完成项、下一项。

## 停止条件

需要题目专用字段/模板、重型或商业求解器、任意代码执行，或本计划之外的模块替换时，停止该项并用实验证据说明。【需批准】步骤未获确认不得实施。真实题外部服务不可用时如实记录；工程验证与数学验收分别报告。
