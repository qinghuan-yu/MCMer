# MCMer 长期优化计划：从多 Agent 临场协作到协议化 Skill 工作流

## 1. 背景与方向

当前系统已经开始沉淀 `result_registry`、`figure_plan`、`writer_context`、`figure_gate`、`renderer` 等模块，但整体链路仍有明显的历史负担：`workflow.py` 仍承担过多全局编排、补洞、图表推断和失败恢复逻辑；部分图表仍可能从题目关键词或 Writer 语义中被动推断；结果、图表、导出校验分散在多个模块中，字段规则和错误语义不够统一。

长期目标不是继续围绕单个报错补洞，而是把项目升级为：

```text
少量 agent 做判断与写作
大量 deterministic skill 做协议、校验、渲染、导出
所有阶段通过结构化 artifact 通信
```

核心原则：

```text
不要让 agent 猜图。
不要让 Writer 扫目录找图。
不要让 solve_spec 承担全局状态。
不要让图表来自题目关键词。
图表必须来自 verified result 的 visualization_contract。
```

最终工作流应稳定收敛为：

```text
ProblemInput
  -> ProblemContract
  -> SolveSpec
  -> DataProfile
  -> VerifiedResultRegistry
  -> VisualizationContract
  -> FigurePlan
  -> FigureArtifactRegistry
  -> FigureBundle
  -> WriterContext
  -> Finalizer
  -> VerifiedOutputBundle
```

## 2. 目标架构

### 2.1 少 Agent 设计

系统长期只保留 4 个主 agent。Agent 负责需要判断、解释、取舍和写作的部分，不负责确定性协议执行。

| Agent | 职责 | 禁止事项 | 核心输出 |
| --- | --- | --- | --- |
| `PlannerAgent` | 读题、拆题、生成 `ProblemContract` 和 `SolveSpec` | 不写代码、不画图、不写论文 | `problem_contract.json`、`solve_spec.json` |
| `SolverAgent` | 计算、建模、产出结构化结果 | 不写漂亮分析、不直接决定正文图片 | `verified_results`、`data_artifacts`、`result_type`、`columns`、`visualization_contract` |
| `ReviewAgent` | 审核结果可信性、完整性、可视化就绪状态 | 不修图、不写论文 | `verified / blocked / unverified` 审核结论 |
| `WriterAgent` | 根据 `WriterContext` 写论文 | 不能新增结果、不能发明图片含义、不能自改图注类型 | Markdown / paper draft |

`Finalizer`、`Renderer`、`FigureGate`、`ResultRegistry` 都不应继续作为 agent 存在，而应下沉为 deterministic skill/tool。

### 2.2 Skill 设计

| Skill | 职责 | 输入 | 输出 |
| --- | --- | --- | --- |
| `ProblemContractSkill` | 判断语言、论文类型、数学建模任务属性、默认图表要求 | `ProblemInput` | `problem_contract.json` |
| `SolveSpecSkill` | 拆分子问题、输入数据、预期结果类型 | `ProblemContract` | `solve_spec.json` |
| `DataProfileSkill` | 读取 Excel/CSV 的 sheet、列名、数值列、分类列、缺失率、样本量 | 上传数据文件 | `data_profile.json` |
| `VerifiedResultSkill` | 规范化 solver 结果并写入统一注册表 | solver 原始结果、数据产物 | `result_registry.json` |
| `VisualizationPlannerSkill` | 只读取 verified results，根据 `result_type` 生成图表计划 | `result_registry.json` | `figure_plan.json` |
| `RendererSkill` | 只接受 `visualization_contract`，确定性生成图 | `figure_plan.json`、source data | `figure_artifacts.json` |
| `FigureGateSkill` | 校验图片语言、绑定结果、source data、caption、view type、正文可用性 | `figure_artifacts.json`、`result_registry.json` | `figure_bundle.json` |
| `WriterContextSkill` | 将 verified results、tables、figures 组装成 Writer 唯一输入 | registry、tables、figure bundle | `writer_context.json` |
| `FinalizerSkill` | 校验 Markdown、DOCX、media、图文一致性、DSML 泄漏、白名单 | draft、figure bundle、writer context | `verified_output_bundle.json` |

### 2.3 固定 Artifact 协议

每一层只读上一层 artifact，不跨层乱读目录或猜上下文。建议冻结以下文件名：

```text
problem_contract.json
solve_spec.json
data_profile.json
result_registry.json
figure_plan.json
figure_artifacts.json
figure_bundle.json
writer_context.json
quality_gate_report.json
verified_output_bundle.json
```

`solve_spec` 不再承载全局图表状态，逐步移除：

```text
figure_requests
expected_figures
blocked_count
required_feasible_count
```

