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

### 逐题实测的中断检查点与独立反例

工程检查点`bee9e6a`已推送并核对远端SHA。两轮逐题实测目录为原题`.local/20261109-111218-005236-d2-scoped-review-flash`、同族`.local/20261109-111218-009237-d2-scoped-review-flash`，上限分别24/16次。会话中断后进程已不存在，原始目录保留，未补写响应或伪造run_summary。

| 输入 | 已启动 | 已返回 | 已知total tokens | 未知请求 | 中断位置 |
| --- | ---: | ---: | ---: | --- | --- |
| 原题 | 12 | 11 | 513911 | 第12次Reviewer_problem2 | 初审 |
| 同族 | 11 | 10 | 330096 | 第11次Reviewer_sp1_model | 修订后的终审 |

合计已知844007 tokens，另2次无响应、用量未知；未知请求仍占总上限及对应合同最多两次尝试，不按零消耗处理。中断审计`.local/d22-interruption-audit-20261109/audit.json`核对21个返回响应的prompt/completion/total标量一致；完整usage对象仅有LiteLLM补入null字段差异。原进程起始config_hash仅在内存，缺失终态时不能补称已证明“配置自启动未变”；当前凭据未出现在工件字节中，来源/缓存的逐项哈希核验按实际证据记录。

- 原题Q3/Q4因完整祖先上下文变化重新生成；Q5a补充有效缓存通过重新校验。Q5b架构length后在预设16384额度恢复，但indexed_core两次8192/12288均reasoning_exhausted，单题blocked；其余题继续审核。Q1初审取得approved/reviewed，22数学项passed、A_U001 accepted；此为at-most10条件模型，exactly10替代情景和敏感性仍无执行证据。
- 新Q3变量口径改变，训练人数仅学员，成本分别收取学员及指导者10元；独立参考计划经Fraction代入1352条约束与1248个变量域全部通过，费用440320一致。证据`.local/d2-scoped-independent-audit-20261109/new_initial_problem3_witness.json`，仅为可行见证。
- 新Q4有实质数学错误：库存转移扣除损毁操作手，但`operator_post_use_maintenance`仍要求保养不少于上一周4×部署数。周1部署11、损毁1，周2存活操作手应保养40，模型却强制44。独立403290计划代入2288条约束/2081个变量域时，仅51周该约束失败（最大差44），其余约束、域与费用一致。证据`new_initial_problem4_counterexample.json`，没有手修模型，等待审核是否捕获。
- 同族初审两题均revision_required；第二题首次因`/candidate_models/0`不是允许的数学计算证据路径而semantic失败，第二次在相同32768额度恢复。首次架构修订length→16384 stop，分类/预算保持。初审正确认可原始双对象费用和回流，却未纠正拆题把原文明确规则列作歧义，推动后续错误推荐。
- sp1修订实际删除指导者费用和G≤T，仅剩T≤4G；全部变量仍仅period，9候选并无9套执行模型。独立采购/服务计划将闲置模块标作指导者，G=[12,2,0,3,2,0]、T=[2,4,0,2,4,0]，原始数学声明108条约束与全部域通过，费用1311<正确口径1388，证明漏费及免费指导者避保养漏洞。证据`.local/d22-scoped-variant-audit-111218/sp1-revised-formulation-counterexample.json`，不是求解最优值。
- sp2确实生成scenario×period的9套变量。独立构造计划在原始响应及canonical中均通过765约束/765域检查，正确`double_idle_each`分支为1388，推荐`inclusive_idle_each`分支为1311；`idle_post`与`idle_none`规则体完全相同，指导后保养没有对应状态。证据`.local/d22-final-math-independent-20261109/sp2-revised-formulation-adjudication.json`。不能将“9情景已建模”当成所有情景语义正确。

续跑工具`.local/run_d2_resume_review_flash.py`从原题初审/同族终审阶段在新目录继续，保留原始模型、审核请求及本轮有效合同缓存。旧目录不动，已知错误模型仍作为待复审输入，不能直接批准。原题仅剩12次、同族5次；按agent+原始逻辑payload汇总跨中断的合同尝试，两个中断合同各只余一次真实发送。D2.2b/D2.3在续跑终态前保持未验收。

### 审核续跑：同族终态与原题第二次中断

续跑工具先补齐失败诊断的阶段快照，RED为1 failed/1 passed，GREEN为14 passed/5.954秒；两份无网probe分别复用Q1审核缓存、直接进入同族终审，API为0。独立验收见`.local/d22-interruption-audit-20261109/resume_harness_review.json`。

