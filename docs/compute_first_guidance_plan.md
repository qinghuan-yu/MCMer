# Guider 长线目标：题面证据、索引优化与真实题闭环

> 本文件是项目唯一长线计划。任何代码、测试、Schema 或配置修改前必须完整阅读本文件，确认唯一 `CURRENT`，并严格执行 Red-Green-Refactor。

## Go / No-Go

- **判断**：`GO`。
- **批准状态**：用户已批准替换题面证据入口、阻断验证合同、扩展通用索引 MILP 与未来期预测算子、assumption-aware ModelIR 合同、索引 Draft/canonical 生成边界、Constraints 紧凑符号上下文边界、Modeler/Reviser 共享架构合同边界、确定性模型完整性与修订闭环审核边界、结构化题面事实覆盖、ProblemSpec Draft/canonical 生成边界、Guidance 分阶段 LLM 预算边界、Modeling 逐子问题隔离边界、预测输出到索引 MILP 的组合执行图边界、canonical 类型化执行组件与节点级事实来源边界、逐子题审核与部分执行边界，以及强类型子问题事实与依赖边界。
- **盲测基线**：2022 五一数学建模 A 题任务 `20260715-135019-066fc4b9`。输入完整，但内嵌表格未进入数据证据层；审核阻断路径因三层验证字段缺失而错误变为 `failed`。

## 强制执行协议

- 每次修改前完整阅读本计划，指出当前未完成步骤。
- 每个行为变更先写最小失败测试并确认 RED，再实现 GREEN，最后在测试绿色时重构。
- 若发现本计划之外的模块边界冲突，立即停止并重新取得用户批准，禁止增加兼容分支、题名判断或问题专用模板。
- 每完成一个子任务立即把对应复选框改为 `[x]`，并移动唯一 `CURRENT`。
- 不创建并行 roadmap；目标变化时直接覆写本文件并删除过时计划。
- 不支持的数学能力必须形成有证据的 `partial/blocked`，不得伪造数据、参数、公式或数值结果。
- 每次修改后报告现状摘要：核心数据流、最危险耦合、本次变化、完成复选框、下一未完成步骤。

## 当前步骤

**CURRENT：Phase 4 强类型子问题事实与依赖投影 REFACTOR。**

## 目标核心数据流

```text
Raw problem source + supplementary files
  -> InputCatalog (ordered paragraphs, structured tables, source refs, hashes)
  -> EvidenceProfile (question tables + uploaded datasets)
  -> ProblemSpec
  -> Model IR (indexed variables/constraints or explicit blocked capability)
  -> ModelReview
  -> ModelIRCompiler
  -> generic operator graph (forecast + MILP)
  -> registries + independent verification
  -> guidance.md + workspace ZIP
```

`InputCatalog` 是题面事实与表格的唯一证据入口。Pipeline 不得把题面正文与 `data/` 文件视为两套互不相见的数据世界。

## 替换边界

