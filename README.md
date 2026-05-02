# MCMer

MCMer 是一个面向数学建模工作流的多 Agent 全栈应用，提供题目输入、数据上传、任务运行跟踪、历史项目回看、论文查看与修订，以及模型与 API Key 配置能力。

## 技术栈

- 后端：FastAPI、WebSocket、Redis、LiteLLM
- 前端：Vue 3、TypeScript、Vite、pnpm
- 运行方式：前后端分离开发，支持本地运行时配置

## 目录结构

```text
backend/
  app/
  project/
  pyproject.toml
frontend/
  src/
  package.json
```

## 本地开发

### 1. 后端

- Python 环境建议使用项目根目录下的 `.venv`
- 配置文件示例见 [backend/.env.example](backend/.env.example)
- 运行配置模板见 [backend/project/runtime_config.example.json](backend/project/runtime_config.example.json)

启动后端：

```powershell
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 2. 前端

- 前端环境示例见 [frontend/.env.example](frontend/.env.example)

启动前端：

```powershell
cd frontend
pnpm install
pnpm run dev --host 0.0.0.0 --port 5173
```

## 构建检查

前端构建：

```powershell
cd frontend
pnpm run build
```

后端语法编译：

```powershell
cd backend
..\.venv\Scripts\python.exe -m compileall app
```

## 配置说明

- 不要提交真实密钥到仓库
- 本地运行时配置文件 `backend/project/runtime_config.json` 已被忽略
- 本地开发环境文件 `backend/.env.dev` 已被忽略

## 许可证

本项目使用 GPL-3.0-only，完整文本见 [LICENSE](LICENSE)。