- 同族续跑`.local/20261109-141726-666632-d2-resume-review-flash`已终态blocked，新增2次Flash调用、109403 tokens、328.266秒；与来源轮合计13次已启动、439499已知tokens、1次用量未知。sp1为reviewed/revision_required，指出漏指导者费用及仅单一可执行情景，但仍把明确原文当歧义。sp2为reviewed/approved，26数学项、2假设及3修订项均通过，漏判推荐分支1311反例及情景语义问题；其执行投影因依赖sp1未批准而blocked，executable为空、verified_count为0。guidance明确无执行，文档质量audit PASS不代表数学通过。独立裁决见`.local/d22-final-math-independent-20261109/variant-terminal-adjudication.json`；来源、配置、凭据扫描及预算核验见`resume_variant_audit.json`。
- 原题续跑`.local/20261109-141700-871570-d2-resume-review-flash`新增5次已启动、4次返回，已知384490 tokens；第5次Reviewer_Q5a未返回，会话中断后进程已不存在，未伪造run_summary。连同111218来源累计17次已启动、898401已知tokens、2次用量未知，原题24次总上限剩余7次。Q2/Q3分别在32768额度返回length，记录未评估；Q4首发stop因`/data/3`数学证据路径不合规而semantic拒绝，第二次stop成功恢复，两个Q4请求合计214707 tokens。
- Q4有效结论为reviewed/revision_required，31项数学检查中28 passed、3 failed，准确要求从保养人数扣除损毁操作手，与51周独立反例一致。当前模型约束仍未改变，后续必须核对实际修订为`4*(部署数−损毁数)`；不能因文字指出错误就称已修复。独立schema/语义复验、响应与缓存相等及原件SHA见`.local/d22-final-math-independent-20261109/original-q4-initial-review-adjudication.json`。
- 第二次中断审计见`.local/d22-interruption-audit-20261109/second_interruption_audit.json`。后续只在新目录聚合111218→141700来源链；Q1/Q4有效缓存重验，已完成的Q2/Q3截断仅原始响应无网重放，Q5a未返回请求仍占一次合同尝试。旧目录和所有原始响应、模型不改写。

同族实际请求还证明错误义务的传递：paragraph-3明确双对象收费，却未成为独立locked fact；UNC-001将其列为歧义，UNC-002与LF-014/LF-015冲突。初审要求强制三×三情景，修订按要求执行，终审要求所有旧修订项接受才可批准。现有提示虽要求原文优先，本轮并未落实；相关原文、请求与响应证据已收入`variant-terminal-adjudication.json`。后续边界修正必须处理来源裁决及错误修订义务的撤回，不能只追加收费公式。

### 链式续跑终态：Q1实际执行，整体仍blocked

`.local/run_d2_chain_resume_flash.py`仅处理上述两层来源，Q2/Q3完成的截断响应按精确history、模型、温度、额度及超时匹配后原样交给真实Requester重验；不合成审核结论、不重新计费。Q1/Q4有效缓存仍由产品重新校验。最小RED先复现旧工具不能接受第二层来源，相关GREEN为18 passed/8.14秒（`.local/d22-chain-integrated-tests.xml`）；最终无网probe停Q5a，旧17次/898401已知/2未知账本不变，独立验收为`chain_harness_review.json`。

正式输出`.local/20261109-d2-chain-review-live-01`在隐藏独立进程执行，598.547秒后自然终态；新增6次Flash请求、390769 tokens，未使用剩余1次额度。原题三段累计23/24次已启动、1289170已知tokens、2次未知；同族13/16次、439499已知tokens、1次未知。连同此前D2的51次/1555969 tokens，**累计87次已启动、3284638已知tokens、3次未知**，总用量仍为null。原始Q2/Q3响应在初审/终审共重放4次，未重复累计；缓存重验3次。单合同两次、总额度、源链/快照原字节、配置和生产源码不变、凭据扫描全部通过，证据`.local/d22-interruption-audit-20261109/chain_terminal_audit.json`。未补写旧响应或修改任何原始模型。

| 子题 | 最终审核 | 执行与独立裁决 |
| --- | --- | --- |
| Q1 | reviewed / approved | 唯一实际MILP解7625；8周独立逐期核算、63约束/72域、DP和求解器primal=dual一致 |
| Q2 | not_assessed / blocked | 32768额度length，原始失败离线重放；无本轮解 |
| Q3 | not_assessed / blocked | 32768额度length，且依赖Q2未批准；无本轮解 |
| Q4 | reviewed / revision_required | 保养错误已修复，情景证据仍缺且依赖Q3未批准；无流水线解 |
| Q5a | not_assessed / blocked | 补发stop响应引用`/data/0`等不允许的数学证据路径，语义失败；累计两次已用完，本地guard拒绝再发送，无预测结果 |
| Q5b | not_assessed / blocked | Q4修订导致祖先变化，旧生成失败产物失效；无两方案结果 |

Q1费用独立分解为购艇600＋购手4200＋艇保养550＋手保养1805＋学员训练420＋指导者训练50＝7625；库存、购买/训练可用延迟、保养排他及指导容量逐周通过。5个FA条目均引用同一个MILP解，不是5次独立实验；该结论仅适用于at-most10口径，exactly10替代情景与敏感性仍未执行。证据`.local/d22-final-math-independent-20261109/original-q1-pipeline-solution-audit.json`。

