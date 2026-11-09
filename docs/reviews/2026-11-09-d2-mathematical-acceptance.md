# D2 真实题数学验收记录

本记录是验收证据，不是并行计划。执行状态以 `docs/compute_first_guidance_plan.md` 为准。D2 尚未完成；原始实验、请求与响应保留在 `backend/.local/`，不手修生成产物。

## D2.1：来源证据与验证类别

原始对象为 `.local/20261109-090320-r52-budget-flash/original`。其四个答案引用 `question.table.4/5`，分别对应原件成本表和需求表；旧验证器却要求它们是已执行节点。

- 修复前原样计算回放：`.local/20261109-092129-d2-evidence-replay`。执行和数学审核通过，来源证据失败，数值层 blocked，整体 partial。
- 来源修复后原样回放：`.local/20261109-092841-d2-evidence-replay`。执行、来源证据、数学审核通过；MILP 五项检查通过，错误声明的 optimization 六项仍 not_assessed，整体 partial。此回放发生在类别校验接入前，不代表最终编译器接受旧分类。
- 来源最小 RED：`.local/d21-source-evidence-red-20261109`，5 failed / 5 passed；GREEN 连同恢复、数学审核回归共47 passed / 7.98秒。结果引用可包含实际执行节点或本子题声明且经原件核验的登记来源；未知、跨子题、重复、节点同名、缺失或失效来源仍拒绝。原引用不被改写。
- 执行快照同时绑定已有 InputCatalog/EvidenceProfile；执行后删除或修改登记不能沿用旧证据。验证阶段不重建丢失的登记。
- 类别实验：`.local/d2_validation_category_probe.py`、`.local/d2-validation-categories-20261109/probe_results.json`，16组正反例。`optimization` 的正定二次目标、多起点与界限检查针对二次规划，MILP 不能冒充该方法；MILP 目标、整数、守恒、对偶界和基线篡改均被拒绝。
- 类别 RED：`.local/d21-family-red.xml`，17 failed / 9 passed；GREEN 26 passed / 2.98秒，相关回归118 passed / 5.67秒（`.local/d21-family-regression-final.xml`）。现有 Registry 登记方法与验证族的双向关系，draft/canonical 共用，错配触发合同重生成，不静默删除类别。数值校验器未删改。
- 最终类别规则对未改原始模型准确产生一条 `validation_family_method_mismatch`：`.local/d2-validation-categories-20261109/canonical_rejection.json`。模型 SHA-256 为 `24e1596a9087512a4f9eb5b92957bdc5738712ceaa24105801a22c5b80a915ec`，保持不变。
- 真实DOCX及登记损坏补充回归：`.local/d21-embedded-regression.xml`，20 passed / 4.36秒。全量首次发现共享测试helper的一处调用未迁移，补显式正确类别后，完整后端664 passed / 21.42秒（`.local/d21-full-final-20261109.xml`），含Guidance/Polish/core。公共协议与前端未改，不重复前端构建。D2.1出口完成。
- 独立复核追加发现失败子题来源污染成功分支：`.local/d2-child-provenance-partial-review/review_summary.json`，实际仅independent发布但chain缺失来源使证据层failed；最小正式测试RED为1 failed / 0.63秒。修复将来源核验限定于实际发布结果引用，并与批准模型声明取交集；失败子题仍保留blocked/partial。相关42 passed / 6.94秒（`.local/d21-partial-green.xml`），最终完整后端665 passed / 23.83秒（`.local/d21-isolation-full.xml`），D2.1出口经此复核后确认。

限制：原模型承诺的假设替代情景仍无执行证据。二次规划当前 `sensitivity` 工件描述多起点解差，不能当作参数扰动证据。分类正确和数值通过均不证明鲁棒性或整题数学充分性。

## D2.2：完整流程实测