| 旧边界 | 新边界 | 迁移要求 |
| --- | --- | --- |
| `ingest_question_source` 仅返回拼接文本 | 版本化 `InputCatalog` | 保留段落/表格顺序、单元格、来源定位与哈希 |
| `_list_data_files` 与 `_build_data_profile` 仅扫描 `data/` | 统一 `EvidenceProfile` | 同时登记题面内嵌表格和补充文件，删除旧双轨 |
| 来源正则只识别“附录” | 结构化来源需求 | 统一识别附件、附录、Appendix，并绑定实际证据 |
| blocked 分支手写旧 `VerificationReport` | 统一三层验证报告工厂 | 所有退出路径使用同一合同，不保留旧字段适配 |
| 仅有连续二次优化 | 索引线性模型 + `optimization.milp` | 使用 SciPy `milp`，不新增赛题专用求解器 |
| 只有时间留出评估 | 未来期预测算子 | 候选模型、滚动验证、基线、区间与未来时点输出齐全 |
| LLM Draft 直接复用 strict canonical 索引节点 | Draft 专用 selector/term/constraint DTO + 单向 promote | `null` 只在 Draft 边界规范化；Core 错误不得延迟到 Constraints 重试 |
| Constraints 直接接收完整稠密 Core 与自由符号字符串 | 确定性 `ConstraintGenerationContext` | 只暴露索引、参数和变量符号目录；完整 Core 仅留在系统侧用于 promote 与语义验证 |
| Modeler 与 Reviser 各自维护 ModelIR Draft 规则 | 唯一共享架构合同 + 阶段职责后缀 | indexed route、operators、assumptions、证据与单子问题覆盖规则必须一致；修订阶段只追加审核处理职责 |
| LLM Reviewer 单独判断模型完整性且复审看不到首次整改要求 | 确定性架构/Core 覆盖校验 + 结构化修订谱系 | 不完整模型在 Reviewer 前失败；复审逐项引用模型路径并证明首次要求已接受 |
| `ProblemSpec.locked_facts/uncertainties` 为自由字符串 | 结构化、证据绑定且有子问题作用域的事实/歧义记录 | 同键同作用域冲突必须拒绝；可解模型逐项提供事实到 ModelIR 路径的覆盖证据，不保留字符串兼容分支 |
| Decomposer 直接复用 strict canonical `ProblemSpec` | `ProblemSpecGenerationDraft` 单向提升到 canonical | Draft 接受自然语言标识符；系统生成稳定 ASCII ID、同步作用域引用并拒绝规范化碰撞，canonical 正则保持严格 |
| Guidance 全阶段共用单一 `llm_timeout` | 显式分解/架构/Core/Constraints/审核预算 | 长结构生成获得独立时限；删除共享标量，诊断公开实际阶段预算 |
| Modeling 任一子问题异常即终止整个 ModelIR | 逐子问题成功/阻断结果聚合 | 单题完整生成与校验封装为独立边界；异常只阻断对应题并保留独立诊断，全局兜底仅处理切片前灾难性失败 |
| `ModelReview` 单一全局状态使一题阻断短路全部可解子题 | 逐子题审核决策 + 显式可执行子题集合 | blocked 子题保留诊断；仅 approved 的 solvable 子题进入编译执行；结果、验证、Guidance 与前端整体状态为 `partial`，不得把部分结果冒充整题完成 |
| `operator_graph` 与 `indexed_linear` 互斥，MILP 参数只能编译期稠密给定 | 预测/估计节点输出可绑定索引参数的组合执行图 | 参数来源使用静态证据或运行时工件的强类型 union；编译器生成有序 DAG，MILP 节点运行时物化参数后再展开矩阵，不保留互斥路由兼容分支 |
| canonical `SolvableModelIR` 平铺 `variables/operators/indexed_linear_model` 且索引变量重复登记 | 类型化 canonical execution components | operator graph 与 indexed linear component 各自拥有变量和执行节点；编译器只消费组件，不保留平铺字段兼容分支 |
| 架构 Draft 在 Core/Constraints 生成前猜测 `fact_bindings.model_paths` | 节点级事实来源 + 最终确定性绑定 | 参数、约束和算子节点登记实际使用的 fact ID；组装器按最终 canonical JSON 路径生成绑定并拒绝遗漏、未知、重复或不存在路径 |

## 非目标

- 不新增血管机器人、库存题或五一 A 题专用生产模板。
- 不让 LLM 直接生成并执行 Python、矩阵或 104 周硬编码答案。
- 不承诺首轮即支持非线性整数规划、随机规划或商业求解器。
- 不用提示词补丁掩盖证据目录或执行合同缺陷。

## 阶段计划

### Phase 0：修复诚实阻断闭环

- [x] RED：审核状态为 blocked 时，必须产出包含 `execution_verified`、`evidence_supported`、`model_adequate` 的 `verification_report.json`、注册表和 `guidance.md`，最终任务状态为 `blocked`。
- [x] GREEN：用统一构造器生成未执行/证据未评估/模型阻断三层状态，禁止伪造候选模型角色。
- [x] REFACTOR：删除 blocked 路径中的旧 `status=` 手写合同，所有失败与阻断出口共享同一报告边界。

**退出证据**：盲测中的同类 ModelReview 阻断不再触发 Pydantic ValidationError；Guidance 清楚说明阻断原因和恢复条件。

### Phase 1：统一题面与附件证据

- [x] RED：DOCX 内嵌成本表和 104 周需求必须以稳定表格引用进入 InputCatalog/EvidenceProfile；“附件1/附件2”必须判定为 available。
- [x] RED：ProblemSpec 或 Model IR 把已登记证据声明为 missing 时必须被确定性拒绝。
- [x] GREEN：实现版本化 InputCatalog，保留段落、表格、行列、单元格、来源路径和 SHA-256。
- [x] REFACTOR：以 EvidenceProfile 整体替换 `_list_data_files`、仅 `data/` 的 `_build_data_profile` 和自由文本附件判断。
- [x] GREEN：Guidance ZIP 包含原始题面、InputCatalog、EvidenceProfile 及其哈希关联。

