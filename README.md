# MCMer

MCMer 是一个面向数学建模场景的实验性全栈系统，保留来源追踪、ModelIR、数值算子、验证与报告能力。

**项目于 2026-11-10 按用户决定封档，最小建模闭环未完成，开发结束。**

## 封档状态

冻结版本：`closeout-incomplete-2026-11-10`，位于 `codex/math-agent-harness` 分支。

- 最后工程验证为 923 项正式后端测试及 2 项原响应回放通过；这不是数学任务完成证明。
- 冻结的四资产派生任务经过两轮 `deepseek-flash` 实测，均为 blocked，未进入求解，正式已验证结果为 0 项。
- 两轮累计 14 次请求、311891 tokens。最后一轮因下一条约束请求的 token 预留不足而在外发前停止；不追加第三轮。
- 代码、正式测试、失败回执与验证记录继续保留；本项目没有完成通用建模或竞赛整题能力验收。

查阅[最终封档记录](docs/compute_first_guidance_plan.md)、[复测审计](docs/reviews/2026-11-10-closeout-retest.md)、[运行回执](backend/closeout/closeout_run_receipts.json)及[验证清单](backend/closeout/validation/manifest.json)。后续内容保留历史架构与使用说明，不代表本次闭环验收通过。

## 分支定位

- `codex/math-agent-harness`：本次封档分支，保存 ModelIR 重构、有限实测与最终记录。
- `main`、`agent`：保留的历史分支，本次封档未合并或改写其内容。

项目当前没有活动开发主线。

## 致谢

MCMer 的产品方向受到 MathModelAgent 等数学建模 Agent 项目的启发，感谢这些项目的早期探索。

项目围绕论文局部修改、润色复核、数值结果追踪、DOCX 导出、历史项目回看和本地工作流可控性展开设计。

## 项目预览

![MCMer 首页预览](docs/homepage.png) 

以下界面截图主要展示 `agent` 分支保留的论文工作流；`main` 复用同一前后端基础设施，但以建模指导、证据追踪和工作区导出为核心。

`agent` 分支按两条主链路运行：

1. 写作功能
   适合从原题和数据出发，自动完成题目拆解、建模、审查、算法求解、数值复核、图表生成、成文与最终审查。
2. 论文润色
   适合对已有论文做一致性审查、数据复核、图文核对和竞赛风格修订。

![MCMer 项目预览](docs/project.png) 

1. 打开项目
   可以进入历史项目，重新审核其工作流。
2. 修订论文
   针对已经完成的论文，重新提交修改观点，让agent重新审视问题并修订论文。

![MCMer 配置预览](docs/config.png) 

1. 配置api key
   配置各主流模型服务商提供的key。 
2. 选择模型
   选择agent使用的模型。

---

## 保留的 Guidance 架构

封档分支保留以下设计约束：

- 题目拆解、建模、模型审核、计算、结果复核和 Guidance 输出使用文件化阶段合同。
- 参数、数值、图表和最终结论绑定数据来源与结果证据。
- 子问题分别标记为可解或阻断；缺少附录、约束或独立观测时，记录缺失信息并保留已完成结果。
- 工作区可以整体下载，包含 Guidance、注册表、Notebook、图表和诊断文件。
- 使用 ModelIR 与注册算子执行图表达模型及计算；实际覆盖和未通过事项见封档记录。

## Agent 分支工作流

`agent` 分支的写作任务支持三种工作流模式：

- `fast`：优先速度，适合快速产出可用模型方案和核心计算流程；正文保持短稿，只保留必要公式、主要结果和风险提示。
- `standard`：默认模式，在完整性、速度和篇幅之间取中庸；会按 `solve_spec.subproblems` 拆分算法求解，每个子问题独立预算并立即落盘结构化结果。
- `strict`：重点检查数值精度、单位一致性、公式口径、复核和证据链；正文优先呈现可复核表格、误差说明和阻断说明。

算法求解阶段会优先生成 `result_registry.json`。其中 `summary` 会记录：

