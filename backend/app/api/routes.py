"""REST API 路由"""
import os
import asyncio
from pathlib import Path
from typing import Optional

from starlette.background import BackgroundTask
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.services.task_service import task_manager
from app.services.config_manager import config_manager
from app.services.redis_manager import redis_manager
from app.core.workflow_budget import DEFAULT_WORKFLOW_MODE, SUPPORTED_WORKFLOW_MODES, normalize_workflow_mode
from app.services.source_ingest import (
    PAPER_SOURCE_EXTENSIONS,
    QUESTION_SOURCE_EXTENSIONS,
    SUPPLEMENTARY_EXTENSIONS,
    SourceIngestError,
    ingest_paper_source,
    ingest_question_source,
    purpose_for_upload,
)
from app.core.workflow import run_workflow
from app.artifacts.workspace_archive import (
    WorkspaceArchiveIncompleteError,
    build_workspace_archive,
)
from app.schemas.enums import TaskStatus
from app.schemas.response import (
    HistoryTaskInfo,
    PaperContent,
    RevisionRequest,
)
from app.utils.log_util import logger

router = APIRouter()


class CreateTaskRequest(BaseModel):
    question: str = ""
    task_type: str = "writing"
    workflow_mode: str = "standard"
    source_question: str = ""
    paper_content: str = ""
    polishing_requirements: str = ""
    expect_files: bool = False


class TaskResponse(BaseModel):
    task_id: str
    status: str
    task_type: str = "writing"
    workflow_mode: str = "standard"
    work_dir: str = ""


ALLOWED_EXTENSIONS = SUPPLEMENTARY_EXTENSIONS | QUESTION_SOURCE_EXTENSIONS | PAPER_SOURCE_EXTENSIONS


class ModelingCompatResponse(BaseModel):
    task_id: str
    status: str = "processing"
    message: str = "任务已创建，请通过 WebSocket 连接获取实时进度"


async def _run_workflow_background(task_id: str, question: str) -> None:
    """后台运行工作流并把消息发布到 Redis/日志。"""
    try:
        async for message in run_workflow(task_id, question):
            await redis_manager.publish_message(task_id, message)
    except Exception as e:
        await redis_manager.publish_message(
            task_id,
            {"type": "error", "message": f"后台工作流异常: {e}"},
        )


def _cancel_payload(task_id: str) -> dict:
    return {
        "type": "cancelled",
        "message": "任务已停止",
        "data": {
            "task_id": task_id,
            "status": TaskStatus.CANCELLED.value,
            "stage": "done",
            "progress": 1,
            "message": "任务已停止",
        },
    }


# ============================================================
# 任务 CRUD
# ============================================================

@router.post("/tasks", response_model=TaskResponse)
async def create_task(req: CreateTaskRequest):
    """创建新的数学建模任务"""
    task_type = (req.task_type or "writing").strip().lower()
    requested_workflow_mode = (req.workflow_mode or DEFAULT_WORKFLOW_MODE).strip().lower()
    if task_type not in {"guidance", "writing", "polish"}:
        raise HTTPException(status_code=400, detail="不支持的任务类型")
    if requested_workflow_mode not in SUPPORTED_WORKFLOW_MODES:
        raise HTTPException(status_code=400, detail="不支持的 workflow_mode")

    workflow_mode = normalize_workflow_mode(requested_workflow_mode)

    if task_type in {"guidance", "writing"} and not req.question.strip() and not req.expect_files:
        raise HTTPException(status_code=400, detail="问题不能为空")

    if task_type == "polish" and not req.paper_content.strip() and not req.expect_files:
        raise HTTPException(status_code=400, detail="润色任务需要提供论文内容")

    default_question = "论文润色任务" if task_type == "polish" else ""
    if task_type == "writing":
        task_type = "guidance"
    task_id = task_manager.create_task(
        question=req.question.strip() or default_question,
        task_type=task_type,
        workflow_mode=workflow_mode,
        source_question=req.source_question.strip(),
        paper_content=req.paper_content,
        polishing_requirements=req.polishing_requirements.strip(),
    )
    task = task_manager.get_task(task_id)

    return TaskResponse(
        task_id=task_id,
        status=task["status"],
        task_type=task.get("task_type", task_type),
        workflow_mode=task.get("workflow_mode", workflow_mode),
        work_dir=task["work_dir"],
    )