**退出证据**：同一 DOCX 不再出现“task_summary 有数据、data_profile 为空”的矛盾；题面表格与外部 XLSX 使用同一证据引用语义。

### Phase 2：通用索引 MILP

- [x] RED：Model IR 支持有限索引集、整数/二元/连续变量、按索引展开的线性目标、状态转移、边界和线性约束；维度或索引错误必须在编译前失败。
- [x] RED：MILP 算子必须拒绝非有限系数、非法积分类型、不可行模型和未声明输出。
- [x] GREEN：实现索引线性 IR 展开器与通用 `optimization.milp`，仅接受编译后的结构化矩阵。
- [x] GREEN：独立验证目标复算、整数性、约束违反、求解下界/间隙和状态守恒，并比较简单可行基线。
- [x] GREEN：用非赛题命名的多期库存 fixture 验证训练/调试延迟、保养、损毁常量和阶梯成本线性化。

**退出证据**：2022 A 题 Q1-Q4 可由同一索引 IR 编译，不包含题名路由或硬编码周答案；若求解器不能证明质量则降级而非宣称最优。

### Phase 3：未来期预测

- [x] RED：预测算子必须输出未来指定时点，而不只是历史留出集；缺少未来索引、时序无序或样本不足时拒绝执行。
- [x] GREEN：实现趋势/周期基函数候选与季节朴素基线，使用滚动起点评估选择模型并生成预测区间。
- [x] GREEN：独立复算时间切分、未来索引、误差、基线比较、区间和参数敏感性。

**退出证据**：Q5 可生成第105-112周逐周预测，并把预测证据传入同一 MILP，而不是把预测值写进提示词。

### Phase 4：真实题复测与回归