Q4新模型显式定义`post_use_maintenance_survivors[t]=4*deployed[t-1]-4*damaged[t-1]`并要求保养不少于存活数。独立403290计划经Fraction代入2704约束/2289域全过，周2保养为40，旧51周反例消失。但损毁约束把`100*weekly_damage_rate`作为损毁数的系数，只有10%时碰巧正确；需求50、损毁率8%/10%/12%时模型给出6/5/4，正确应为4/5/6。终审31项数学检查全passed，未发现该敏感性反例；保养和表3修订项accepted，情景项仍revision_required。终审首发stop再次因`/data/4`路径semantic失败，第二次有效恢复。403290仍是独立可行见证，不能当本轮求解或已证最优值。证据`original-q4-revised-witness.json`及`original-terminal-adjudication.json`。

原题编译/执行成功，`verification_report.json`为partial：execution_verified/evidence_supported/numerical_validation passed，model_adequate及mathematical_review not_assessed。最终产品状态仍blocked，指导文档audit BLOCK另报缺少建模路线/最终答案章节及未登记数值，不能把局部数值正确当完整交付完成。文档中未呈现7625和逐周答案，实际存在的Q1推荐MILP被写成“未登记推荐方法”；canonical有15个参数，parameter_registry为空，FA1–5均持有整个求解payload，未投影成应填答案。独立原样渲染/审核回放与原工件完全一致；11个被阻断数字全部可归于有精确source_ref的题面事实、来源定位符或审核计数，属于审核输入口径不匹配。裁决见`.local/d22-final-math-independent-20261109/guidance-terminal-adjudication.json`。后续须单独核对规范模型/结果到指导的投影及数字审核证据入口，不能只删审核要求或手修本次产物通过。

**D2.2b复测与独立裁决任务完成，D2.2/D2.3整体未验收。** 生产代码仍为`bee9e6a`，未重复707项全量或前端构建。下一项来源裁决/错误修订义务撤回的具体范围与RED验收已写入唯一长期计划D2.2c，等待新边界批准；审核截断、证据路径、参数敏感性和指导出口限制均保留。

## D2.2c：获批来源裁决与有据撤回

用户明确批准实施。原始反例是同族题将明确双对象收费及闲置保养关系列为歧义，错误义务推动模型删去指导者费用。当前迁移让Reviewer提出有精确引文的来源纠正，系统从原件验证并生成新的ProblemSpec，不覆盖旧题意；消解歧义必须提升完整locked fact。修订义务显式分为source/model，只有已应用且精确覆盖全部basis的来源裁决才能撤回source义务，真实model缺陷不能撤回。

### RED → GREEN → 集成验证

- 来源合同：4 RED→4 GREEN（`.local/d22c-source-review-{red,green}.xml`）；撤回/绑定：6 failed、4 passed→10 GREEN（`d22c-source-evidence-{red,green}.xml`）。旧审核绑定缺少必填nullable来源摘要即失效，不猜测迁移。
- 来源解析：22 failed、4 passed→26 GREEN；覆盖真实原件、伪引文/表格、错作用域、同target等价提议和冲突、完整关系事实提升及旧对象不变。最终来源文件29 passed（`d22c-source-kind-green.xml`）。机械引文核验不证明自然语言的数学含义正确。
- Reviewer：3 failed、1 passed→4 GREEN（`d22c-source-reviewer-{red,green}.xml`）。只复用原题意/模型/审核/catalog/profile绑定都重算一致、且完整祖先未变的无关approved决定。共享冲突2 RED→37项相关GREEN（`d22c-conflicts-{red,green}.xml`），拒绝整批冲突、隔离共享范围及后继，保留独立决定和原始错误证据。
- 有界流程：6 RED与2项原始绑定快照RED→11 GREEN（`d22c-flow-final.xml`），相关回归32 passed。Pipeline保存原字节题意/模型/批准绑定，在既有一次修订内应用来源裁决，Modeler读取新题意；共享来源所有者及完整后继均失效，即使中间模型JSON同值。终审再提出纠正只阻断，不触发第二轮循环。
- 独立审查补充一：将CSV来源伪装为uploaded_file并伪造rows仍可获得纠正/重载通过。修正夹具后3项RED均为DID NOT RAISE（`d22c-source-kind-red-final.xml`）；类型必须匹配真实格式，未解析通用附件不再注册自报rows为原文，来源模块29 GREEN。无关通用附件不会阻止合法原文裁决。
- 独立审查补充二：没有具体纠正提议、仅有source义务时，共享来源的其他题及后继仍可执行/复用。uncertainty与locked_fact两种对象的投影、修订共4 RED；由统一未裁决来源闭包收集basis和纠正目标，两个消费者直接使用，相关32 GREEN（`d22c-pending-scope-{red,green}.xml`）。仅独立题保留，待决范围不调用生成API。
- REFACTOR：来源裁决由单一新模块拥有；内部合同及调用方直接迁移，无兼容门面。原有8个测试文件补显式kind/model及合法最小夹具，保留原断言。最终完整后端 **767 passed / 24.44秒**（`.local/d22c-final-full.xml`），覆盖Guidance/Polish/core；公共协议和前端未改，不重复前端构建。

### 清理与实测准备