@router.post("/modeling", response_model=ModelingCompatResponse)
async def modeling_compat(
    ques_all: str = Form(...),
    comp_template: Optional[str] = Form(default="CHINA"),
    format_output: Optional[str] = Form(default="Markdown"),
    auto_start: bool = Form(default=True),
    files: list[UploadFile] = File(default=[]),
):
    """兼容旧接口：一次性提交题目和数据集（工作流由 WS 触发）。"""
    if not ques_all.strip():
        raise HTTPException(status_code=400, detail="ques_all 不能为空")

    task_id = task_manager.create_task(ques_all)
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=500, detail="任务创建失败")

    # 预保存数据集
    data_dir = os.path.join(task["work_dir"], "data")
    os.makedirs(data_dir, exist_ok=True)
    for file in files or []:
        ext = os.path.splitext(file.filename or "")[1].lower()
        if ext and ext not in ALLOWED_EXTENSIONS:
            continue
        file_path = os.path.join(data_dir, file.filename or "dataset")
        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)

    if auto_start:
        # 标记为后台运行任务
        task["auto_started"] = True
        task_manager._save_task_info(task_id, task)
        workflow_task = asyncio.create_task(_run_workflow_background(task_id, ques_all))
        task_manager.register_workflow_task(task_id, workflow_task)

    return ModelingCompatResponse(task_id=task_id)


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str):
    """获取任务状态"""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    return TaskResponse(
        task_id=task_id,
        status=task.get("status", "unknown"),
        task_type=task.get("task_type", "writing"),
        workflow_mode=task.get("workflow_mode", "standard"),
        work_dir=task.get("work_dir", ""),
    )


@router.get("/tasks/{task_id}/messages")
async def get_task_messages(task_id: str):
    """获取任务的消息日志，用于运行中和历史项目回放。"""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    entries = redis_manager.get_task_messages(task_id)
    messages = []
    for entry in entries:
        payload = entry.get("payload") if isinstance(entry, dict) else entry
        if payload:
            messages.append(payload)

    return {
        "task_id": task_id,
        "messages": messages,
        "total": len(messages),
    }


@router.get("/tasks")
async def list_active_tasks():
    """列出所有活跃任务"""
    tasks = []
    for tid, info in task_manager._active_tasks.items():
        tasks.append(
            {
                "task_id": tid,
                "status": info["status"],
                "task_type": info.get("task_type", "writing"),
                "workflow_mode": info.get("workflow_mode", "standard"),
                "question": info.get("question", "")[:100],
                "created_at": info.get("created_at", ""),
            }
        )
    return {"tasks": tasks, "total": len(tasks)}


@router.delete("/tasks/{task_id}")
async def cancel_task(task_id: str):
    """停止运行中的任务"""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    status = task.get("status", "unknown")
    if status not in {TaskStatus.RUNNING.value, TaskStatus.PENDING.value} and task_id not in task_manager._workflow_tasks:
        raise HTTPException(status_code=400, detail="任务当前不在运行中")

    await task_manager.cancel_running_task(task_id)
    await redis_manager.publish_message(task_id, _cancel_payload(task_id))
    return {"message": "任务已停止", "task_id": task_id}


# ============================================================
# 历史任务浏览
# ============================================================

@router.get("/history")
async def list_history():
    """列出所有历史任务（包括已完成和进行中的）"""
    tasks = task_manager.list_historical_tasks()
    return {"tasks": tasks, "total": len(tasks)}


@router.delete("/history/{task_id}")
async def delete_history_task(task_id: str):
    """删除历史项目及其工作目录。"""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task_manager.is_task_executing(task_id, task):
        raise HTTPException(status_code=400, detail="运行中的任务请先停止")

    work_dir = task_manager.delete_task_files(task_id)
    await redis_manager.delete_task(task_id)
    return {"message": "历史项目已删除", "task_id": task_id, "work_dir": work_dir or ""}


@router.get("/history/{task_id}", response_model=PaperContent)
async def get_task_context(task_id: str):
    """获取任务的完整上下文（题目 + 论文 + 元信息）"""
    ctx = task_manager.load_task_context(task_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="任务不存在")

    return PaperContent(
        task_id=ctx["task_id"],
        question=ctx["question"],
        primary_artifact_type=ctx["primary_artifact_type"],
        markdown_path=ctx["markdown_path"],
        paper=ctx["paper"],
        guidance=ctx["guidance"],
        latest_paper=ctx["latest_paper"],
        latest_guidance=ctx["latest_guidance"],
        paper_version=ctx["paper_version"],
        status=ctx["status"],
        created_at=ctx["created_at"],
        audit_status=ctx["audit_status"],
        audit_summary=ctx["audit_summary"],
        audit_blocks=ctx["audit_blocks"],
        verification_layers=ctx["verification_layers"],
        capability_coverage=ctx["capability_coverage"],
    )


@router.get("/history/{task_id}/paper")
async def get_paper(task_id: str, version: int = 0):
    """获取指定版本的论文内容"""
    paper = task_manager.load_paper(task_id, version)
    if not paper:
        raise HTTPException(status_code=404, detail="论文不存在")

    return {
        "task_id": task_id,
        "version": version,
        "content": paper,
    }