- [x] RED-GREEN-REFACTOR：未来预测的敏感性变化超过声明阈值时，独立审核必须失败，不能只验证报告布尔值的一致性。
- [x] RED-GREEN-REFACTOR：Core 结构校验允许运行时参数参与目标系数，复用运行时占位验证；实际执行仍严格物化和检查真实参数。
- [x] RED-GREEN-REFACTOR：验证报告显式登记子题覆盖状态；即使已执行子集三层均通过，只要仍有阻断子题，整题报告必须保持 partial。
- [x] RED：ModelIR 必须结构化登记建模假设、题面依据、影响范围、替代情景和敏感性检查；假设不得伪装为 locked fact，缺失替代情景或敏感性检查时 Reviewer 不得批准。
- [x] GREEN：贯通 `ProblemSpec.uncertainties -> ModelIR assumptions -> ModelReview -> ClaimRecord.assumptions -> guidance.md`，允许在题意歧义但能力充分时给出明确标注的条件解，而不是误报算子能力不足。
- [x] REFACTOR：生成 Draft 与 canonical ModelIR 使用同一假设语义，删除下游凭空拼接假设的路径，并确保旧 fixture 通过显式空假设迁移而非兼容分支。
- [x] RED：索引生成合同必须拒绝大索引集逐值展开的目标项/约束和超出约束族预算的草稿，避免 104 期模型在生成阶段超时。
- [x] GREEN：Core/Constraints 提示、Schema 预算和语义校验统一要求 `for_each + binder` 紧凑表达，仅允许少量显式边界项。
- [x] REFACTOR：复用同一紧凑性校验器并保持 canonical ModelIR 的通用上限不变，删除生成层的重复判断。
- [x] RED：Core 的索引维度/selector 错误必须在请求 Constraints 前失败；Draft 可省略字段为 `null` 时必须可规范化，但 canonical 合同仍拒绝 `null`。
- [x] GREEN：实现 Draft 专用 selector/term/objective/constraint DTO 与单向 `promote()`，Core 和 Constraints 分别执行本阶段语义验证。
- [x] REFACTOR：删除生成层对 canonical 子节点的直接复用和跨阶段错误归因，保持编译器只接收 strict canonical ModelIR。
- [x] RED：selector Draft 的 JSON Schema 必须显式区分 binder 分支与 literal value 分支，不能只依赖响应后的 validator。
- [x] GREEN：以两个 Draft selector 类型组成 union，分别要求 binder 或 value，并保持单向 promote 到 canonical `IndexSelector`。
- [x] RED：Constraints 请求必须包含由已验证 Core 派生的紧凑符号目录，且不得包含 `dense_values`、目标项或完整 Core；生成的未知参数/变量仍必须确定性拒绝。
- [x] GREEN：实现不可变 `ConstraintGenerationContext`，登记索引值、参数 ID/维度与变量 ID/维度/类型，并整体替换 Constraints 的完整 Core 输入。
- [x] REFACTOR：完整 Core 只在系统侧参与 promote、展开与语义验证；统一符号目录构造和诊断，验证请求体缩减、合同回归与真实 Q2。
- [x] REFACTOR：Core/Constraints 共用同一 selector union，删除运行时 XOR validator，验证 Schema、合同回归与真实 Q2。
- [x] RED：同一内容请求首次发生项目请求超时时必须原样重试一次；任意业务 RuntimeError 与连续两次超时仍立即失败并留下诊断。
- [x] GREEN：为 StructuredContractRequester 增加专用超时异常和每内容尝试一次的传输重试，不增加两次完整 JSON 生成上限。
- [x] REFACTOR：诊断区分 content attempt 与 transport attempt，并保持失败、恢复和总超时测试确定性。
- [x] RED：Modeler 与 Reviser 的系统提示必须包含同一份架构合同；Reviser 不得遗漏 indexed route 使用空 operators、假设来源和单子问题覆盖规则。
- [x] GREEN：提取唯一共享架构合同，初建与修订仅组合各自职责后缀，删除两套重复且漂移的规则正文。
- [x] REFACTOR：共享合同由单一构造边界注入并保持修订 payload 不变；运行合同回归、真实 Q2 修订和复审。
- [x] RED：架构层声明的 decision/state/derived 变量若未映射到 indexed Core，或 Core 变量未参与目标/约束，必须在进入 Reviewer 前确定性失败。
- [x] GREEN：实现通用架构/Core 变量覆盖与使用校验，不引入题名、变量名、最低约束数或赛题专用规则。
- [x] REFACTOR：显式迁移通用 fixture，统一诊断并验证完整 Q2 能通过、不完整 v9 Q2 会被拒绝。
- [x] RED：首次 review 的结构化整改要求必须传入 Reviser 与复审；缺少对应模型路径、要求未接受或路径不存在时，复审不得 approved。
- [x] GREEN：实现带稳定 requirement ID、处置状态和 ModelIR 路径证据的修订谱系，并由确定性校验约束 approved。
- [x] REFACTOR：Pipeline 保留首次 review 工件，统一初审/复审输入边界，并在 Guidance 与 ZIP 中公开修订闭环证据。
- [x] RED：ProblemSpec 子问题仅含 title/brief_description 且 task_summary 超长时，Modeling 失败仍必须生成合法 blocked ModelIR、三层报告和 Guidance。
- [x] GREEN：用有界、按字段优先级解析的子问题目标构造器替换 task_summary 兜底，不截断错误原因或恢复动作。
- [x] REFACTOR：统一失败目标诊断并回归完整/简略 ProblemSpec，随后恢复真实题 Modeling 复测。
- [x] RED：Modeler/Reviser 共享合同必须禁止把题面锁定事实包装成假设，并要求每条假设都有非空替代情景、敏感性检查；题意歧义必须精确引用 `problem_spec.json#uncertainties/<uncertainty_id>`。
- [x] GREEN：在唯一共享架构合同中明确假设生成与来源引用规则，使初建和修订获得完全相同的约束。
- [x] REFACTOR：回归空假设、标准建模约定和题意歧义三类路径，随后恢复真实题 Modeling 复测。
- [x] RED：ProblemSpec 事实与歧义必须具有稳定 ID、精确 InputCatalog 来源、适用子问题和规范化语义键/值；非法来源、未知作用域及同键同作用域冲突必须确定性失败。
- [x] RED：可解 ModelIR 必须逐项绑定适用 locked fact 到存在的模型 JSON 路径；遗漏、重复、未知事实或不存在路径不得进入 Reviewer。
- [x] GREEN：以结构化 `LockedFactSpec`/`UncertaintySpec` 和 `FactBindingSpec` 整体替换字符串列表，并贯通 Decomposer、Modeler、Reviser 与确定性语义校验。
- [x] REFACTOR：显式迁移 fixtures、失败降级、EvidenceProfile 一致性与 Guidance/ZIP 公开工件，删除旧字符串事实路径。
- [x] RED：Guidance 工作流必须为 decomposition、architecture、indexed core、indexed constraints、review 暴露独立超时预算；标准模式 Constraints 不得继续继承 120 秒共享值，诊断必须记录实际预算。
- [x] GREEN：用不可变分阶段预算整体替换 `WorkflowBudget.llm_timeout`，在各结构化请求边界注入对应预算。
- [x] REFACTOR：删除 Guidance 的共享超时标量并回归 fast/standard/strict 三种模式、传输重试和超时诊断。
- [x] RED-GREEN-REFACTOR：真实题 Modeling 长错误必须完整归档到 `modeling_failure.json`，正式 blocked 合同只保留有界摘要与诊断引用，失败降级不得再次触发字段长度校验。
- [x] RED：任一子问题合同生成失败时，其他子问题仍须继续生成；失败题必须形成合法 `BlockedModelIR` 并引用独立完整诊断。
- [x] GREEN：提取单子问题生成边界，以成功或阻断结果聚合完整 ModelIR；请求 payload 显式列出 `required_fact_ids`。
- [x] REFACTOR：全局 Modeling 兜底只处理切片前灾难性失败，统一单题诊断命名、摘要和全 blocked/partial 回归。
- [x] RED：索引参数必须支持绑定上游算子工件字段，合法预测→MILP 必须形成有序 DAG。
- [x] GREEN：以静态证据/运行时工件参数来源 union 替换纯稠密参数边界，编译器为 MILP 生成参数绑定输入与模型模板。
- [x] RED：编译阶段必须拒绝未知上游工件与组合执行图依赖环。
- [x] GREEN：用统一工件生产者目录验证运行时参数来源，并把参数依赖纳入既有拓扑诊断。
- [x] RED：MILP 运行时必须从上游工件精确物化参数、复核字段路径、索引长度与有限数值，再展开矩阵求解；缺值、NaN 和长度错误不得进入 SciPy。
- [x] GREEN：扩展通用 MILP 运行时物化器与参数/结果溯源，不新增赛题专用算子。
- [x] RED：Modeler 必须能生成同时含预测节点和 indexed model 的组合草稿；Draft 接受 RFC 6901 URI fragment 并单向规范化，canonical 仍只接受 `/` 指针。
- [x] GREEN：整体替换互斥 `execution_route` 生成合同，Core/Constraints 继承运行时参数符号目录，Reviewer 能审核跨节点来源。
- [x] REFACTOR：统一静态与动态索引展开、编译诊断、执行依赖和验证溯源，迁移 fixtures 并删除旧互斥路由规则。
- [x] RED：Decomposer Draft 中混入 Unicode 的 fact/uncertainty/semantic 标识符必须可单向提升为稳定 ASCII canonical ID；所有子问题作用域引用必须同步，规范化碰撞必须确定性拒绝。
- [x] GREEN：实现 `ProblemSpecGenerationDraft -> ProblemSpec` 单向提升并整体替换 Decomposer 对 strict canonical 的直接复用，不放宽 canonical 正则。
- [x] REFACTOR：统一子问题、事实、歧义与 semantic key 的稳定标识符生成和碰撞诊断，迁移合同 fixtures 并删除生成层直接写 canonical 的路径。
- [x] RED：纯 indexed canonical 模型不得要求重复的高层 `variables`；operator graph 与 forecast→MILP 组合模型必须通过类型化组件编译，旧平铺字段必须被 canonical Schema 拒绝。
- [x] GREEN：以 `OperatorGraphExecutionComponent` / `IndexedLinearExecutionComponent` 整体替换 canonical `variables/operators/indexed_linear_model` 平铺字段，并迁移 compiler、execution、semantics 与 review。
- [x] RED：架构 Draft 不得再提交预猜的 `fact_bindings.model_paths`；Core 参数、约束和算子节点必须登记来源 fact ID，最终组装必须生成真实路径并拒绝遗漏、未知、重复和不存在路径。
- [x] GREEN：贯通节点级事实来源与最终 `FactBindingSpec` 组装，分阶段请求只接收本阶段未覆盖事实，Reviewer 只审核 canonical 真实路径。
- [x] REFACTOR：迁移全部 ModelIR fixtures 与测试，删除平铺执行字段、`FactBindingDraft` 和架构阶段事实路径生成规则，统一组件遍历与事实路径诊断。
- [x] RED-GREEN：真实题多事实子问题不得在建模压缩阶段截断 `locked_facts`/`uncertainties`；混合执行图候选可等待系统注入 MILP 节点，纯 operator graph 仍必须引用实际节点。
- [x] REFACTOR：结构化事实和歧义完整进入子题作用域切片，候选节点引用校验按类型化执行组件语义统一，不增加题目专用路径。
- [x] RED-GREEN：非零索引偏移的 Constraints 生成合同必须使用排除越界端点的辅助索引集，并以独立约束族表达首末期边界；确定性越界校验保持严格。
- [x] RED：混合 ModelIR 中某题 blocked 时，另一题的 approved 决策必须形成显式可执行集合并实际产生数值结果；注册表同时保留已验证结果与阻断结果，最终状态为 `partial`。
- [x] GREEN：以逐子题审核记录整体替换 `ModelIRReview`/`ApprovedModelIR` 全局门控，确定性派生整体 `approved`、`partial`、`revision_required` 或 `blocked`，拒绝遗漏、重复、未知或非法执行 ID。
- [x] GREEN：Pipeline、Calculation、Verification 与 Guidance 只执行审核通过的 solvable 子题，并将未执行子题的审核/建模原因合并进同一三层验证和结果注册表。
- [x] REFACTOR：删除 `CalculationStage requires an approved Model IR` 等全局短路，迁移审核 fixtures 与前端结果协议，确保 `partial` 不显示为整题完成。
- [x] 重跑 2022 五一 A 题，审查 ProblemSpec、InputCatalog、EvidenceProfile、Model IR、执行计划、数值注册表、验证报告和 Guidance。
- [x] RED：以强类型 `SubproblemSpec` 替换自由 `dict`，要求每题显式登记 `required_fact_ids`、`required_uncertainty_ids` 与前置子题依赖；未知、遗漏、跨作用域或循环引用必须在 Modeling 前失败。
- [x] GREEN：贯通 `ProblemSpecGenerationDraft -> ProblemSpec -> Modeler` 的逐题事实/歧义/依赖投影，使数据来源、题面数值条件和上游结果具有唯一结构化入口。
- [ ] REFACTOR：迁移 Decomposer/Modeler、fixtures 与失败合同，删除从 description 猜测事实或把 `data_source.id` 当 `fact_id` 的隐式路径，随后再次复跑 2022 五一 A 题。
- [ ] 人工核对 Q1-Q4 状态转移、整数损毁、训练容量、阶梯成本及 Q5 两方案口径，不以求解器 success 代替数学审核。
- [x] 运行完整后端测试、前端协议测试、生产构建、UTF-8、编译、旧链扫描、ZIP 和 `git diff --check`。

