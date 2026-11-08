# Guider 长线计划：数值可信与可维护模块边界

本文件是唯一长期执行计划；执行规则以 AGENTS.md 为准。

## 目标与授权

GO：用户于 2026-11-01 明确要求完成优化、更新并拆分超长文件。本轮先修复已复现的验收缺陷，再按职责迁移模块，保持 API、数学算子与润色行为。保留已有未提交更新，不自动提交或推送。

证据：后端 411 项回归通过，但独立实验复现成本错序/错列仍通过、artifact: 静态来源绕过核验、失败子题图表污染成功子题验证。共享函数由 app/polish/workflow.py 实际调用，禁止当作死代码删除。

## 已锁定不变量

1. InputCatalog/EvidenceProfile 是题面与附件唯一证据入口，不得将已登记来源误报缺失。
2. ProblemSpec 使用强类型子题、事实、歧义与依赖；Draft 仅单向提升至 canonical，拒绝未知符号、跨作用域引用和依赖环。
3. ModelIR 使用类型化执行组件，支持预测工件到 MILP 的运行时物化，禁止任意生成代码或题名路由。
4. 建模、审核和执行按子题保留可解结果；三层验证不能把子集通过冒充整题完成。
5. 子题结构化字段与列表保真传递；共用生成合同、分阶段预算、事实路径由系统组装。
6. 静态参数按确切文档、列/单元格和索引标签核对；拒绝错序、错列、未知来源与伪造 artifact 前缀，运行时占位只用于强类型 runtime_source。
7. 运行失败沿全部跳过节点传播；失败子题的参数、结果和图表均不进入独立成功子题的公开注册表。
8. 注册器、领域实现、生成合同、阶段验证、呈现和共享写作支持分别拥有实现；调用方直接导入实际 owner，无旧入口兼容门面或循环依赖。

## 核心流与模块归属

```text
题面/附件 -> 证据目录 -> ProblemSpec -> Modeling -> Review
  -> 编译/执行 -> 结果与图表注册表 -> 独立验证 -> Guidance/ZIP
                    ^
         算子目录负责注册，领域模块拥有算法和输出校验
API -> core 工作流路由 -> guidance / polish
                           -> 明确归属的文本、导出、写作和图表模块
```

- 数值证据解析由独立解析器拥有，索引展开器只负责索引与矩阵；删除全表数字集合匹配、模糊文档匹配和字符串前缀豁免。
- canonical 模型与 Draft 分离；生成、审核、约束校验与上下文处理分离；阶段入口与验证/打包分离。
- 算子实现和模型充分性验证按数学领域归属；注册表保留实际编排职责。
- core/workflow.py 保留路由和任务结果边界，polish 共用写作/图表支持迁移到明确归属模块。
- common_utils 按文本提取、结果登记、Markdown/文档导出和审计拆分；其他超长图表/文档/编码模块先验证函数依赖再拆分。

## 取舍与迁移规则

采用职责迁移而非仅缩短文件：证据解析不并入编译器；共享写作不由 Guidance 私有模块拥有。不逐函数拆文件，不建立泛用 helpers 或透传门面。直接迁移所有仓库内调用方、测试和导入。防误用由错序、错列、伪来源、失败图表和跨工作流回归约束；剩余数学充分性通过真实题和人工审核。

## 当前步骤

CURRENT：D2 真实题数学审核；当前五题均在参数合同阶段阻断，须先解决 D1 复测暴露的生成与事实作用域问题并取得可执行模型、数值结果。

## 活动任务

本轮 A1/A2/B1/B2/B3/C1 已完成并收敛至上述不变量，不保留重复历史勾选日志。

### D：保留的真实题数学验收

- [x] D1 复跑 2022 五一 A 题并完成产物审查（2026-11-08，deepseek-flash）：标准配置拆题截断；strict 超时预算加 32768 输出上限后五题均因参数证据合同失败而 blocked。执行计划与数值未生成；复测完成不代表数学验收通过。详见 `docs/reviews/2026-11-08-deepseek-flash-retest.md`。
- [ ] D2 人工核对 Q1–Q4 状态转移、整数损毁、训练容量、阶梯成本及 Q5 两方案口径，不以测试绿色或求解器 success 代替数学审核。

## 2026-11-08 真实题复测证据