@router.get("/tasks/{task_id}/guidance.md")
async def download_guidance(task_id: str):
    """Download the task's primary guidance artifact as Markdown."""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    guidance_path = Path(task.get("work_dir", "")) / "guidance.md"
    if not guidance_path.is_file():
        raise HTTPException(status_code=404, detail="指导方案不存在")

    return FileResponse(
        path=guidance_path,
        media_type="text/markdown",
        filename="guidance.md",
    )


@router.get("/tasks/{task_id}/workspace.zip")
async def download_workspace(task_id: str):
    """Download the complete task workspace as a zip archive."""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    work_dir = Path(str(task.get("work_dir") or ""))
    if not work_dir.is_dir():
        raise HTTPException(status_code=404, detail="项目工作区不存在")

    try:
        archive_path = build_workspace_archive(task_id, work_dir)
    except WorkspaceArchiveIncompleteError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "missing_required_groups": list(exc.missing_required_groups),
            },
        ) from exc
    return FileResponse(
        path=archive_path,
        media_type="application/zip",
        filename=f"{task_id}-workspace.zip",
        background=BackgroundTask(lambda: archive_path.unlink(missing_ok=True)),
    )

# ============================================================
# 论文修订
# ============================================================

@router.post("/tasks/{task_id}/revise")
async def revise_paper(task_id: str, req: RevisionRequest):
    """提交论文修订请求（创建修订子任务）"""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="原任务不存在")

    paper = task_manager.load_paper(task_id)
    if not paper:
        raise HTTPException(status_code=400, detail="未找到论文，无法修订")

    # 确定修订版本号
    task_dir = task.get("work_dir", "")
    version = 1
    if task_dir:
        while os.path.exists(os.path.join(task_dir, f"res_v{version}.md")):
            version += 1

    # 创建修订子任务（feedback 存储在 question 中供工作流读取）
    revision_task_id = task_manager.create_task(
        question=task.get("question", ""),
        parent_task_id=task_id,
        revision_number=version,
        feedback=req.feedback,
        revise_code=req.revise_code,
        task_type="polish",
        workflow_mode=(req.workflow_mode or task.get("workflow_mode") or "standard"),
        source_question=task.get("source_question", task.get("question", "")),
        paper_content=paper,
        polishing_requirements=req.feedback,
    )

    return {
        "revision_task_id": revision_task_id,
        "original_task_id": task_id,
        "version": version,
        "message": "修订任务已创建，请通过 WebSocket 连接获取实时进度",
    }


# ============================================================
# 数据集上传
# ============================================================

@router.post("/tasks/{task_id}/upload")
async def upload_dataset(
    task_id: str,
    file: UploadFile = File(...),
    purpose: str = Form(default="auto"),
):
    """上传任务相关文件，并在需要时解析题目或论文源材料。"""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {ext}。支持: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    upload_purpose = purpose_for_upload(task.get("task_type", "writing"), file.filename or "", purpose)
    if upload_purpose == "question_source":
        target_dir = os.path.join(task["work_dir"], "inputs", "question")
    elif upload_purpose == "paper_source":
        target_dir = os.path.join(task["work_dir"], "inputs", "paper")
    else:
        target_dir = os.path.join(task["work_dir"], "data")

    os.makedirs(target_dir, exist_ok=True)

    filepath = os.path.join(target_dir, file.filename or ("upload" + ext))
    try:
        with open(filepath, "wb") as f:
            content = await file.read()
            f.write(content)
        file_size_kb = len(content) / 1024
        logger.info(f"数据集已保存: {filepath} ({file_size_kb:.1f} KB)")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文件保存失败: {e}")

    task_updates = {}
    if upload_purpose == "question_source":
        try:
            task_updates = ingest_question_source(filepath, task)
        except SourceIngestError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    elif upload_purpose == "paper_source":
        try:
            task_updates = ingest_paper_source(filepath, task)
        except SourceIngestError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if task_updates:
        task = task_manager.update_task_fields(task_id, **task_updates) or task

    # 列出当前所有数据文件
    data_dir = os.path.join(task["work_dir"], "data")
    files = os.listdir(data_dir) if os.path.exists(data_dir) else []
    return {
        "message": f"文件 {file.filename} 上传成功",
        "filename": file.filename,
        "path": filepath,
        "size_kb": round(file_size_kb, 1),
        "purpose": upload_purpose,
        "task_updates": task_updates,
        "data_files": files,
    }


