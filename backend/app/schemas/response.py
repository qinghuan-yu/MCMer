"""
响应消息模型
"""
from typing import Optional, Any
from pydantic import BaseModel


class SystemMessage(BaseModel):
    """系统消息 (通过 WebSocket 推送)"""
    content: str
    type: str = "info"  # info, warning, error, success
    agent: str = "system"
    data: Optional[Any] = None


class InterpreterMessage(BaseModel):
    """代码解释器消息"""
    type: str  # code, result, error, image
    content: str
    agent: str = "coder"
    section: str = ""


class WriterMessage(BaseModel):
    """写作手消息"""
    type: str  # text, citation, section_complete
    content: str
    agent: str = "writer"
    section: str = ""


class TaskProgress(BaseModel):
    """任务进度"""
    task_id: str
    status: str
    stage: str  # coordinator, modeling, coding, writing, done, revision
    progress: float = 0.0  # 0.0 - 1.0
    message: str = ""
    current_subtask: str = ""


class TaskResult(BaseModel):
    """任务结果"""
    task_id: str
    status: str
    paper_path: str = ""
    notebook_path: str = ""
    work_dir: str = ""
    error_message: str = ""


# ============================================================
# 历史 & 修订相关模型
# ============================================================

class HistoryTaskInfo(BaseModel):
    """历史任务摘要"""
    task_id: str
    question: str = ""
    status: str = "unknown"
    created_at: str = ""
    has_paper: bool = False
    has_notebook: bool = False
    revision_count: int = 0
    is_revision: bool = False
    parent_task_id: str = ""


class PaperContent(BaseModel):
    """论文内容"""
    task_id: str
    question: str = ""
    paper: Optional[str] = None
    latest_paper: Optional[str] = None
    paper_version: int = 0
    status: str = "unknown"
    created_at: str = ""


class RevisionRequest(BaseModel):
    """用户修订请求"""
    task_id: str
    feedback: str  # 用户的修改建议
    revise_code: bool = False  # 是否需要修改代码
    sections: Optional[list[str]] = None  # 指定修订的章节


class RevisionResult(BaseModel):
    """修订结果"""
    task_id: str
    original_task_id: str
    paper_content: str = ""
    paper_path: str = ""
    version: int = 1
    status: str = "completed"