准备脚本 `.local/run_d2_full_flash.py` 使用真实六阶段 GuidancePipeline 和产品已有的一次审核修订。默认 dry-run；`--live` 才调用 DeepSeek，所有角色固定 `deepseek-flash`，每个实验硬上限24次实际请求，底层每次仅一次尝试，保留每阶段原始快照和失败合同。

原件 dry-run `.local/20261109-092442-617291-d2-full-dry-run` 保留全部24来源块、5项表证据与问题1–5；同族题 dry-run `.local/20261109-092447-532259-d2-full-dry-run` 保留6来源块、2项表证据。`.local/d2-harness-smoke-20261109` 验证调用上限、失败快照和凭据隔离。

### 两轮真实流程：均 blocked

| 输入 | 原始运行目录（backend/下） | 实际请求 | total tokens | 秒 | 最终结果 |
| --- | --- | ---: | ---: | ---: | --- |
| 完整原DOCX | `.local/20261109-093833-218051-d2-full-flash` | 17 | 450671 | 1319.578 | 初次建模合并失败，未进入Reviewer/求解 |
| 同族DOCX | `.local/20261109-093833-820416-d2-full-flash` | 21 | 528718 | 1725.016 | 初审及一次修订后合并失败，未进入复审/求解 |

共38次、979389 tokens，全部使用deepseek-flash，未达到任一实验24次上限。两轮原件与持久配置哈希均不变，凭据扫描通过；没有手修原件、响应或模型。执行时生产代码摘要保存在`.local/d2-source-checkpoint-20261109.json`；后续局部修复未改变这两轮进程已加载的代码或历史结果。

原题Q1–Q4单题合同生成后，Q5a因图组件变量目录为空在提升阶段失败，Q5b沿依赖阻断。随后总ModelIR合并又因假设ID跨子题重复而失败，全体被回退为blocked。`.local/replay_d2_assumption_collision.py`从原始请求提取Q1–Q3 canonical，逐题均通过；Q1+Q2、Q2+Q3原样合并分别失败，Q1+Q3通过。冲突为`A_U001`（problem1/problem2）、`A_U002/A_U003`（problem2/problem3）。证据`.local/d2-assumption-collision-replay-20261109/reproduction.json`包含来源SHA。Q4不在后续dependency_models快照中，此复现不伪称提取了Q4。

原题单题合同复核：Q1五类成本、指导者占用和保养关系正确；Q3的`10L−D≤5、D−10L≤4`正确实现半入，两个状态式能推出保养回流和损毁守恒；Q4阶梯有前档填满约束。以上不构成完整模型或数值验收。其剩余问题包括：拆题将“完全损毁/一次性购买量/有可能需要”扩成不相容的替代解释；Q4架构把空白table-2模板称为Q3结果来源；Q4使用未经证明的M=1000采购上界；Q5没有有效预测或两方案计算。

同族初始模型的回流和双对象训练费正确，初审本轮没有重复“模块永久消失”误判。Reviewer准确发现替代情景只有声明，却又把原文支持的双对象收费称为错误“双重计费”。sp1修订删去指导者费用：训练4人、指导者1人应收费40，修订式仅32；新增`4g−n≤3`排除了“2位指导者各训练1人”的合法安排。sp2修订建立了9组情景并保留原文分支，但部分情景公式重复。最终两个子题的`asm_unc001_training_charging_basis`和`asm_unc002_idle_module_maintenance_frequency`同名，合并失败，未执行。完整独立裁决见`.local/d2-variant-consistency-20261109/live-final-audit-summary.json`及`live-initial/sp1-revision-regressions.json`，不按错误审核意见修改原始产物。

### 实测推动的局部修复与边界决策

