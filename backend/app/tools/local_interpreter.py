"""
本地 Jupyter Code Interpreter
基于 jupyter-client 实现代码执行
"""
import os
import glob
import asyncio
import uuid
from typing import Optional

import nbformat
from nbformat import v4 as nbf
from jupyter_client import AsyncKernelManager

from app.tools.base_interpreter import BaseCodeInterpreter
from app.utils.log_util import logger


class LocalCodeInterpreter(BaseCodeInterpreter):
    """本地 Jupyter Kernel 代码解释器"""

    def __init__(self, work_dir: str):
        self.work_dir = work_dir
        self.kernel_manager: Optional[AsyncKernelManager] = None
        self.kernel_client = None
        self.notebook = nbf.new_notebook()
        self.current_section = ""
        self._initialized = False

    async def _ensure_kernel(self):
        """确保 Kernel 已启动"""
        if self._initialized:
            return

        self.kernel_manager = AsyncKernelManager()
        await self.kernel_manager.start_kernel()
        self.kernel_client = self.kernel_manager.client()
        self.kernel_client.start_channels()
        await self.kernel_client.wait_for_ready()

        # 设置工作目录
        await self.execute(f"import os; os.chdir(r'{self.work_dir}')", "init")
        self._initialized = True
        logger.info("Jupyter Kernel 已启动")

    def add_section(self, title: str) -> None:
        """添加 Markdown 标题段落"""
        self.current_section = title
        self.notebook.cells.append(
            nbf.new_markdown_cell(f"## {title}")
        )

    async def execute(self, code: str, section: str = "") -> str:
        """执行代码（基类接口）"""
        result, _, _ = await self.execute_code(code)
        return result

    async def execute_code(self, code: str) -> tuple[str, bool, str]:
        """执行代码，返回 (输出文本, 是否出错, 错误信息)
        
        兼容根目录 coder_agent 的调用约定。
        """
        await self._ensure_kernel()

        # 记录执行前的图片文件
        images_before = set(self._list_images())

        # 添加代码单元格
        cell = nbf.new_code_cell(code)
        self.notebook.cells.append(cell)

        try:
            msg_id = self.kernel_client.execute(code)

            outputs = []
            error_occurred = False
            error_message = ""
            while True:
                try:
                    msg = await asyncio.wait_for(
                        self.kernel_client.get_iopub_msg(), timeout=30
                    )
                except asyncio.TimeoutError:
                    outputs.append("[超时] 代码执行超过30秒")
                    return "\n".join(outputs), True, "代码执行超时"

                msg_type = msg["header"]["msg_type"]
                content = msg["content"]

                if msg_type == "stream":
                    text = content.get("text", "")
                    outputs.append(text)
                    cell.outputs.append(
                        nbf.new_output("stream", text=text,
                                       name=content.get("name", "stdout"))
                    )
                elif msg_type == "execute_result":
                    text = content.get("data", {}).get("text/plain", "")
                    outputs.append(text)
                    cell.outputs.append(
                        nbf.new_output("execute_result", data=content.get("data", {}))
                    )
                elif msg_type == "display_data":
                    cell.outputs.append(
                        nbf.new_output("display_data", data=content.get("data", {}))
                    )
                    if "image/png" in content.get("data", {}):
                        img_data = content["data"]["image/png"]
                        outputs.append("[图片已生成]")
                elif msg_type == "error":
                    traceback = "\n".join(content.get("traceback", []))
                    outputs.append(traceback)
                    error_occurred = True
                    error_message = f"{content.get('ename', 'Error')}: {content.get('evalue', '')}"
                    cell.outputs.append(
                        nbf.new_output("error", ename=content.get("ename", ""),
                                       evalue=content.get("evalue", ""),
                                       traceback=content.get("traceback", []))
                    )
                elif msg_type == "status" and content.get("execution_state") == "idle":
                    break

            result_text = "\n".join(outputs) if outputs else "代码执行完成 (无输出)"
            return result_text, error_occurred, error_message

        except Exception as e:
            error_msg = f"代码执行异常: {str(e)}"
            logger.error(error_msg)
            return error_msg, True, error_msg

    def _list_images(self) -> list[str]:
        """列出 work_dir 中所有图片文件"""
        patterns = ["*.png", "*.jpg", "*.jpeg", "*.svg", "*.pdf"]
        images = []
        for pat in patterns:
            images.extend(glob.glob(os.path.join(self.work_dir, pat)))
        return [os.path.basename(p) for p in images]

    async def get_created_images(self, section: str = "") -> list[str]:
        """返回当前 work_dir 中的图片文件名列表"""
        return self._list_images()

    async def save_notebook(self, filepath: str) -> None:
        """保存 notebook"""
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            nbformat.write(self.notebook, f)
        logger.info(f"Notebook 已保存: {filepath}")

    async def close(self) -> None:
        """关闭 Kernel"""
        if self.kernel_manager:
            try:
                await self.kernel_manager.shutdown_kernel()
            except Exception:
                pass
            self._initialized = False
            logger.info("Jupyter Kernel 已关闭")