删除68个确认无引用且可重建目录（67个pytest basetemp及本地pycache）、19812文件、157531971字节（150.23MiB）；12983份保留文件SHA一致。证据为`.local/d22c-cleanup-{manifest,result,verification}-20261109.json`。正式测试、原始API响应/模型、恢复链、报告引用和独立数学基准保留，清理不是验收。

同族harness `.local/run_d22c_source_flash.py` 15项通过；无网probe原字节重放既有初始生成检查点一次，然后使用真实产品Pipeline进行审核、最多一次纠正/修订及执行/验证/指导，停在首个Reviewer传输前，0 API。Q4 harness `.local/run_d22c_q4_review_flash.py` 6项通过；从当前真实Reviewer捕获Q4完整payload和语义闭包，保留原件/题意/三个完整祖先，0 API。两项均不复用旧审核或旧schema缓存、不向提示注入已知独立答案。

### 新实测终态：未达到来源裁决验收

工程检查点`0d9269f`已推送。生产冻结后两项实验均在隐藏进程自然结束；只用deepseek-flash，单次/每合同预算不增加，剩余额度不用于重复追跑。

| 输入与范围 | 目录（backend/下） | 新请求 | total tokens | 秒 | 终态 |
| --- | --- | ---: | ---: | ---: | --- |
| 同族初始生成检查点→真实后续Pipeline | `.local/20261109-d22c-source-live-01` | 4/12 | 178026 | 580.343 | blocked，无纠正/修订/执行 |
| 原题当前Q4的全新局部初审 | `.local/20261109-d22c-q4-flash-01` | 1/2 | 113968 | 212.219 | ContractTruncationError，无有效审核 |

同族两题首次均把`basis_refs`混用为模型路径/数学检查ID，schema拒绝；第二次修正该字段后，又以`/candidate_models`作数学证据路径而semantic拒绝。两题均`assessment_status=not_assessed`、执行投影为空；修订调用0次，没有source_resolution、revision_request或execution_manifest。四份原始候选的source_corrections均为空，仍把明确双对象收费和闲置保养称为真实歧义，要求三×三情景。无网合同验证通过的新裁决/撤回路径没有被真实请求行使，不能把合同失败解释为成功识别错误模型，也不能声称1388已复测通过。最终指导audit PASS仅证明阻断披露/文档规则，不代表数学验收。

Q4首发prompt81200＋completion32768＝113968，全部completion用于reasoning，正文为空、finish_reason=length。现有initial=ceiling=32768，产品直接拒绝截断，没有增加额度或发第二次。独立Fraction核验仍得到部署50时rho为8%/10%/12%的模型损毁6/5/4，正确4/5/6；本轮没有有效审核，无法宣称Reviewer发现了该错误。其模型与此前见证SHA一致。

本轮合计**5次、291994 tokens、未知0**；D2累计**92次已启动、3576632已知tokens、3次历史用量未知，总量仍为null**。reasoning已在completion内，不重复累计。原件、全部旧实验、生产源码与持久配置均不变，凭据扫描通过；按原始请求/响应/账本再次核对tokens及每合同次数。独立证据位于`.local/d22c-independent-audit-20261109/`：`variant-adjudication.json`、`q4-adjudication.json`、`api-and-integrity-audit.json`。

结论：D2.2c.1–3工程实现已完成，D2.2c.4实测已结案但出口未通过，保留未勾选；D2.2/D2.3整体仍未验收。下一行为是获得有效的来源冲突裁决和模型证据引用，不能靠放宽路径校验、提高预算、手修输出或重复跑同一实验代替。当前成果尚不证明可选来源纠正足以纠正Reviewer对错误拆题的依赖。

### D2.2c.4 继续：公开引用规则与来源语义对照

调用边界重新核对：`core/workflow.py:97`将Guidance/Polish分流；`guidance/workflow.py:25`构造六阶段Pipeline，`reviewer.py:50`生成逐题审核和来源纠正提议，`pipeline.py:207`在一次修订时应用已通过机械校验的提议。Polish独立运行并共享core写作能力，不消费这些来源合同。本轮只修复已有审核合同，不新设产品阶段。

**引用合同的RED→GREEN**：`.local/d22c4-path-probe-20261109/probe_results.json`用JSON Schema、Pydantic和实际语义校验器重放4份原始响应；四份都通过发送的schema，第1/3份随后被basis_refs规则拒绝，第2/4份被数学路径前缀规则拒绝。最小正式测试有效RED为9 failed/9 passed（`.local/d22c4-reference-schema-red-final.xml`），证明首次/重试请求没有公开其运行规则；初轮两个夹具漏字段的失败未计为有效RED。`ProblemRecordReference`和`MathematicalEvidencePath`现由字段类型统一导出schema和校验输入，移除重复语法判断；实际元素存在性、来源作用域及业务状态检查保留。Reviewer提示同步区分source basis、数学路径和较宽的revision路径，未更改合法输入范围。相关65 passed/4.01秒，完整后端**785 passed/22.54秒**（`.local/d22c4-reference-full.xml`）。4份旧响应原样被新schema拒绝、旧树SHA不变，见`replay_current_results.json`。显式增加jsonschema开发依赖，TOML验证通过；此依赖声明是配置验证，不声称TDD。

