# MCMer

MCMer 是一个面向数学建模场景的多 Agent 全栈系统，支持从题目输入、数据上传、建模求解、结果复核、论文生成到历史项目回看的完整链路。

## 项目预览

![MCMer 首页预览](docs/homepage.png) 
当前项目按两条主链路运行：

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

## 后端规划

后端当前已经包含这些关键约束：

- 求解阶段限制只能使用当前环境已安装的库。
- 代码手必须输出结构化结果文件，不能只给自然语言总结。
- 写作链路已加入数值复核与更严格的最终审查。
- DOCX 导出支持公式与本地图片兜底嵌入。

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

## 运行前准备

### Python

- 建议直接使用项目根目录下的 `.venv`
- 后端依赖定义在 [backend/pyproject.toml](backend/pyproject.toml)

### Node.js

- 需要本地已安装 `pnpm`
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

每个任务都会在 `backend/project/work_dir/<task_id>/` 下生成运行产物，通常包括：

- `res.md`：论文 Markdown
- `res.docx`：导出的 Word 文档
- `res.json`：任务结果摘要
- `notebook.ipynb`：代码执行记录
- `*_structured_results.json`：结构化中间结果
- 图表、CSV、JSON 等分析文件

## 许可证

本项目使用 GPL-3.0-only，完整文本见 [LICENSE](LICENSE)。