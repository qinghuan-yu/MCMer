"""
Agent-to-Agent 通信数据模型
"""
from typing import Optional
from pydantic import BaseModel


class SubQuestion(BaseModel):
    """子问题"""
    id: int
    title: str
    description: str
    keywords: list[str] = []


class Questions(BaseModel):
    """问题集"""
    background: str = ""
    sub_questions: list[SubQuestion] = []


class CoordinatorToModeler(BaseModel):
    """协调手 -> 建模手"""
    questions: Questions
    ques_count: int


class ModelerToCoder(BaseModel):
    """建模手 -> 代码手"""
    questions_solution: dict  # key: "eda", "ques1", ..., "sensitivity_analysis" ; value: 建模方案文字描述


class Citation(BaseModel):
    """文献引用"""
    title: str
    authors: str = ""
    year: str = ""
    doi: str = ""
    url: str = ""


class CoderToWriter(BaseModel):
    """代码手 -> 写作手"""
    coder_response: str | None = None       # LLM 最后的文字输出（含对结果的文字描述）
    created_images: list[str] = []           # 本次执行新生成的图片文件名列表


class WriterResponse(BaseModel):
    """写作手响应"""
    response_content: str                    # 论文正文（Markdown）
    footnotes: list = []                     # 文献引用脚注
