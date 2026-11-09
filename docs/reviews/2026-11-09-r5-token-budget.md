# R5.2 阶段预算、截断与有界恢复

日期：2026-11-09。分支：`codex/modeling-core-refactor`。唯一执行计划为 `docs/compute_first_guidance_plan.md`；本文保存 R5.2 的合同、回归和实测证据。

## 行为与预算

Guidance 的每次合同请求最多发起两次实际 LLM 调用，包含超时、格式错误及截断恢复。请求器向共享 LLM 显式传入 `max_attempts=1`，共享层关闭 SDK 的隐藏重试；其他调用方仍默认保留三次传输尝试，并有回归验证。每次 token 覆盖仅作用于当前调用，不修改 LLM 实例或持久配置。

响应先判断 `finish_reason=length`，再进行 JSON/schema/语义校验。截断时即使正文碰巧是可解析的完整 JSON，也不能批准或缓存。空正文且全部 completion 用于 reasoning 时记录 `reasoning_exhausted`；其他 length 记录 `output_truncated`。只有首轮截断且尚未达到显式 ceiling 时，才能使用原始完整请求提升一次额度。到达上限或耗尽两次调用时立即停止，不进行第三次请求。

语法、schema、语义、项目超时、传输和截断分别记录；语法/语义重生成不增加 token 上限。超时重试保留原请求，且与其他失败共用两次调用总额。未知用量记为 null，并标明 `usage_complete=false`，不伪报零消耗。诊断同时保留每次上限、finish_reason、返回用量、已预留输出额度及最大输出额度；reasoning 已包含在 completion 中，不重复加入 total。该上限是每次合同请求的调用/输出预算，不是整题总账单上限；网络失败的实际服务端消耗可能未知。

阶段 token 默认值依据 R3/R4 已保存的 Flash 用量设定，三种 workflow mode 共用这些 token 配额，保留分模式时间预算：

| 阶段 | 首轮 | 截断恢复上限 | 实测依据（completion，含 reasoning） |
| --- | ---: | ---: | --- |
| 题目结构 | 12288 | 16384 | R3 原题8692、同族4125 |
| 事实提取 | 24576 | 32768 | R3 原题24243、同族14221 |
| 模型架构 | 12288 | 16384 | R3 原题9305、同族7742 |
| 数据/参数 | 8192 | 12288 | R3 3315/4520、同族4976 |
| 数学声明 | 32768 | 32768 | R3 原题25222、同族30670 |
| 数学审核 | 32768 | 32768 | R4 成功审核21319–26208，另有32768截断反例 |

这不是对所有模型服务或题目规模的充足性保证。数学声明及审核到32768仍失败时明确保留失败，不自动继续加预算。1024首轮仅用于本次在线故障注入，不是正式审核默认值。

本次有效漏费审核耗时207.407秒，超过生产standard审核180秒；同时回查R3原题事实提取133.828秒，超过standard拆题120秒。先用这两个真实耗时取得RED，再仅将standard拆题最低时间改为180秒、审核改为300秒；其余阶段与fast/strict时间不变，配置更大值仍有效。

## RED → GREEN 与边界验证

- `backend/.local/r52-budget-red-20261109`：7项失败。旧代码接收了 length 的合法JSON、空截断后盲目重试；没有失败分类/用量记录；超时加语法错误产生第三次调用；一次上层传输调用实际触发三次底层调用。
- `r52-budget-green-20261109`：7 passed。截断拒绝、原因分离、用量记录与调用上限通过。
- `r52-stage-tokens-red-20261109`：5 failed / 8 passed。新增预算类型及阶段注入尚缺失，未知用量被统计成零。前三项为新合同接口缺失，不冒充已有数学行为反例。
- `r52-contracts-green-20261109`：68 passed，一项旧测试仅因诊断新增字段失败；迁移精确字典断言，保留原超时检查。
- `r52-final-budget-contracts-20261109`：18 passed，含新增的共享 LLM 默认重试回归。
- `r52-final-full-20261109`：当时618 passed，30.17秒；随后在线实验揭示阶段超时问题。
- `r52-observed-timeout-red2-20261109`：两项失败，120<133.828、180<207.407，证明生产默认不能容纳已有完整有效响应。
- 最终完整后端：`r52-final-validated-20261109`，**620 passed，20.36秒**，覆盖 Guidance、Polish 和 core。新增预算测试14项、实测时间预算回归2项；完整回归后不再修改生产代码。

最终命令在 backend 下执行，环境 `PYTHONDONTWRITEBYTECODE=1`、`LITELLM_LOCAL_MODEL_COST_MAP=True`：