- 事实路径：同族一份数学声明对同一事实生成21条互异合法来源路径，内部`FactBindingSpec.model_paths`任意20上限拒绝了它。正式RED为1 failed / 2 passed（`.local/d22-fact-path-red.xml`）；移除该上限，保留非空、唯一与JSON指针规则。GREEN及`.local/reproduce_d2_fact_path_limit.py --expect accepted`原样回放保留全部21条；5个输入文件哈希不变。完整后端667 passed / 22.55秒（`.local/d22-fact-path-full.xml`）。
- 图架构变量：原题第17次原始响应省略variables，draft按默认空数组接受一次，随后canonical要求非空而失败；反馈未能进入请求重试。正式RED为3 failed / 1 passed（`.local/d22-graph-variables-contract-red.xml`）；现有draft validator新增非空规则，graph-only/hybrid适用、indexed-only保持可为空。GREEN 4 passed / 3.28秒（`.local/d22-graph-variables-green.xml`），两个完整stub流程均在请求内重试并保留有效变量。原始响应不改，SHA仍为`2cfba1e5dcfa70936d6fcb6c22a01dda68b0e331235ee3e099872dc12b1bb02a`；不据此声称真实Q5已生成成功。
- 最终完整后端670 passed / 21.40秒（`.local/d22-graph-variables-full.xml`）。所有代码变更遵循实际RED→GREEN，数值算法未改；本轮不再发起新的线上调用，已记录结果仍为blocked。
- 假设身份：生成按子题局部验收，ModelIR合并要求全局唯一，审核又用平铺的假设ID反查子题；此责任冲突已由两轮运行和最小合并反例证实。检查点`e2730c3`保存以上局部修复与blocked证据；用户随后明确批准按子题归属重构，后续实施如下。

### 获批的假设归属替换

canonical假设ID只在所属子题内唯一。`assumption_checks`从ModelIRReview根层直接迁入各SubproblemReviewSpec；覆盖顺序、重复和接受状态均在该子题内判断，删除全局ID到owner反查。SolvableModelIR被审核判为blocked仍需完整假设审核；本身BlockedModelIR的检查必须为空。Reviewer提示、失败投影和正式测试直接迁移；既有修订投影自动只携带目标子题检查。

- RED：`.local/d22-scoped-review-red.xml`，11 failed / 3 passed / 0.58秒；`.local/d22-scope-red-final.xml`，4 failed / 0.38秒。后者明确复现旧平铺审核哈希匹配仍passed、预期应not_assessed，及同题canonical重复原本未拒绝。
- GREEN：`.local/d22-scope-green.xml`，18 passed / 0.43秒；完整后端`.local/d22-scoped-full.xml`，683 passed / 23.20秒。
- 原始模型回放：`.local/replay_d2_assumption_scope.py`与`.local/d2-assumption-scope-green-20261109/reproduction.json`，Q1/Q2/Q3分别、三种两题组合及三题合并均通过；全部字段、重复局部ID和来源SHA保持，旧RED脚本与证据不变。
- 旧审核根字段（空或非空）直接拒绝；即使原哈希仍匹配，也不再获得数学审核通过。历史审核不做猜测式迁移，恢复实验必须重新请求新审核；此前已知漏费修订产物不作为推荐基准。

边界修复不保证Reviewer数学判断正确，D2.2/D2.3仍待有效审核、执行和独立核对。独立diff审查未发现可行动缺陷；Guidance调用方已迁移，core/Polish未消费内部假设审核字段，公共协议不变。

### 归属重构后的恢复实测：合并通过，审核仍失败

`.local/run_d2_recovery_flash.py`在新目录保留原件/catalog/profile/ProblemSpec，只复用首次Decomposer/Modeler有效合同，仍重新执行schema及语义校验。旧Reviewer/Reviser和执行结果不复用；观察器不修改返回值或缓存摘要。预演4项通过，原题旧Q5a缺变量架构在请求内拒绝，同族首次模型与原始模型相同。每轮最多新增12次调用，三角色只用deepseek-flash。

| 输入 | 恢复目录（backend/下） | 重验缓存 | 新请求 | total tokens | 秒 | 终态 |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| 原题 | `.local/20261109-102840-971292-d2-recovery-flash` | 14 | 4 | 241414 | 449.609 | 整题Reviewer输出截断，failed |
| 同族 | `.local/20261109-102842-107221-d2-recovery-flash` | 8 | 9 | 335166 | 891.906 | 终审两次合同无效，failed |