**来源前置条件探针**：`.local/d22c4-source-probe-20261109/source-obligation-probe.json`证明当前合同没有逐UNC来源判断，UNC也不在数学检查覆盖ID中。两个明确标注为独立诊断的合同，沿用原第2/4响应的模型修订义务/假设检查，数学项改为明确not_assessed；它们通过真实validator和Reviewer覆盖要求，source阻断集合为空，错误题意义务以kind=model进入Modeler。该诊断不是手修原响应或实际有效审核，只证明当前职责没有来源判断前置条件。

两项诊断均默认无网，显式live才启用Flash；本地guard验证分别5项和3项通过，属于tests-after，不伪称TDD。第一项仅给完整来源与ProblemSpec，逐UNC强制原文判断；第二项仅给原文与semantic_key/source_refs主题定位，移除拆题断言和候选。两者都不提供ModelIR、独立正确答案或1388，不应用输出，也不是产品验收。

| 诊断 | 目录（backend/下） | 次数 | total tokens | 秒 | 独立语义裁决 |
| --- | --- | ---: | ---: | ---: | --- |
| 来源与既有题意候选 | `.local/20261109-d22c4-source-diagnostic-flash-01` | 1 | 17768 | 68.453 | 两UNC均声称消解；训练费纠正错误，闲置保养正确 |
| 仅原文与主题定位 | `.local/20261109-d22c4-source-ablation-flash-01` | 1 | 10497 | 56.625 | 训练费仍判ambiguous，闲置保养specified且正确 |

第一项给出LF-025：“模块训练费用按新模块个数计收……指导者费用包含在该8元/个中，不另行单独计收训练费。”它同时精确引用“训练成本同时对新模块和指导者计收”，完整范围和现有resolver均通过，但4新模块+1指导者的40被改成32。LF-026按每个未服务且未指导的周期保养，符合原文。第二项未给错误候选仍把“含指导者”视作可能包价，未解决计费关系；单样本且输出合同也变化，不能据此断言候选锚定的因果效应。两项均不能证明仅把来源提取移到独立阶段足够可靠，机械source_resolves也不能作为语义正确证据。

本轮2次合计**28265 tokens、未知0**；D2累计**94次、3604897已知tokens、3次历史未知，总量null**。每次请求/响应/usage、原文/旧树/生产/配置/快照SHA再次独立核对，reasoning未重复计，原始工件不变。独立裁决和预算证据为`.local/d22c4-source-probe-20261109/live-adjudication.json`、`ablation-adjudication.json`、`api-and-integrity-audit.json`。

**边界裁决**：对外schema和真实校验不一致已修复。最危险的新实证是纠正提议可以通过真实引文校验却改变原文关系；现有resolver隐藏的是机械来源和快照一致性，不拥有独立语义判断。更小的下一替换是将纠正提议与独立语义应用许可分离，保留一次修订及共享失效；完整范围和首个RED已写入唯一计划D2.2d，待批准。未采用大规模重拆，也没有放宽校验或追加提示后反复请求。D2.2c.4父项和D2整体仍未验收。

## D2.2d：获批独立语义复核与应用许可

用户明确批准“实施独立语义复核门禁”。Model Reviewer现在只提出纠正；`source_adjudication.py`独立核对原件、目标源记录和完整提议，输出supported/contradicted/not_assessed，绑定题意、catalog/profile、原件和所有owner的完整提议。机械来源校验可接收待审提议，但应用只接受与许可完全一致的supported集合。旧缺许可的纠正记录和旧缺语义摘要的审核绑定直接失效，历史工件不改写。

### 工程证据与独立审查

- 来源RED `.local/d22d-source-red.xml`直接复现真实“双对象收费”引文配错误单对象关系，在旧resolver无许可也能应用（DID NOT RAISE）。新增异步RED `.local/d22d-source-await-red.xml`复现等待provider期间修改提议会使旧结论绑定新内容；请求现持有深拷贝快照。合同最终23 passed，`.local/d22d-source-final.xml`。
- Reviewer RED `.local/d22d-reviewer-red.xml`为4 failed：三种语义结论均未触发独立请求，旧审核绑定缺字段仍接受。GREEN覆盖拒绝/未评估/请求失败转为未解决source义务、保留已有数学检查与独立题，终审不启动第二次复核、不覆盖初审许可。共享来源所有者和完整后继继续由统一pending source闭包阻断；真实model缺陷不可撤回。
- 应用/恢复RED `.local/d22d-flow-red.xml`为2 failed，补充完整初审绑定RED `.local/d22d-flow-binding-red-final.xml`为7 failed/10 passed。应用前重新绑定审核、模型、执行投影、题意、来源及语义许可；缺失或自洽替换许可、改review/model/projection均拒绝。恢复基于原题意重新检查许可和已应用记录，全拒绝没有resolution时仍绑定许可。组合65 passed，`.local/d22d-flow-final-green3.xml`。
- 独立审查复现50条合法model义务加1条source义务导致整个审核返回失败（`.local/d22d-review-capacity-red.xml`）。取消required_revisions/revision_checks不一致的固定50条上限，保留唯一ID、完整有序覆盖及每合同token/两次请求预算；原50条model义务逐项不变，source义务单独保留。同owner混合支持/拒绝仍隔离，已应用纠正后的终审新提议不能覆写许可。审查及旧flow夹具18 passed，`.local/d22d-review-audit-final.xml`；旧来源测试迁移60 passed。
- 无网生产Stage审查 `.local/d22d-flow-readonly-audit/audit.json`：全拒绝只保留independent；删除许可或替换为另一份自洽许可后，数学审核由passed变为not_assessed，不能沿用旧批准。
- 最终完整后端 **834 passed / 27.18秒**，`.local/d22d-full.xml`，涵盖Guidance/Polish/core。公共API、前端和求解算法未改；未重复前端构建。新行为有真实RED，旧夹具直接补显式有效许可，没有兼容路径。

