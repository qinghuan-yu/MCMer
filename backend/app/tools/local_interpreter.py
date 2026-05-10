"""
本地 Jupyter Code Interpreter
基于 jupyter-client 实现代码执行
"""
import os
import glob
import asyncio
import ast
import importlib.util
import re
from typing import Optional

import nbformat
from nbformat import v4 as nbf
from jupyter_client import AsyncKernelManager

from app.tools.base_interpreter import BaseCodeInterpreter
from app.utils.log_util import logger


class LocalCodeInterpreter(BaseCodeInterpreter):
    """本地 Jupyter Kernel 代码解释器"""

    _MATPLOTLIB_FONT_LIST = "['WenQuanYi Micro Hei', 'WenQuanYi Zen Hei', 'Noto Sans CJK SC', 'SimHei', 'Microsoft YaHei', 'PingFang SC', 'Arial Unicode MS', 'DejaVu Sans']"
    _MATPLOTLIB_FONT_SETUP = f"""\
try:
    import matplotlib
    from matplotlib import font_manager
    font_manager._load_fontmanager(try_read_cache=False)
    matplotlib.rcParams['font.family'] = 'sans-serif'
    matplotlib.rcParams['font.sans-serif'] = {_MATPLOTLIB_FONT_LIST}
    matplotlib.rcParams['axes.unicode_minus'] = False
except Exception:
    pass
"""

    def __init__(self, work_dir: str):
        self.work_dir = work_dir
        self.kernel_manager: Optional[AsyncKernelManager] = None
        self.kernel_client = None
        self.notebook = nbf.new_notebook()
        self.current_section = ""
        self._initialized = False

    @staticmethod
    def _kernel_bootstrap_code(work_dir: str) -> str:
        return f"""
import os
os.chdir(r'''{work_dir}''')

try:
    import matplotlib
    from matplotlib import font_manager
    # 清除字体缓存，确保能识别容器中后安装的中文字体
    font_manager._load_fontmanager(try_read_cache=False)
    matplotlib.rcParams['font.family'] = 'sans-serif'
    matplotlib.rcParams['font.sans-serif'] = [
        'WenQuanYi Micro Hei',
        'WenQuanYi Zen Hei',
        'Noto Sans CJK SC',
        'SimHei',
        'Microsoft YaHei',
        'PingFang SC',
        'Arial Unicode MS',
        'DejaVu Sans',
    ]
    matplotlib.rcParams['axes.unicode_minus'] = False
except Exception:
    pass
""".strip()

    async def _run_kernel_code(self, code: str, timeout: int = 30) -> tuple[str, bool, str]:
        """在已启动的 kernel 中直接执行代码，避免初始化阶段递归进入 execute_code。"""
        msg_id = self.kernel_client.execute(code)

        outputs: list[str] = []
        error_occurred = False
        error_message = ""

        while True:
          try:
              msg = await asyncio.wait_for(
                  self.kernel_client.get_iopub_msg(), timeout=timeout
              )
          except asyncio.TimeoutError:
              return "\n".join(outputs), True, "代码执行超时"

          if msg.get("parent_header", {}).get("msg_id") != msg_id:
              continue

          msg_type = msg["header"]["msg_type"]
          content = msg["content"]

          if msg_type == "stream":
              outputs.append(content.get("text", ""))
          elif msg_type == "execute_result":
              outputs.append(content.get("data", {}).get("text/plain", ""))
          elif msg_type == "error":
              outputs.append("\n".join(content.get("traceback", [])))
              error_occurred = True
              error_message = f"{content.get('ename', 'Error')}: {content.get('evalue', '')}"
          elif msg_type == "status" and content.get("execution_state") == "idle":
              break

        return "\n".join(outputs), error_occurred, error_message

    async def _ensure_kernel(self):
        """确保 Kernel 已启动"""
        if self._initialized:
            return

        self.kernel_manager = AsyncKernelManager()
        await self.kernel_manager.start_kernel()
        self.kernel_client = self.kernel_manager.client()
        self.kernel_client.start_channels()
        await self.kernel_client.wait_for_ready()
        self._initialized = True

        # 设置工作目录并补齐 Matplotlib 中文字体配置
        init_result, init_error, init_error_message = await self._run_kernel_code(
            self._kernel_bootstrap_code(self.work_dir)
        )
        if init_error:
            self._initialized = False
            raise RuntimeError(init_error_message or init_result or "Kernel 初始化失败")
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

    @classmethod
    def _normalize_matplotlib_font_settings(cls, code: str) -> str:
        """把模型生成的字体配置重写为容器内可用的中文字体优先级。"""
        matplotlib_tokens = (
            "matplotlib",
            "plt.",
            "seaborn",
            "sns.",
        )
        if not any(token in code for token in matplotlib_tokens):
            return code

        normalized = code
        rcparams_owner = r"((?:plt|matplotlib)\.rcParams)"
        safe_replacements = [
            (
                rf"{rcparams_owner}\[['\"]font\.sans-serif['\"]\]\s*=\s*\[[^\]]*\]",
                lambda match: f"{match.group(1)}['font.sans-serif'] = {cls._MATPLOTLIB_FONT_LIST}",
            ),
            (
                rf"{rcparams_owner}\[['\"]font\.family['\"]\]\s*=\s*[^\n]+",
                lambda match: f"{match.group(1)}['font.family'] = 'sans-serif'",
            ),
            (
                rf"{rcparams_owner}\[['\"]axes\.unicode_minus['\"]\]\s*=\s*[^\n]+",
                lambda match: f"{match.group(1)}['axes.unicode_minus'] = False",
            ),
        ]

        for pattern, replacement in safe_replacements:
            normalized = re.sub(pattern, replacement, normalized)

        font_setup = cls._MATPLOTLIB_FONT_SETUP.strip()
        plot_call = re.search(
            r"^(\s*)(?:[A-Za-z_][\w\s,]*=\s*)?"
            r"(?:plt\.(?:figure|subplots|subplot|plot|scatter|bar|barh|hist)|"
            r"sns\.(?:lineplot|scatterplot|barplot|histplot|heatmap|boxplot|violinplot|regplot|relplot|catplot))\(",
            normalized,
            flags=re.MULTILINE,
        )
        if plot_call and not plot_call.group(1):
            normalized = (
                normalized[:plot_call.start()]
                + font_setup
                + "\n"
                + normalized[plot_call.start():]
            )
        else:
            normalized = font_setup + "\n" + normalized

        return normalized

    async def execute_code(self, code: str) -> tuple[str, bool, str]:
        """执行代码，返回 (输出文本, 是否出错, 错误信息)
        
        兼容根目录 coder_agent 的调用约定。
        """
        await self._ensure_kernel()

        code = self._normalize_matplotlib_font_settings(code)

        unavailable_imports = self._find_unavailable_imports(code)
        if unavailable_imports:
            modules = ", ".join(unavailable_imports)
            error_message = (
                "检测到未安装的第三方库导入: "
                f"{modules}。请改用当前环境已安装的库，或使用标准库/已存在结果文件完成任务。"
            )
            logger.warning(error_message)
            return error_message, True, error_message

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

                if msg.get("parent_header", {}).get("msg_id") != msg_id:
                    continue

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

    @staticmethod
    def _find_unavailable_imports(code: str) -> list[str]:
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return []

        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_level = alias.name.split(".", 1)[0]
                    if top_level:
                        imports.add(top_level)
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    continue
                if node.module:
                    top_level = node.module.split(".", 1)[0]
                    if top_level:
                        imports.add(top_level)

        unavailable = []
        for module_name in sorted(imports):
            if importlib.util.find_spec(module_name) is None:
                unavailable.append(module_name)
        return unavailable

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