恢复共13次、576580 tokens；连同首轮D2累计51次、1555969 tokens，均为Flash。两轮`run_summary.json`确认原始运行目录、原件和持久配置未变、凭据扫描通过。每次请求/响应、失败诊断和阶段快照保留；没有手修模型或突破预设调用预算。

- 原题Q1–Q4及Q5a均solvable并成功合并；Q5a原缺变量问题在真实请求中恢复。Q5b架构先后在12288/16384额度返回length，仅该题保留blocked；之后整题Reviewer一次响应在32768额度返回length（该次共116567 tokens），触发ContractTruncationError。未接收截断内容，没有批准模型或执行结果，不能声称Q5完成。
- 同族初审正确认可双对象训练费和保养回流，仍要求修订真实的替代情景缺口。两题修订成功合并，均保留正确费用及回流，没有重现首轮漏费；但仍仅period维度，未实现所宣称的替代情景。首次复审漏填revision_checks，语义校验拒绝；重试补齐后status为revision_required却没有required_revisions，两题均被schema拒绝。两次finish_reason均为stop，属于合同自洽性失败，不是截断或求解失败。未产生最终有效审核或数值解。
- 同族独立裁决：`.local/d22-recovery-variant-audit/audit-summary.json`、`revised-model-math-checks.json`、`reviewer-adjudication.json`。指导者费用8高于保养3，当前缺少冗余的guide≤training本身不足以证明最优解会虚构指导；未把此项误判为新的数学错误。替代情景缺失仍须保留。1388基准的实例已和当前DOCX及R3实际catalog逐块核对一致；旧参考JSON内不一致的source_sha256字段未用作来源证明，且本轮无执行解可与1388逐期比较。

本轮证据说明获批假设归属替换已走通真实合并；新的工程阻断为整题审核请求规模与复审合同自洽性。调用链为Pipeline→ModelReviewStage→LLMModelReviewer→单次整题ModelIRReview请求，任何合同失败均无有效整题审核；拟调整范围和审批状态仅记录在唯一长期计划。未增加预算或启动无界重跑。

### 获批的逐子题审核调度替换

用户于2026-11-09回复“批准。开始吧”。实际Reviewer现逐题请求SubproblemReviewSpec，程序按题目顺序汇总；完整ProblemSpec、catalog、profile和完整祖先模型仍可见。初审与复审明确携带检查ID；provider必须提供完整数学检查，失败只能由系统记为not_assessed/blocked且检查内容为空，不能补造审核结论。

- 局部合同RED：`.local/d22-local-review-contract-red.xml`，14 failed/2 passed；GREEN 44 passed/4.51秒。修订路径统一相对本子题，旧`/subproblems/...`拒绝，跨题ID/漏覆盖/未接受项仍不能批准。未评估不能被独立题通过掩盖。
- 请求/恢复RED：`.local/d22-review-isolation-red.xml`，5 failed/3.53秒；GREEN `.local/d22-review-isolation-green.xml`，5 passed/3.52秒。验证单题截断、独立缓存恢复、目标/祖先模型变化、原件字节变化、数学检查缺项和复审要求传递；不提高单次预算，不改变两次尝试上限。
- 执行投影RED：新增`test_guidance_review_projection.py`的4项失败，复现整体revision_required阻断独立题、放行未批准依赖和pipeline混淆修订/执行状态。GREEN相关26 passed/5.51秒。ApprovedModelIR只表达approved/partial/blocked，是否进行一次修订读取原始审核；终审仍需修订时可以执行独立批准分支，失败按完整依赖闭包传播，原审核和绑定保留。
- 修订隔离RED：`test_guidance_revision_isolation.py`的3项失败，旧实现无条件重建所有题；修复后仅目标及变化影响的已批准后继重建，独立未改模型原样保留，无有效审核且依赖变化的题阻断。追加祖先上下文反例为1 failed/2 passed，证明祖先变更、中间模型JSON同值时叶子缓存仍可能过期；生成上下文改用完整祖先闭包后通过。最终相关回归111 passed/7.59秒。
- 集成55 passed/5.88秒（`.local/d22-review-integrated-final.xml`）；完整Guidance/Polish/core为**707 passed/27.00秒**（`.local/d22-review-dispatch-full.xml`）。新增合同字段导致一处旧精确断言需直接迁移，未放宽生产校验。公共任务/API与前端未改，不重复前端构建。独立只读审查未发现可行动问题，传递依赖与特殊ID探针通过。