### 原始错误与正确关系的真实Flash复核

新实验`.local/20261109-d22d-semantic-flash-01`调用生产`LLMSourceAdjudicator`，原样使用上一诊断的LF-025/LF-026提议；只发送原文、目标源记录、待审提议、提示及schema，无ModelIR或独立正确答案。预演`.local/20261109-d22d-semantic-probe-01`为0调用；独立出站审查通过。标准review额度32768/32768、最多2次，实际只用 **1次deepseek-flash、2520 prompt＋9522 completion＝12042 tokens**，其中9218 reasoning已计入completion；59.188秒自然终态、未知0。

| 原样提议 | Flash结论 | 独立裁决与应用 |
| --- | --- | --- |
| LF-025：训练仅按新模块收费、指导者不另收费 | contradicted | 正确拒绝。原文明确向两对象计收；4新模块＋1指导者应40，错误提议为32。未提升该事实，UNC-001保留。 |
| LF-026：每个未服务且未指导周期均保养 | supported | 与原文一致。隔离应用只提升该事实并消解UNC-002。 |

完整两项提议送入应用入口时被拒绝，只有supported子集能生成`permitted_problem_spec.json`和`permitted_source_resolution.json`；原始`problem_spec.json`及所有历史运行保持原字节。调用账本、原始response、许可、应用快照、原件/历史目录/生产/配置哈希和凭据扫描均核对；独立审计见`.local/d22d-semantic-audit-20261109/audit.json`。

本轮之后D2累计 **95次已启动、3616939已知tokens、3次历史未知，总用量仍为null**。本次完成D2.2d.1–4规定的工程和两项来源反例验收，压缩为计划不变量15。它证明本例错误纠正被拦截且正确提议可应用，不证明自然语言语义普遍可靠，也没有重新生成/执行整题。D2.2c.4及D2.2/D2.3仍未验收；下一步回到真实Reviewer→一次纠正/修订→执行链，继续核对同族题和既有Q4反例。

## D2.2c.4d–e：接入门禁后的真实链与终态归因

生产检查点`93f4200`已推送，新增隔离工具`.local/run_d22d_pipeline_flash.py`从原始有效初始题意/模型接续真实Pipeline。旧工具和历史目录字节保留。新版保存独立语义许可原字节，并核对来源请求恰含完整原件、目标源记录及此前合法审核返回的提议；不注入独立正确答案。仪器边界11 failed/1 passed→27 passed/6.23秒（`.local/d22d-pipeline-harness-{red,final}.xml`），含原15项预算/检查点/无网测试。正式probe `.local/20261109-d22d-pipeline-probe-01`为0调用，抵达当前sp1 Reviewer。

### 真实终态：外部空响应，未进入数学裁决

`.local/20261109-d22d-pipeline-live-01`保留12次总上限、每合同最多两次和标准额度；117.64秒自然结束为blocked。实际两次请求都使用deepseek-flash：sp1约110.703秒、sp2约6.188秒，均报`APIError: DeepseekException - Unable to get json response ... Original Response:`且响应为空。没有provider response、finish_reason或usage；这是传输/供应商响应失败，不能解释为模型识别了数学错误，也不能证明全局服务故障原因。

- 两题均not_assessed，数学/假设/修订检查及纠正提议为空；执行投影为空。初始ModelIR仍原样solvable，模型生成未退化，但审核批准不可获得。
- 未触发独立来源请求、来源应用、模型修订或求解；无source_adjudication/source_resolution/revision_request/execution_manifest。指导audit PASS仅证明阻断披露，不能当成数学或完整流程通过。
- 本轮**2次已启动、2次用量未知**；已知token小计0表示没有已知用量，不表示零消耗。D2累计**97次已启动、3616939已知tokens、5次未知，总量null**。失败合同自然结案，未重置计数重跑。
- 原件、88份历史源目录文件、实测期间179份生产源码/资源、配置和工具SHA一致；7份阶段快照清单、绑定和空执行投影重算通过，凭据扫描通过，审计不改实验。独立证据：`.local/d22d-pipeline-audit-20261109/api-and-integrity-audit.json`、`.local/d22d-pipeline-flow-audit-20261109/audit.json`及`.local/d22c4-chain-math-audit-20261109/terminal-adjudication.json`。

