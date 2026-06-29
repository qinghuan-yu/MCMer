"""
数据模型 / 枚举定义
"""
from enum import Enum


class CompTemplate(str, Enum):
    """竞赛模板"""
    CUMCM = "cumcm"       # 国赛
    MCM = "mcm"            # 美赛
    CUSTOM = "custom"      # 自定义


class FormatOutPut(str, Enum):
    """输出格式"""
    Markdown = "markdown"
    Latex = "latex"
    Word = "word"


class TaskStatus(str, Enum):
    """任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentType(str, Enum):
    """Agent 类型"""
    COORDINATOR = "coordinator"
    MODELER = "modeler"
    CODER = "coder"
    WRITER = "writer"