这些字段应迁入 `figure_plan`、`figure_bundle` 或 `quality_gate_report`。

## 3. 结果驱动图表

长期最关键的改造是图表链路从题目驱动改为结果驱动。

禁止主路径：

```text
题目关键词 -> FigurePlan
```

目标主路径：

```text
verified_results -> visualization_contract -> FigurePlan
```

标准映射先冻结为：

| `result_type` | 默认 `view_type` |
| --- | --- |
| `regression_model` | `scatter_with_fit`、`residual_plot`、`coefficient_plot` |
| `classification_model` | `confusion_matrix`、`feature_importance`、`roc_curve` |
| `optimization_result` | `convergence_curve`、`sensitivity_curve`、`decision_variable_bar` |
| `grouped_decision_result` | `group_boxplot`、`group_bar`、`risk_curve` |
| `descriptive_statistics` | `histogram`、`boxplot`、`correlation_heatmap` |

数学建模论文默认要求：

```text
每个一级问题至少 1 张图
每个核心 verified result 至少 1 个 view
没有图必须有 visualization_exemption
```

### 3.1 VerifiedResult 最小字段

每个核心结果必须被规范化为：

```json
{
  "id": "result_q1_model",
  "section": "P1",
  "result_type": "regression_model",
  "verified": true,
  "data_artifacts": ["data/q1_fit.csv"],
  "columns": {
    "x": "dose",
    "y": "response",
    "prediction": "fitted_value",
    "residual": "residual"
  },
  "claim_text": "响应变量与剂量之间存在显著非线性关系。",
  "visualization_contract": {
    "required": true,
    "eligible_views": ["scatter_with_fit", "residual_plot"],
    "source_data": "data/q1_fit.csv",
    "language": "zh-CN"
  }
}
```

## 4. 项目瘦身计划

### 4.1 第一刀：瘦 `workflow.py`

当前 `backend/app/core/workflow.py` 约 3000+ 行，长期目标降到 600-900 行，只做编排，不承载协议、渲染、校验和导出细节。

目标拆分：

```text
backend/app/core/workflow.py
backend/app/core/stages/planning_stage.py
backend/app/core/stages/solver_stage.py
backend/app/core/stages/review_stage.py
backend/app/core/stages/figure_stage.py
backend/app/core/stages/writer_stage.py
backend/app/core/stages/finalizer_stage.py
backend/app/core/stages/failure_stage.py
```

拆分原则：

- `workflow.py` 只串联阶段、传递 artifact 路径、收集阶段状态。
- 每个 stage 只读显式输入 artifact，并产出一个或多个命名 artifact。
- stage 内部调用 skill/tool，不直接写大段 prompt 或做私有协议补丁。
- failure stage 统一处理 blocked、fallback、partial delivery，不散落在主流程中。

### 4.2 第二刀：清 legacy wrapper

逐步迁移或删除：

```text
_language_verified_generated_images
_ensure_figure_requests_in_solve_spec
_infer_figure_requirement_flags
_solve_spec_expects_figures
_render_required_requests_deterministically
```

迁移顺序：

1. 先把现有测试迁到 `FigureStage`、`FigurePlanBuilder`、`ArtifactRegistry`。
2. 用 `FigureGateSkill` 接管语言、存在性、caption、result binding 校验。
3. 用 `VisualizationPlannerSkill` 接管图表需求推断。
4. 确认端到端 fixture 通过后，再删除 `workflow.py` 私有函数。

### 4.3 第三刀：合并重复校验

当前校验分布在：

```text
coder_agent.py
local_interpreter.py
figure_artifacts.py
registry.py
exporters.py
```

长期统一为：

```text
figure_artifact_schema.py
result_schema.py
visualization_schema.py
```

所有模块共用同一套字段、错误码、必填规则。业务模块只消费 schema 校验结果，不再各自手写相似判断。

### 4.4 第四刀：清 prompt 冗余

把长 prompt 中重复描述的协议改成引用结构化 JSON schema。每个 Agent 只看到自己需要的约束：

- Planner 只看 `ProblemContract`、`SolveSpec` schema。
- Solver 只看 `VerifiedResult`、`DataArtifact`、`VisualizationContract` schema。
- Reviewer 只看 registry 审核规则。
- Writer 只看 `WriterContext` 和 `FigureBundle` 的可引用内容。

## 5. 质量门禁升级

最终 gate 分为四层：

### 5.1 ResultGate

- verified result 是否足够覆盖一级问题。
- 核心结论是否有数据支撑。
- blocked result 是否有明确原因和恢复建议。
- `claim_text` 是否能追溯到数据 artifact。

### 5.2 VisualizationGate