### 原件和Q4无网复核

`.local/d22c4-chain-math-audit-20261109/preparation.json`直接从原DOCX重算六期DP=1388，并复核旧1388解的逐期状态和六项费用。旧1311见证仍通过原始错误模型：sp1为108条约束/114个域、sp2九情景为765条约束/765个域；它继续证明漏费推荐风险，不能当正确最优基准。以上均为独立历史对照，不是本轮真实链的执行结果。

`.local/d22c4-q4-current-boundary-20261109/q4-boundary-adjudication.json`用Fraction重新代入未改的Q4原始约束：部署50、rho=8%/10%/12%时仍允许损毁6/5/4，正确为4/5/6。当前生产无网捕获的数学payload、实际user content、三个祖先及32768/300秒预算与旧截断请求一致；完整请求并非逐字相同，引用规则和列表上限有提示/schema变化。新来源门禁须在有效纠正提议后触发，旧Q4正文为空、尚未到此边界；没有证据表明门禁解决了该截断或敏感性错误，因此本轮未为同一数学输入重发昂贵请求。此判断不意味着以后必然截断；Q4仍未验收，403290仅保持既有可行上界。

### 真实失败推动的局部呈现修复

原`pipeline_events.json`的done消息声称“guidance audit blocked the generated plan”，但`guidance_audit_report.json`实际PASS，阻断来自模型初审失败。最小正式RED为3 failed/2 passed（`.local/d22c4-terminal-red.xml`），同时复现验证阻断被误归因及partial消息未披露未完成。状态与消息现由单一`_resolve_final_outcome`直接派生：指导审核BLOCK、模型批准blocked、结果验证blocked及partial/completed各自准确表述；模型批准阻断优先于下游镜像验证报告。内部调用方直接迁移，状态、公共字段、许可门禁、重试和数学算法不改。

相关37 passed/7.16秒（`.local/d22c4-terminal-green.xml`）；完整后端**839 passed/29.66秒**（`.local/d22c4-terminal-full.xml`）。原始终态只读回放`.local/d22c4-terminal-replay-20261109/terminal-adjudication.json`确认只改变done.message/current_subtask，TaskResult全部字段与原事件及run_summary一致，115份原实验文件SHA不变；60组状态组合的原状态判定全部保留。原实验保留，不把之后的消息修复写回旧运行。D2.2c.4d实测/裁决和4e局部归因修复完成，父项D2.2c.4及D2.2/D2.3继续未验收；下一步仍是取得有效真实审核与执行证据。

## D2.2c.4f：网络恢复后的有界续跑与来源前置证据

用户要求网络恢复后继续。续跑只承接`.local/20261109-d22d-pipeline-live-01`的两个失败初审，不重置预算：旧两次请求/失败原字节继承、未知用量保留，两初审各只允许补发一次；新合同仍最多两次，整链上限12次。生产固定为`01b4c91`，相对前轮`93f4200`只有已验收的终态消息归因变化。manifest明确保存两套生产哈希及唯一差异，不声称两轮生产完全相同。

### 续跑工具的RED、GREEN与预演

- 新工具`.local/run_d22d_pipeline_resume_flash.py`继承已冻结的真实Pipeline工具，未修改旧工具或生产。最小RED 8 failed（`.local/d22d-resume-red.xml`）：实际继承计数0而非2，或改变初审history/schema/payload/传输参数后未拒绝；失败来自目标行为缺失。
- GREEN 18 passed/14.07秒（`.local/d22d-resume-expanded.xml`），与base27项联合45 passed/15.65秒（`.local/d22d-resume-final.xml`）。另一次独立18 passed/13.64秒（`.local/d22d-resume-independent-checks.xml`），覆盖累计两次/总12、未知用量、损坏继承、合法修订后新合同、语义重试不能重置初审额度。重构复用原工具的预算、来源请求核验及阶段快照，只新增继承和原请求绑定；冻结后不再修改。
- 工具SHA256 `05d85fc977e51afb07ad46a4679ce0d7fd646d1dc143bb5f5d8911ba75abd816`。正式无网probe `.local/20261109-d22d-pipeline-resume-probe-01`继承2次、新增0次、未知2次，总量null，首个完整history与原请求一致。独立审查`.local/d22d-pipeline-resume-audit-20261109/boundary-review.json`通过。

### 真实自然终态与用量

`.local/20261109-d22d-pipeline-resume-live-01`在隐藏进程自然结束，588.094秒、blocked。新增6次全部收到provider response，无新传输失败；继承2次后整链8/12。两初审的完整history（含系统/schema）及传输参数与原失败请求相同，初审合同累计各2次。新输出和原失败记录分别保留。