@router.get("/tasks/{task_id}/datasets")
async def list_datasets(task_id: str):
    """列出任务的数据文件"""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    data_dir = os.path.join(task["work_dir"], "data")
    files = []
    if os.path.exists(data_dir):
        for f in sorted(os.listdir(data_dir)):
            fpath = os.path.join(data_dir, f)
            if os.path.isfile(fpath):
                size_kb = os.path.getsize(fpath) / 1024
                files.append({
                    "filename": f,
                    "size_kb": round(size_kb, 1),
                    "path": fpath,
                })

    # 读取小文件的前几行作为预览
    previews = {}
    for fi in files[:3]:  # 最多预览3个文件
        try:
            fpath = fi["path"]
            if fi["filename"].endswith((".csv", ".txt", ".tsv", ".dat")):
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    previews[fi["filename"]] = "".join(f.readline() for _ in range(5))
            elif fi["filename"].endswith(".json"):
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
                    previews[fi["filename"]] = content[:500]
        except Exception:
            pass

    return {"files": files, "total": len(files), "previews": previews}


@router.delete("/tasks/{task_id}/datasets/{filename}")
async def delete_dataset(task_id: str, filename: str):
    """删除指定数据文件"""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    filepath = os.path.join(task["work_dir"], "data", filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="文件不存在")

    os.remove(filepath)
    return {"message": f"文件 {filename} 已删除"}


# ============================================================
# 配置管理 (API Keys)
# ============================================================

@router.get("/config/keys")
async def get_api_keys():
    """获取当前 API Key 配置（脱敏显示）"""
    return config_manager.get_all_keys()


@router.post("/config/keys")
async def update_api_keys(keys: dict):
    """更新 API Key 配置"""
    if not isinstance(keys, dict):
        raise HTTPException(status_code=400, detail="请求体必须为 JSON 对象")
    config_manager.update_keys(keys)
    return {
        "message": "配置已更新",
        "keys": config_manager.get_all_keys(),
    }


@router.get("/config/models")
async def get_supported_models():
    """获取前端展示用的常用模型列表，尽量过滤明显过时或实验性条目。"""
    return {
        "models": [
            {"id": "openai/gpt-4.1", "name": "GPT-4.1", "provider": "OpenAI"},
            {"id": "openai/gpt-4.1-mini", "name": "GPT-4.1 Mini", "provider": "OpenAI"},
            {"id": "openai/o3", "name": "o3", "provider": "OpenAI"},
            {"id": "openai/o3-mini", "name": "o3 Mini", "provider": "OpenAI"},
            {"id": "anthropic/claude-sonnet-4-20250514", "name": "Claude Sonnet 4", "provider": "Anthropic"},
            {"id": "anthropic/claude-opus-4-20250514", "name": "Claude Opus 4", "provider": "Anthropic"},
            {"id": "anthropic/claude-3-7-sonnet-20250219", "name": "Claude 3.7 Sonnet", "provider": "Anthropic"},
            {"id": "deepseek/deepseek-chat", "name": "DeepSeek Chat", "provider": "DeepSeek"},
            {"id": "deepseek/deepseek-reasoner", "name": "DeepSeek Reasoner", "provider": "DeepSeek"},
            {"id": "deepseek/deepseek-v4-flash", "name": "DeepSeek V4 Flash（兼容端点）", "provider": "DeepSeek"},
            {"id": "deepseek/deepseek-v4-pro", "name": "DeepSeek V4 Pro（兼容端点）", "provider": "DeepSeek"},
            {"id": "mimo/mimo-v2.5-pro", "name": "MiMo V2.5 Pro", "provider": "MiMo"},
            {"id": "mimo/mimo-v2.5", "name": "MiMo V2.5", "provider": "MiMo"},
            {"id": "gemini/gemini-2.5-flash", "name": "Gemini 2.5 Flash", "provider": "Google"},
            {"id": "gemini/gemini-2.5-pro", "name": "Gemini 2.5 Pro", "provider": "Google"},
            {"id": "gemini/gemini-2.0-flash-lite", "name": "Gemini 2.0 Flash-Lite", "provider": "Google"},
            {"id": "ollama/llama3.1", "name": "Llama 3.1 (Ollama)", "provider": "Ollama"},
            {"id": "ollama/qwen2.5", "name": "Qwen 2.5 (Ollama)", "provider": "Ollama"},
        ]
    }


@router.get("/messages")
async def get_messages(task_id: str):
    """兼容接口：读取任务消息日志。"""
    return {
        "task_id": task_id,
        "messages": redis_manager.get_task_messages(task_id),
    }


@router.get("/status")
async def get_status(task_id: str):
    """兼容接口：查询任务状态。"""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {
        "task_id": task_id,
        "status": task.get("status", "unknown"),
        "work_dir": task.get("work_dir", ""),
    }