```powershell
..\.venv\Scripts\python.exe -B -m pytest tests -q -x -p no:cacheprovider --tb=short --basetemp=.local/r52-final-validated-20261109
```

修改集中在已有 core LLM/预算与 Guidance 请求/阶段注入边界，没有替换公共 API、任务协议或求解器。无需新增兼容门面；旧共享调用方的默认重试由实际 stub 传输实验覆盖。CodeGraph 当前不可用，结论来自实际调用次数和回归，不依赖文件大小。未改前端，未重复前端构建。

## R4 原始失败离线回放

脚本：`backend/.local/run_r52_budget_validation.py`。输出：`backend/.local/20261109-090258-r52-budget-offline`。

直接读取 `backend/.local/20261108-222326-r42-review-flash/missing_activity` 的两份原始响应，不修补正文：第一份 stop、JSON非法；第二份 length、正文为空。新请求器准确记录 `syntax → truncation/reasoning_exhausted`，两次后停止，无 approved_model_ir 产物。记录用量为 prompt39071、completion57522、reasoning52078、total96593；reasoning未重复累计。离线回放零API调用，另已准备原题及漏费变体的可审查输入。

## Flash 在线实测

输出：`backend/.local/20261109-090320-r52-budget-flash`。仅调用 `https://api.deepseek.com` 的 `deepseek-flash`，范围沿用用户已授权的 R4 同范围复测：公开原件快照、拆题、已有模型/错误变体、审核提示词和JSON合同。密钥只用于认证，不发送配置或仓库全量代码。逐次保存请求、响应、用量、诊断、审核及摘要；不手修模型或响应。

原题使用1024→32768的显式实验额度。首轮1024全部用于reasoning，finish_reason=length、正文为空；第二轮正常stop，23518 completion中19276用于reasoning，生成有效审核，25项数学检查均passed、review_status=approved。两轮history完全相同，证明恢复重发原始完整合同；合计70954 tokens。原模型仍为已有费用/时点解释下的7625条件解。

漏费错误模型使用正式审核额度32768→32768，一次stop，得到有效revision_required审核，32项数学检查中4项failed：闲置保养、训练费同时计入新模块和指导者、目标及整体公式。审核准确指出指导者训练费缺失，以及虚构指导活动规避保养费。其余关于假设文字和指导者身份的意见仍按D2前置问题处理，不据此擅改公式。

| 请求 | 秒 | prompt | completion | reasoning（已含在completion） | total | finish_reason |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 原题首轮故障注入 | 8.937 | 23206 | 1024 | 1024 | 24230 | length |
| 原题恢复 | 169.219 | 23206 | 23518 | 19276 | 46724 | stop |
| 漏费错误模型 | 207.407 | 19491 | 29529 | 23715 | 49020 | stop |
| 合计 | 385.563 | 65903 | 54071 | 44015 | 119974 | 3次 |

通过既有 `backend/.local/audit_r42_replays.py` 对本批未修改的模型/解进行独立原件核算：原题passed、费用7625；漏费模型failed、求解目标1311而实际费用1623，并在1/3/4/5期出现无受训者的指导者。证据保存为各案例的 `independent_audit.json`；审核命中有独立依据。R4当时未能完成的漏费审核在本次取得有效拒绝证据，不能据一个案例声称Reviewer普遍可靠。

在线超时沿用R4实验的LLM300秒、合同310秒，之后依据观测修正standard生产时间；没有重复发请求伪装成生产整条流水线或所有模式实测。模型准备阶段的MILP计算只是独立诊断，不绕过产品审核执行错误模型。两组持久配置摘要均未改变。原始请求、响应、失败证据及实验脚本保留在忽略目录，不提交密钥或配置。

12个变更文件严格UTF-8、无BOM；core两个原混合换行文件采用局部字节替换，保留原有换行，其他文件保持LF。Python AST和diff检查通过。唯一计划更新现状摘要，R5阶段压缩为锁定不变量，唯一CURRENT移至D2前置核验。

## 现状摘要

1. 核心流：原件→可恢复分阶段建模→带预算和原因分类的数学审核→执行→分层验证。
2. 主要风险：Reviewer误判、原始证据ID与执行节点ID语义、泛化/具体验证类别重叠及同族题假设一致性仍需D2前核验。
3. 本次变化：阶段token配额、最多两次实际调用、截断拒绝及有界恢复、用量和失败分类。
4. 完成项：R5.2；连同已完成的R5.1，R5阶段出口成立。
5. 下一未完成项：D2，先复现并解决证据引用/验证类别问题，再复测假设一致性和原件整题流程。预算恢复不等于数学正确性或整题完成；D2尚未验收。