- `verified_count` / `blocked_count`：可写入论文的已验证结果数与阻断项数。
- `coverage_status`：`ready`、`partial` 或 `no_verified_results`。
- `coverage_ratio`：有 verified 结果的结构化结果文件占比。
- `top_blocked_reasons`：主要阻断原因，常见为工具调用次数、执行时间或缺少可复核数据。

当代码手接近工具调用上限时，会进入“最小数值交付模式”：优先把已算出的关键数值表、参数表、来源文件和未完成原因写入结构化结果文件，避免整篇论文因为某个子问题被阻断而完全缺少数值。

自动保全的中间结果不会直接等同于 `verified`。例如模型若采用 `T = K * P * d`，系统会检查 `K` 是否按 `T / (P * d)` 计算；若发现把 `P / T` 这类斜率误标为扭矩系数，会在结果注册表中阻断该项，等待重新求解或复核。

## 技术栈

- 前端：Vue 3、TypeScript、Vite、pnpm
- 后端：FastAPI、WebSocket、Redis、LiteLLM
- 代码执行：Jupyter Kernel 本地解释器
- 导出：Markdown、DOCX、Notebook

## 目录结构

```text
MCMer/
├─ backend/
│  ├─ app/
│  ├─ project/
│  ├─ pyproject.toml
│  └─ runtime_config.example.json
├─ frontend/
│  ├─ src/
│  └─ package.json
├─ .venv/
├─ start-dev.cmd
└─ README.md
```

---

## 快速启动

### 首次配置（git clone 之后）

如果你是刚通过 `git clone` 拉取本项目，建议按下面顺序完成第一次配置。

#### 1. 创建 Python 虚拟环境

本项目默认使用项目根目录下的 `.venv`，`start-dev.cmd` 也依赖这个约定。

```powershell
git clone <your-repo-url>
cd MCMer

py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -e .\backend
```

说明：

- 后端要求 Python >= 3.10，建议直接使用 Python 3.11。
- 使用 `pip install -e .\backend` 后，会按 [backend/pyproject.toml](backend/pyproject.toml) 安装后端依赖。
- 后端依赖包括 Jupyter/ipykernel、LiteLLM 和文档导出相关库。

#### 2. 安装前端依赖

```powershell
cd frontend
pnpm install
cd ..
```

说明：

- 需要本机已安装 Node.js 和 `pnpm`。
- 前端依赖量级不大，主要是 Vue 3、Vite、Pinia、KaTeX、marked 等常规依赖。

#### 3. 复制环境配置文件

后端默认读取 `backend/.env.dev`，而不是直接读取 `.env.example`。因此 clone 后建议先复制一份：

```powershell
Copy-Item .\backend\.env.example .\backend\.env.dev
```

前端如果需要单独指定后端地址，也可以复制：

```powershell
Copy-Item .\frontend\.env.example .\frontend\.env.development
```

至少建议检查这些配置项：

- `OPENAI_API_KEY` / `DEEPSEEK_API_KEY` / `ANTHROPIC_API_KEY` / `MIMO_API_KEY`
- `DEFAULT_MODEL`、`MODELER_MODEL`、`CODER_MODEL`、`WRITER_MODEL`
- `CODE_INTERPRETER`，默认是 `local`
- `REDIS_URL`，默认是 `redis://localhost:6379/0`

补充说明：

- Redis 不是强制前置。若本机没有 Redis，后端会自动退回内存模式，项目仍可启动。
- 运行时模型和 key 也可以在前端设置页中修改，本地配置会写入 `backend/.local/runtime_config.json`。

### 方式一：一键启动

Windows 下可直接双击根目录脚本：

```bat
start-dev.cmd
```

这个脚本会：

- 启动后端 `uvicorn app.main:app --port 8000`
- 启动前端 `pnpm run dev --port 5173`
- 若前端尚未安装依赖，会先自动执行 `pnpm install`

启动后可访问：

- 前端首页：`http://127.0.0.1:5173`
- 后端接口文档：`http://127.0.0.1:8000/docs`

### 方式二：手动启动

#### 1. 后端