D2.2a工程出口完成。新恢复工具`.local/run_d2_scoped_review_flash.py`默认无网，显式live才启用Flash；原题/同族额外请求上限分别24/16。它检查新单题审核边界，只从历史目录选原始生成缓存，旧整题Reviewer/Reviser不复用；缓存内容和摘要不修改，现行schema/语义/祖先上下文决定能否复用。D2.2b真实运行及D2.3仍待验收。

## D2.3：独立数学核对准备

`.local/d2_independent_math.py` 与 `.local/d2-independent-preparation-20261109/` 从原DOCX核对19段、5表和SHA，原件总需求4065。6个正反例通过，旧Q1真实解独立重算7625，与既有DP参考一致。

- Q2：逐周机器人需求20%四舍五入，104周合计损毁810台机器人、3240操作手。
- Q3/Q4：10%四舍五入、指导容量20，合计419台、1676手；14周不能使用 Python 的 half-even `round`。
- Q4：逐批边际阶梯价，不是整单折扣；输出表须含指定周和总计，训练人数包含新手与指导者。
- Q5：共同预测105–112周；方案1固定Q4前104周及期末保养队列，105周高价即用采购区别于普通延迟采购；比较1–112周总费用。不能无条件预设方案2更便宜。

独立参考脚本`.local/d2_independent_milp.py`不调用应用编译器/算子，使用现有SciPy；9项正反例通过。Q1与先前DP一致；Q2/Q3按条件解释求得primal=dual、gap=0，并通过逐期独立状态和费用核算：

| 子题 | 条件费用 | 采购艇/手 | 最优性 |
| --- | ---: | --- | --- |
| Q1 | 7625 | 3 / 42 | 与DP一致，gap=0 |
| Q2 | 669480 | 879 / 3826 | gap=0 |
| Q3 | 440320 | 498 / 2338 | gap=0 |
| Q4 | ≤403290 | 498 / 2338 | 180秒下界398545，gap=1.17657%，未证最优 |

Q4采用真实二元段序约束，不能跳过前档。30秒上界404130/下界391265的证据保留；180秒结果另存`.local/d2-independent-milp-q4-180s-20261109/`，没有修改生产预算。原始矩阵、解、来源、费用和界限证明在`.local/d2-independent-milp-reference-20261109/`。Q2首8周费用11615来自104周最优计划，同时改变损毁率和视野，不能把差异全归因损毁。

恢复后追加构造性见证：`.local/d2_cross_substitute_initial.py`使用独立Fraction精确算术，不调用app编译器/算子，把独立Q1–Q3最优计划与Q4可行计划映射至本轮原始模型。`.local/d2-recovery-independent-audit-20261109/cross_substitution_report.json`记录Q1–Q4分别63/1560/1144/2704条约束实例，最大违约均0，变量域全部通过，目标精确为7625/669480/440320/403290。Q3的available_operators不含保养队列，Q4 maintained_operators与maintenance_process_operators互斥，映射分别核对；训练总人数包括新手与指导者。该证据只证明这些参考计划在当前模型中可行且计费一致，不证明可行集完整等价或求解器已执行。

这些是独立条件基准和可行见证，线上流程没有执行解，因此尚不能对照新产物完成D2.3。Q4独立最优性及M=1000上界充分性、Q5预测/两方案及整题充分性未评估；不能把Q4当前可行上界当成Q5所需的最优前缀。