- 用户授权 DeepSeek API，并指定 `deepseek-flash`。原始材料沿用基线任务 `20260715-135019-066fc4b9`；所有 20 次生成响应均为该模型，持久化模型配置未改变。
- 标准运行 `20261108-134516-d1-deepseek-flash`：2 次响应均触及 8192 token 上限并截断；预算对照运行 `20261108-134743-d1-deepseek-flash`：18 次响应正常结束，完成逐题生成尝试、审核与阻断 Guidance，三层状态为 `not_assessed / blocked / blocked`，verified 数值为 0。
- 实测 Q1–Q3 参数阶段的 `locked_facts` 为空；离线对照中，修正相同参数的来源引用和循环映射后，仅恢复 Q1 本题事实可见性即可让核心合同校验从拒绝变为通过。该实验未修复生产代码，也未验证完整数学模型。
- Q4/Q5 仍有来源引用合同问题；事实抽取将“四舍五入”登记为可选 half_even 歧义。未执行约束生成、MILP 和数值复核，D2 保持未完成。后续模块边界调整须先提出经验证的替换方案并获明确授权。
- 原始材料、API 请求/响应、合同诊断、三层报告、Guidance 和部分工作区 ZIP 已保留。后端与前端业务代码未修改。

## 模块导航

| 职责 | 实现归属 |
| --- | --- |
| 算子注册与数学实现 | guidance/operator_catalog.py + operator_runtimes/ |
| 模型充分性注册与领域校验 | guidance/model_validators.py + model_validation/ |
| Canonical、生成草稿、审核合同 | guidance/model_ir.py、model_ir_drafts.py、model_review_contracts.py |
| 拆题、建模、审核与生成校验 | guidance/decomposer.py、modeler.py、reviewer.py、model_generation.py、generation_validation.py |
| 阶段编排、结果验证、Guidance 打包 | guidance/stages.py、verification_stage.py、guidance_stage.py |
| Guidance 主渲染与段落/诊断 | guidance/renderer.py + guidance_rendering/ |
| 路由与共享写作/图表支持 | core/workflow.py、workflow_runtime.py、writing_support.py、chart_support.py、chart_language.py |
| 图表请求、数据、门禁和渲染 | core/figure_requests.py、figure_data.py、figure_gate.py、figure_rendering.py |
| 文档、图表与审计工件 | artifacts/document_*.py、*_renderers.py、guidance_audit*.py、figure_*.py |
| 文本、Markdown、文档导出与结果登记 | utils/problem_text.py、markdown.py、document_export.py、result_registry.py、result_legacy.py、result_audit.py、paper_audit.py、file_io.py |

## 2026-11-01 工程验收证据

- RED：静态错序/错列/伪来源等首批 6 项失败，图表隔离与跳过节点失败传播分别复现失败；另补索引标签重排、单元格坐标和 DOCX 文本数字匹配的失败测试后转 GREEN。
- 职责合同先 RED，再原样移动实现并直接迁移导入。算子元数据、审核族检查集及被移动函数/方法的 AST 保持一致；润色预算解析遗漏已用失败合同复现并修复。
- 正常权限运行完整后端 `pytest tests -q -p no:cacheprovider`：515 passed（20.89 秒），包含真实附件、ZIP、部分执行和跨工作流回归。
- 前端协议 `node --experimental-strip-types --test tests/*.test.ts`：8 passed；`npm run build` 通过。
- `ruff check --select F821 backend/app backend/tests`、128 个变更 Python 文件语法/编译与严格 UTF-8/BOM 检查、git diff --check 全部通过；84 个新文件无尾空格/额外末尾空行。
- 173 个后端模块的顶层导入图无循环；原 16 个超过 700 行的实现文件均已按职责收敛，当前最长实现文件 666 行。删除旧 agents/common_utils/figure_stage/renderers 门面及 20 个纯转发包装函数，保留活跃逻辑。
- 本地运行缓存已由 .gitignore 排除。本轮未进行新一轮真实题生成或人工数学验收，也未自动提交/推送。

## 执行约束

每次代码修改前完整阅读本文件，确认当前未完成步骤；行为修复先运行最小失败测试，重构由行为回归和模块边界合同约束。子任务完成立即勾选并移动唯一 CURRENT，完成阶段压缩为不变量。修改后报告现状摘要：核心流、危险耦合、本次变化、完成项、下一项。

## 停止条件

需要题目专用字段/模板、重型或商业求解器、任意代码执行，或本计划之外的模块替换时，停止该项并用实验证据说明。真实题外部服务不可用时如实记录；工程优化与数学验收分别报告。