```powershell
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

#### 2. 前端

```powershell
cd frontend
pnpm install
pnpm run dev --host 0.0.0.0 --port 5173
```

### 方式三：Docker Compose 启动

Docker Compose 用于可复现部署；日常开发推荐使用本地 `.venv + pnpm`。

#### 1. 准备配置文件

后端建议先复制一份开发环境配置：

```powershell
Copy-Item .\backend\.env.example .\backend\.env.dev
```

如果希望预先放入运行时模型与 key 配置，也可以初始化本地运行时配置目录：

```powershell
New-Item -ItemType Directory -Force .\backend\.local | Out-Null
Copy-Item .\backend\runtime_config.example.json .\backend\.local\runtime_config.json
```

说明：

- Compose 会把项目根目录下的 `work_dir`、`backend/.local` 和 `backend/logs` 挂载到容器内，方便保留任务产物、运行时配置和日志。
- Compose 内部会自动把后端 Redis 地址改为 `redis://redis:6379/0`，不需要手动改 `.env.dev`。

#### 2. 一键启动

```powershell
docker compose up --build
```

日常修改后端 Python 代码时不需要重新构建镜像。Compose 会把 `./backend` 挂载到容器内 `/app`，后端以 `uvicorn --reload` 启动；保存 `.py` 文件后容器会自动重载。只有修改 `backend/Dockerfile`、`backend/pyproject.toml` 或新增系统/ Python 依赖时，才需要重新执行 `docker compose up -d --build`。

启动后可访问：

- 前端首页：`http://127.0.0.1:5173`
- 后端接口文档：`http://127.0.0.1:8000/docs`
- 后端健康检查：`http://127.0.0.1:8000/health`

#### 3. 停止与清理

```powershell
docker compose down
```

如果要连同 Redis 持久化数据一起删除：

```powershell
docker compose down -v
```

#### 4. Docker 方式的适用范围

- 适合复现 Redis、FastAPI、Vite 前后端联动和导出链路。
- 日常开发调试推荐使用本地 `.venv` 和 `pnpm run dev`。

## 运行前准备

### Python

- 建议直接使用项目根目录下的 `.venv`
- 推荐 Python 3.11
- 后端依赖定义在 [backend/pyproject.toml](backend/pyproject.toml)

### Node.js

- 需要本地已安装 `pnpm`
- 建议 Node.js 18+
- 前端依赖定义在 [frontend/package.json](frontend/package.json)

### 配置文件

- 后端环境示例见 [backend/.env.example](backend/.env.example)
- 前端环境示例见 [frontend/.env.example](frontend/.env.example)
- 运行时模型与 key 配置模板见 [backend/runtime_config.example.json](backend/runtime_config.example.json)

实际运行时：

- 本地运行时配置会写入 `backend/.local/runtime_config.json`
- 不要提交真实密钥到仓库

## 使用流程

### 写作功能

1. 进入首页，选择“写作功能”
2. 输入题目或上传原题文档、数据文件
3. 等待任务依次经过分析、建模、求解、复核、写作与最终审查
4. 在项目页查看过程消息、论文、Notebook 与导出文件

### 论文润色

1. 进入首页，选择“论文润色”
2. 上传 Markdown、DOCX、PDF 或 zip 论文源文件
3. 等待系统执行一致性检查、数据复核、图文核对与措辞修订
4. 在项目页查看修订结果

## 构建与检查

### 前端构建

```powershell
cd frontend
pnpm run build
```

### 后端语法编译

```powershell
cd backend
..\.venv\Scripts\python.exe -m compileall app
```

## 产物说明

每个任务都会在项目根目录的 `work_dir/<task_id>/` 下生成运行产物，通常包括：

- `res.md`：论文 Markdown
- `res.docx`：导出的 Word 文档
- `res.json`：任务结果摘要
- `notebook.ipynb`：代码执行记录
- `*_structured_results.json`：结构化中间结果
- 图表、CSV、JSON 等分析文件

## 使用范围

MCMer 用于数学建模学习、科研参考与教学演示。生成的论文、图表、代码和数值结果需要人工核验，不应直接用于竞赛、课程作业、学术论文或成果申报等正式提交。

请遵守相应机构对原创性、AI 使用和署名的要求，勿将输出用于有偿代做、内容倒卖或虚假申报。

## 许可证

本项目使用 GPL-3.0-only，完整文本见 [LICENSE](LICENSE)。
