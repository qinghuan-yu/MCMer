"""
代码解释器基类
"""
from abc import ABC, abstractmethod
from typing import Optional


class BaseCodeInterpreter(ABC):
    """代码解释器抽象基类"""

    @abstractmethod
    async def execute(self, code: str, section: str = "") -> str:
        """执行代码并返回结果"""
        ...

    @abstractmethod
    def add_section(self, title: str) -> None:
        """添加新的代码段落"""
        ...

    @abstractmethod
    async def save_notebook(self, filepath: str) -> None:
        """保存为 notebook 文件"""
        ...

    @abstractmethod
    async def close(self) -> None:
        """关闭解释器"""
        ...