| 调用编号 | 真实合同 | finish_reason | prompt + completion = total tokens | 结果 |
| --- | --- | --- | ---: | --- |
| 003 | sp1初审 | stop | 19271 + 21008 = 40279 | 有效revision_required，无来源纠正 |
| 004 | sp2初审 | stop | 28131 + 22706 = 50837 | 有效revision_required，无来源纠正 |
| 005 | sp1修订架构 | stop | 13797 + 8562 = 22359 | 进入情景模型生成 |
| 006 | sp1索引/数据 | length | 13954 + 8192 = 22146 | 截断，拒绝接收 |
| 007 | 同一索引/数据合同恢复 | stop | 13954 + 7258 = 21212 | 两组三元素情景轴，形成九情景范围 |
| 008 | sp1数学声明 | length | 14915 + 32768 = 47683 | 全completion为reasoning，正文为空，无有效合同 |

新增合计**204516 tokens、未知0**，reasoning已在completion内。继承的两个空响应仍未知，整链总量null；不因没有usage计零。D2累计只加这6次：**103次已启动、3821455已知tokens、5次历史未知，总量null**。数学声明initial已达32768 ceiling，产品不再增加额度重试；尚余整链额度不代表允许绕过单合同预算或自然终态继续请求。

初审和最终审核的绑定/执行投影重算一致，revision_request除归档引用外精确保留初审；9份阶段snapshot manifest全部通过。sp1修订数学声明截断，sp2因上游失败阻断；最终两题not_assessed，executable为空，无execution_manifest、求解或verified_results。仅发生一次模型修订，没有独立来源请求、许可或应用。done消息准确为`model approval blocked execution`，guidance audit PASS不再被误报为阻断原因，也不能当数学通过。

原件、前轮全树、初始生成检查点、实测期间生产与持久配置保持，凭据扫描通过。用量独立审计为`.local/d22d-pipeline-resume-audit-20261109/usage-and-integrity.json`；工程审计为`.local/d22d-pipeline-resume-flow-audit-20261109/audit.json`，审计前后实测全树字节一致。生产未变，本轮没有重复839项后端回归或前端构建；此前839 passed仍是当前生产检查点的验证结果。

### 数学裁决与下一职责边界

独立审计`.local/d22c4-chain-math-audit-20261109/resume-terminal-adjudication.json`直接解析本轮DOCX，逐段/表/SHA与已核对原件一致。paragraph-3明确“训练成本同时对新模块和指导者计收”“其他未服务也未指导的熟练模块同样保养”。两份有效初审仍把这两项当歧义，合计产生5条kind=model义务且source_corrections均为空；四条要求替代收费/保养情景，另一条要求实际求解证据。独立来源忠实性判failed，当前数学执行结论not_assessed。旧1388基准和1311漏费反例继续适用，但没有本轮新解，不重复计算或冒充实际结果。

本轮观察到错误歧义→实际九情景数据合同→数学声明截断的顺序。没有消融实验，不能声称九情景是截断唯一原因，也不能声称前置来源判断必然消除所有截断。004的REV-003在数学审核阶段索取实际solution，另有执行顺序问题，先记录，不混入当前来源替换。

调用链已重新阅读核对：`core/workflow.py:97`分流Guidance/Polish；`guidance/workflow.py:25`构造Pipeline；`reviewer.py:153`只从已返回source_corrections收集提议，`:185`才调用`source_adjudication.py:180`；`pipeline.py:109`进入一次修订，`:245`只应用已有提议；`modeling_context.py:36`把所有kind=model要求直接交给Modeler。`polish/workflow.py:87`保持独立写作路径，不消费这些内部合同。运行中的两份有效初审和005/007真实请求验证了这条路径，不以静态阅读单独推断。

结论：当前模块把“是否需要来源判断”留给产生数学义务的同一个Reviewer，独立许可无法检查未提出的纠正。仅加强提示已经没有新增结构保证；仅加必填uncertainty_checks可以保证作答数量，仍未分离来源与数学义务。推荐把已登记歧义的解释及独立许可放到建模前，让纠正与保留歧义都经过复核，再由数学Reviewer消费已裁决题意；未评估来源阻断所属范围与后继，独立分支保留。代价是旧无来源许可模型/审核缓存失效、内部调用方直接迁移，语义误判仍需独立验收。具体影响文件、迁移边界及首批RED条件已写入唯一计划D2.2e，尚未批准或实施。

现状摘要：核心链本轮抵达真实初审与一次模型修订，停于数学声明截断；最大风险是可选来源纠正使错误歧义直接成为数学义务。本次新增有界恢复工具和独立审计、更新记录，生产不变；完成D2.2c.4f，父项D2.2c.4/D2.2/D2.3不勾选。下一项D2.2e.1待明确批准后先RED，后续仍需实际模型、执行和独立费用核对。

## D2.3：独立数学核对与剩余出口

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

上述Q2–Q4仍是独立条件基准和可行见证。链式续跑已新增Q1实际解7625并完成逐期对照，其余子题没有本轮执行解，D2.3整体未完成。Q4独立最优性及采购上界充分性、参数敏感性错误、Q5预测/两方案及整题充分性仍未验收；不能把Q4当前可行上界当成Q5所需的最优前缀。
