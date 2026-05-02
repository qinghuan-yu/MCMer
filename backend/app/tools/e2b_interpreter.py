"""
E2B 云端 Code Interpreter
https://e2b.dev/
"""
from typing import Optional

from app.tools.base_interpreter import BaseCodeInterpreter
from app.utils.log_util import logger


class E2BCodeInterpreter(BaseCodeInterpreter):
    """E2B 云端代码解释器"""

    def __init__(self, api_key: str, work_dir: str):
        self.api_key = api_key
        self.work_dir = work_dir
        self.sandbox = None
        self.current_section = ""

    async def _ensure_sandbox(self):
        """确保沙箱已启动"""
        if self.sandbox is not None:
            return
        try:
            from e2b import AsyncSandbox
            self.sandbox = await AsyncSandbox.create(api_key=self.api_key)
            logger.info("E2B 沙箱已启动")
        except ImportError:
            raise ImportError("请安装 e2b: pip install e2b")
        except Exception as e:
            raise RuntimeError(f"E2B 沙箱启动失败: {e}")

    def add_section(self, title: str) -> None:
        self.current_section = title

    async def execute(self, code: str, section: str = "") -> str:
        await self._ensure_sandbox()
        try:
            result = await self.sandbox.run_code(code)
            if result.error:
                return f"错误: {result.error}"
            return "\n".join([str(o) for o in result.logs.stdout]) if result.logs else "执行完成"
        except Exception as e:
            return f"E2B 执行错误: {str(e)}"

    async def save_notebook(self, filepath: str) -> None:
        logger.info("E2B 模式下 notebook 保存在云端")

    async def close(self) -> None:
        if self.sandbox:
            try:
                await self.sandbox.close()
            except Exception:
                pass
            self.sandbox = None
            logger.info("E2B 沙箱已关闭")