- 每个核心结果是否有图或 `visualization_exemption`。
- 每张图是否绑定 `result_id`。
- 图是否使用 `visualization_contract.source_data`。
- 图是否使用 contract 中声明的数据列。

### 5.3 FigureSemanticGate

- `view_type` 是否匹配 caption。
- caption 是否匹配正文解释。
- 图是否属于对应章节。
- 图片语言是否匹配 `problem_contract.language`。

### 5.4 DocumentGate

- Markdown 图片引用是否存在。
- DOCX media 是否包含应入正文图片。
- 无 DSML / `tool_call` / debug 内容泄漏。
- 无 debug/workflow 图混入正文。
- 正文只能引用 `FigureBundle.available_figures`。

## 6. 测试体系

建立真实事故回归集，每个 fixture 验证最终 artifact 链路，而不是只测函数。

建议 fixture：

```text
中文题 + 中文图 + DOCX 图片
英文题 + 英文图
NIPT 数据建模题
回归模型题
分类模型题
优化模型题
只有表格无高级结果题
solver 被熔断题
debug 图不能入正文
Writer 误写图注类型
DOCX media 缺图
```

每个 fixture 至少验证：

- `result_registry.json` 是否有 verified / blocked 汇总。
- 核心 verified result 是否有 `visualization_contract`。
- `figure_plan.json` 是否只来自 verified result。
- `figure_artifacts.json` 是否绑定 source data 和 result id。
- `figure_bundle.json` 是否只暴露可入正文图片。
- `writer_context.json` 是否是 Writer 唯一输入。
- Markdown / DOCX 是否引用同一批 canonical figures。
- `verified_output_bundle.json` 是否给出最终通过或阻断原因。

## 7. 分阶段路线

### 阶段一：协议冻结

定义并冻结：

```text
VerifiedResult
VisualizationContract
FigureArtifact
WriterContext
```

目标是字段稳定，不再到处临时加键。现有 `backend/app/artifacts/contracts.py`、`visualization_protocol.py`、`writer_context.py` 可以作为迁移起点。

### 阶段二：结果驱动可视化

让 `VisualizationPlanner` 从 `result_registry.json` 生成 `figure_plan.json`。题目关键词逻辑降级为 fallback，只在没有 verified result 且需要解释性图示时使用，并必须在 `quality_gate_report.json` 中标记来源。

### 阶段三：Renderer skill 化

每个 `view_type` 对应一个 deterministic renderer。Renderer 不猜列，只读 contract；缺列、缺数据、类型不匹配时输出 blocked artifact，而不是生成看似合理的图。

### 阶段四：Writer 受控

Writer 只能读取 `writer_context.json`，只能引用：

```text
FigureBundle.available_figures
canonical_caption
verified claim_text
approved tables
```

Writer 不允许扫描目录、不允许新增图、不允许修改 `view_type` 和图注语义。

### 阶段五：项目瘦身

迁移测试，删除 `workflow.py` legacy wrapper，拆 stages，合并重复 schema。此阶段目标是降低维护成本，而不是增加新功能。

### 阶段六：真实回归

用固定题集跑端到端，建立通过标准：不是论文好看，而是 artifact 链路正确、失败可解释、导出一致。

## 8. 最终运行形态

理想状态下，系统运行应简化为：

```text
PlannerAgent 生成 solve_spec
SolverAgent 产出 verified results + data artifacts
ReviewAgent 验证 result registry
VisualizationPlannerSkill 生成 figure plan
RendererSkill 生成 figures
FigureGateSkill 生成 figure bundle
WriterAgent 写论文
FinalizerSkill 导出并验证
```

Agent 数量少，skill 细，协议强，失败可解释。长期稳定方案不是让 agent 更聪明，而是让 agent 的自由度被强协议约束住。

## 9. 近期落地清单

1. 新增 schema 模块并冻结字段：`result_schema.py`、`visualization_schema.py`、`figure_artifact_schema.py`。
2. 将 `VisualizationPlannerSkill` 主输入改为 `result_registry.json`。
3. 将 `figure_requests` 从 `solve_spec` 主路径剥离，迁入 `figure_plan`。
4. 将 `WriterContextSkill` 改为 Writer 的唯一入口。
5. 把 `test_figure_artifact_protocol.py` 中依赖 `workflow.py` 私有函数的用例迁到 skill/stage 层。
6. 为中文图、DOCX media、debug 图隔离建立端到端 fixture。
7. 拆出 `planning_stage.py`、`solver_stage.py`、`review_stage.py`、`figure_stage.py` 和 `failure_stage.py`。
8. 将 legacy wrapper 标记为 deprecated，并在回归集通过后删除。
