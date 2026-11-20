# MCMer 数学建模工作台

MCMer 把一道题从原文带到可以复核的数值答案。输入题面和数据后，模型在 DeepSeek Harness 中定义变量、目标和约束。MCMer 核对来源，独立审核模型，调用已注册的求解器，然后把经过验证的结果写入建模指导报告。某一问缺少数据或计算能力时，报告会指出缺什么，以及补齐后可以继续求什么。

## 启动工作台

在仓库根目录选择一种启动方式。

### Docker Compose

安装 Docker 并启动 Docker 引擎。首次检出时，从示例创建本地配置。以下命令可在 PowerShell 中运行。

```powershell
if (!(Test-Path backend/.env.dev)) { Copy-Item backend/.env.example backend/.env.dev }
docker compose up -d --build
```

镜像包含 Python、Node.js 和锁定版本的 DeepSeek Harness 组件。Compose 同时启动前端、后端和 Redis。进入 [工作台首页](http://127.0.0.1:5173)，后端健康状态可在 [健康检查](http://127.0.0.1:8000/health) 查看。用 `docker compose down` 停止服务。

### Windows 本地运行

准备 Python 3.11、Node.js 22.12 或更新版本，以及 pnpm。在 PowerShell 中运行以下命令。

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .\backend
npm ci --ignore-scripts --prefix backend/dsh
pnpm --dir frontend install --frozen-lockfile
if (!(Test-Path backend/.env.dev)) { Copy-Item backend/.env.example backend/.env.dev }
.\start-dev.cmd
```

启动脚本分别打开前后端终端。前端使用 5173 端口，本地后端使用 **8010** 端口，Vite 将页面请求转发到该端口。分别在两个终端按 Ctrl+C 即可停止。[工作台首页](http://127.0.0.1:5173) · [本地后端接口文档](http://127.0.0.1:8010/docs)

## 配置模型

1. 打开顶部的 **设置**，在对应服务商的输入框填入 API Key，点击 **保存 API Key**。
2. 在 **默认模型选择** 中选择与密钥匹配且支持工具调用的模型，点击 **保存模型设置**。首次使用可以让拆题、建模和审核共用这个模型。
3. 若要为拆题和建模指定不同模型，`COORDINATOR_MODEL` 用于拆题，`MODELER_MODEL` 用于建模、来源裁决和数学审核。模型选择顺序依次为运行时的专用模型、运行时的默认模型、环境中的专用模型、环境中的默认模型。网页保存的配置位于 `backend/.local/runtime_config.json`。[环境配置示例](backend/.env.example) · [运行时配置示例](backend/runtime_config.example.json)

项目实测使用的模型标识是 `deepseek/deepseek-flash`。要复用这一配置，可在首次启动前将 `backend/.env.dev` 中的 `COORDINATOR_MODEL` 和 `MODELER_MODEL` 都设为该标识，并填写 `DEEPSEEK_API_KEY`。已有网页模型配置时，按上面的优先顺序选择生效值。提交任务会向所选服务商发送题面、相关数据和工具调用上下文。

## 跑第一道题

1. 在 **首页 → 方案生成** 保留默认的 **standard：标准模式**。
2. 在题面框中直接粘贴文字，或在 **上传数据** 中放入 TXT、Markdown、含可提取文字的 PDF 或 DOCX。仅粘贴时，工作台会保存 UTF-8 原件，并为各段建立引用。同时上传原题时，系统以选中的上传原件登记题面事实，将另贴的文字保存为补充要求。CSV 或 XLSX 数据表可以一并上传。
3. 点击 **生成方案**。第一次可以直接上传仓库里的整数规划题。它给出两个非负整数变量，要求在两条约束下最大化目标函数，报告变量值和最优值。[示例题面](backend/tests/fixtures/ortools_simple_mip/question.txt)
4. 在运行页查看进度。默认流程按交付目标拆题，登记原文和表格来源。模型可检查草稿、查询算子、修改变量及约束，然后提交模型。工作台会在提交时指出未绑定的事实或无效的答案证据，并允许模型根据反馈修正。独立审核通过后，后端编译模型、执行算子、复核数值并生成报告。

## 查看结果与导出

运行页按 **执行正确性、证据可追溯性、模型充分性** 展示检查结果。点 **下载 MD** 获取报告，对应接口为 `GET /api/tasks/{task_id}/guidance.md`。以后可以从 **项目 → 打开项目** 回看进度和诊断，或选 **查看方案** 阅读报告。

**下载工作区 ZIP** 对应 `GET /api/tasks/{task_id}/workspace.zip`。无图任务也可下载，包内的 `workspace_manifest.json` 列出各文件的路径、大小与 SHA-256。若已登记的图表或必要的执行材料缺失，接口会返回 HTTP 409，并在 `missing_required_groups` 中列出缺项。

| 任务状态 | 怎样读报告 |
| --- | --- |
| `completed` | 所请求结果已通过发布复核。在答案表中查看数值、单位和来源。 |
| `partial` | 逐题查看已验证的答案，以及其他子题的缺失条件。 |
| `blocked` | 查看阻断位置、缺失输入和恢复动作。任务即使生成了诊断报告，仍可能有零项可发布结果。 |

同一次运行的工作区保存在 `work_dir/<任务 ID>/`（使用上述配置时）。可按以下顺序复查一个数值。

| 要核对什么 | 工作区文件 |
| --- | --- |
| 题面给了什么，原文和数据在哪里 | `input_catalog.json`、`evidence_profile.json`、`problem_spec.json` |
| 模型用了哪些变量、假设、目标和约束，审核通过了哪些子题 | `model_ir.json`、`model_review.json`、`approved_model_ir.json` |
| 哪些计算真正执行，数值对应哪个结果 | `execution_manifest.json`、`result_registry.json`，以及运行时生成的 `notebook.ipynb` |
| 为什么可以发布，下载文本是否与复核版本一致 | `verification_report.json`、`guidance_audit_report.json`、`guidance.md`。审计回执含 `publication_status` 和报告 SHA-256 |

如果求解器给出了候选值，先看 `result_registry.json` 的 `verified_results` 是否登记该答案，再核对报告中的数值。阻断任务也会出具审计回执，`publication_status` 表明该报告公布的是已验证答案还是阻断原因。

要复查“两种方法比较过吗”，可在 `verification_report.json` 中查看 `model_adequate.numerical_validation.baseline_comparisons`。每条实际比较记录给出指标、基线值、模型值及其结果引用。只有候选名称、缺少针对该候选的比较证据时，记录为 `not_assessed`。

## 离线复现

从完成上述本地依赖安装的仓库根目录运行以下命令。测试使用脚本提供模型回复，实际执行官方工具循环、求解器、验证和报告导出，无需配置 API Key。

```powershell
.\.venv\Scripts\python.exe -m pip install pytest pytest-asyncio jsonschema
Set-Location backend
$env:LITELLM_LOCAL_MODEL_COST_MAP = 'True'
..\.venv\Scripts\python.exe -X utf8 -m pytest -q -p no:cacheprovider tests/test_scipy_milp_guidance.py tests/test_workbench_guidance_controls.py tests/test_portfolio_harness_loop.py tests/test_anscombe_linear_fit.py tests/test_anscombe_recorded_actions.py
```

SciPy 例题用穷举核对最优解，`(1, 2)` 和 `(2, 2)` 都使目标 y 达到 2。成对测试先去掉资源上限，验证求解前会报告缺少哪项输入，再补上上限或更正证据引用，检查工作台能否发布正确答案。四资产例题把求解结果与独立枚举的 81 种收费情形进行比较。[整数规划测试](backend/tests/test_scipy_milp_guidance.py) · [修订与阻断测试](backend/tests/test_workbench_guidance_controls.py) · [四资产测试](backend/tests/test_portfolio_harness_loop.py)

## 验收记录

在 Google OR-Tools 公开整数规划题上，DeepSeek Flash 通过修复后的工作台完成了从题面到报告的真实运行。求解结果为 **x = 3、y = 2、目标值 23**，独立枚举确认这是唯一最优解。任务状态为 `completed`，发布状态为 `verified`，五条答案记录引用同一个解。该轮共 9 次模型请求、81,320 tokens，用时 87.875 秒。[复测题面](backend/tests/fixtures/ortools_simple_mip/question.txt) · [实际导出的报告](backend/tests/fixtures/ortools_simple_mip/verified_guidance.md) · [复测回执](backend/tests/fixtures/ortools_simple_mip/live_retest_receipt.json)

同一题的首次实测也算出目标值 23，但四项答案引用了执行清单中不存在的证据名，最终为 `blocked`，正式发布 0 项。这次失败促使工作台在模型提交时反馈无效引用。修复后的全套离线回归为 **988 项通过，0 项跳过**，其中 2 项历史响应回放依赖本机留存材料。[首次实测回执](backend/tests/fixtures/ortools_simple_mip/live_receipt.json) · [验收与历史记录](docs/compute_first_guidance_plan.md)

原始示例报告保留了当时的文字，其中将连续松弛称为最优性依据。该次执行记录实际给出了整数求解器的原始界和对偶界，二者都是 23，相对间隙为 0。当前报告根据执行记录展示这些值，并将模型拟议的方法标明来源。独立穷举属于验收时的外部核算。

**Anscombe 第一组公开数据的一元线性回归。** 在版本 `4315ee0`，DeepSeek Flash 为 11 对观测提交了带截距的最小二乘模型，并通过数学审核。编译时，工作台把表和拟合结果误判为矩阵和标量，求解未执行，报告为 `blocked`。这轮耗用 8 次请求、129,718 tokens、167.281 秒。原版本随后离线重放了全部 8 条响应，重现了相同的报告。[题面](backend/tests/fixtures/anscombe_linear_fit/question.txt) · [原始工作簿](backend/tests/fixtures/anscombe_linear_fit/observations.xlsx) · [实测回执](backend/tests/fixtures/anscombe_linear_fit/live_receipt.json)

修复结构化输出后，用同一批模型动作走默认流程，再从原始已审核模型直接复算，两条离线路径都发布了相同的报告。拟合直线为 **y₁ = 3.0000909091 + 0.5000909091 x₁**，11 行拟合值和两个系数均经独立核对。报告中的截距、斜率及两者汇总共享同一份拟合证据。修复后的建模上下文有所变化，8 个历史请求中有 3 个与新请求完全一致。该修复的验收使用离线重算。[实际导出报告](backend/tests/fixtures/anscombe_linear_fit/offline_verified_guidance.md) · [双路径验收回执](backend/tests/fixtures/anscombe_linear_fit/offline_repair_receipt.json)

本次发布前，后端离线回归 **1045 项通过，0 项跳过**，其中两项使用本地历史回放材料，前端构建通过。公开题的端到端证据目前覆盖一个真实跑通的整数规划例题，以及一个用原始模型离线修复后跑通的回归例题。其他题型的算子测试不能据此推算整题成功率。

## 项目与许可证

`main` 为数学建模工作台。原主分支保存为 [main-before-modeling-workbench-20261120](https://github.com/qinghuan-yu/MCMer/tree/main-before-modeling-workbench-20261120)。感谢 MathModelAgent 对本项目早期方向的启发。

MCMer 采用 GPL-3.0-only 许可证。[许可证全文](LICENSE)