**2026-10-31 提交前验证**：三处审核回归新增的 5 项测试先确认 RED，再以最小实现转为 GREEN；Core 复用既有运行时占位校验，没有扩展执行兼容分支。完整后端 `pytest tests -q -p no:cacheprovider` 为 408 passed，包含真实附件 ZIP 与旧链架构守卫；前端 `node --experimental-strip-types --test tests/*.test.ts` 为 8 passed，`npm run build` 通过。57 个待提交文件通过严格 UTF-8 解码，48 个 Python 文件通过语法/编译检查，3 个 JSON 文件解析通过，BOM 未意外变化，`git diff --check` 通过。Windows 受限环境中的临时目录和 asyncio 本地连接影响测试，完整后端验证改在正常权限下运行。本次不宣称完成上方仍未勾选的真实题复跑和人工数学审核。

**退出证据**：完整输入不再被误报缺失；支持部分给出可复算数值，不支持部分准确写明缺少的算子或假设；前端不会把 partial/blocked 显示为整题完成。

## 停止条件

- SciPy `milp` 不可用或需要新增重型/商业依赖时，停止并单独提案。
- 索引 IR 只能依靠赛题专用字段表达时，停止并重新设计通用语义。
- 真实题提升只能靠题名、关键词或硬编码答案时，本路线判定失败。

## 最终完成判据

1. blocked、failed、partial、verified 的所有路径均满足同一三层验证合同。
2. DOCX/PDF 题面内嵌表格和外部数据文件进入同一证据目录。
3. Q1-Q4 使用通用索引 MILP，Q5 使用通用未来期预测与同一优化链。
4. 每个正式数值都能追踪到原始单元格、IR 节点、算子版本和独立验证。
5. 仓库没有题目专用生产模板、任意代码执行或旧新双轨。
6. 全量回归、真实附件、ZIP、前端构建和人工数学审查全部通过。
