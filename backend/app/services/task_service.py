"""
任务管理服务
"""
import asyncio
import os
import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config.setting import settings
from app.core.workflow_budget import normalize_workflow_mode
from app.schemas.enums import TaskStatus
from app.schemas.response import TaskProgress
from app.services.redis_manager import redis_manager
from app.utils.common_utils import md_to_docx, normalize_math_markdown
from app.utils.log_util import logger


class TaskManager:
    """任务管理器"""

    def __init__(self):
        self._active_tasks: dict[str, dict] = {}
        self._workflow_tasks: dict[str, asyncio.Task] = {}

    def _task_work_dir(self, task_id: str) -> str:
        return os.path.join(settings.WORK_DIR, task_id)

    def create_task(
        self,
        question: str,
        parent_task_id: str = "",
        revision_number: int = 0,
        feedback: str = "",
        revise_code: bool = False,
        task_type: str = "writing",
        source_question: str = "",
        paper_content: str = "",
        polishing_requirements: str = "",
        workflow_mode: str = "",
    ) -> str:
        """创建新任务（支持作为修订子任务）"""
        task_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        work_dir = self._task_work_dir(task_id)
        os.makedirs(work_dir, exist_ok=True)
        os.makedirs(os.path.join(work_dir, "data"), exist_ok=True)
        os.makedirs(os.path.join(work_dir, "inputs"), exist_ok=True)

        task_info = {
            "task_id": task_id,
            "question": question,
            "status": TaskStatus.PENDING.value,
            "task_type": task_type,
            "workflow_mode": normalize_workflow_mode(workflow_mode or settings.WORKFLOW_MODE),
            "work_dir": work_dir,
            "created_at": datetime.now().isoformat(),
            "parent_task_id": parent_task_id,
            "revision_number": revision_number,
            "is_revision": bool(parent_task_id),
            "feedback": feedback,
            "revise_code": revise_code,
            "source_question": source_question,
            "paper_content": paper_content,
            "polishing_requirements": polishing_requirements,
            "source_question_text": "",
            "source_question_file": "",
            "source_paper_file": "",
            "source_bundle_dir": "",
            "source_markdown_file": "",
            "source_image_manifest": [],
        }
        self._active_tasks[task_id] = task_info

        # 持久化任务信息
        self._save_task_info(task_id, task_info)
        redis_manager.ensure_task_message_log(task_id)

        logger.info(f"任务已创建: {task_id}" + (" (修订)" if parent_task_id else ""))
        return task_id

    def _save_task_info(self, task_id: str, info: dict) -> None:
        """持久化任务信息到 work_dir"""
        try:
            work_dir = info.get("work_dir") or self._task_work_dir(task_id)
            os.makedirs(work_dir, exist_ok=True)
            info["work_dir"] = work_dir
            info_path = os.path.join(work_dir, "task_info.json")
            with open(info_path, "w", encoding="utf-8") as f:
                json.dump(info, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存任务信息失败: {e}")

    def _normalize_loaded_task(self, task_id: str, task: dict) -> dict:
        """将重启后遗留的运行中任务修正为失败，避免前端误判为仍在执行。"""
        actual_work_dir = self._task_work_dir(task_id)
        if task.get("work_dir") != actual_work_dir:
            task["work_dir"] = actual_work_dir
        status = str(task.get("status", "")).strip().lower()
        if task_id in self._active_tasks:
            return task
        if status not in {TaskStatus.RUNNING.value, TaskStatus.PENDING.value}:
            return task
        if not task.get("auto_started"):
            return task

        task["status"] = TaskStatus.FAILED.value
        task.setdefault("error_message", "任务在服务重启后中断，请重新发起。")
        self._save_task_info(task_id, task)
        logger.warning(f"任务 {task_id} 在服务重启后遗留为 {status}，已修正为 failed")
        return task

    def get_task(self, task_id: str) -> Optional[dict]:
        """获取任务信息（先查内存，再查磁盘）"""
        if task_id in self._active_tasks:
            return self._active_tasks[task_id]

        # 从磁盘加载历史任务
        task_dir = os.path.join(settings.WORK_DIR, task_id)
        info_path = os.path.join(task_dir, "task_info.json")
        if os.path.exists(info_path):
            try:
                with open(info_path, "r", encoding="utf-8") as f:
                    task = json.load(f)
                    return self._normalize_loaded_task(task_id, task)
            except Exception:
                pass
        return None

    def update_status(self, task_id: str, status: TaskStatus) -> None:
        """更新任务状态"""
        task = self.get_task(task_id)
        if not task:
            return

        task["status"] = status.value
        if task_id in self._active_tasks:
            self._active_tasks[task_id]["status"] = status.value
            self._save_task_info(task_id, self._active_tasks[task_id])
        else:
            self._save_task_info(task_id, task)

        logger.info(f"任务 {task_id} 状态更新: {status.value}")

    def update_task_fields(self, task_id: str, **fields) -> Optional[dict]:
        """更新任务附加字段并持久化。"""
        task = self.get_task(task_id)
        if not task:
            return None

        task.update(fields)
        if task_id in self._active_tasks:
            self._active_tasks[task_id].update(fields)
            self._save_task_info(task_id, self._active_tasks[task_id])
            return self._active_tasks[task_id]

        self._save_task_info(task_id, task)
        return task

    def register_workflow_task(self, task_id: str, workflow_task: asyncio.Task) -> None:
        """登记运行中的工作流任务，便于后续取消。"""
        self._workflow_tasks[task_id] = workflow_task

        def _cleanup(done_task: asyncio.Task) -> None:
            current = self._workflow_tasks.get(task_id)
            if current is done_task:
                self._workflow_tasks.pop(task_id, None)

        workflow_task.add_done_callback(_cleanup)

    async def cancel_running_task(self, task_id: str) -> bool:
        """取消运行中的工作流。"""
        workflow_task = self._workflow_tasks.get(task_id)
        cancelled = False

        if workflow_task and not workflow_task.done():
            workflow_task.cancel()
            try:
                await workflow_task
            except asyncio.CancelledError:
                cancelled = True
            except Exception as exc:
                logger.warning(f"取消任务 {task_id} 时发生异常: {exc}")
                cancelled = True

        self.update_status(task_id, TaskStatus.CANCELLED)
        self.cleanup(task_id)
        return cancelled or workflow_task is not None

    def delete_task_files(self, task_id: str) -> Optional[str]:
        """删除任务工作目录。"""
        task = self.get_task(task_id)
        if not task:
            return None

        work_dir = task.get("work_dir", "")
        if work_dir and os.path.exists(work_dir):
            shutil.rmtree(work_dir)

        self.cleanup(task_id)
        return work_dir

    def is_task_executing(self, task_id: str, task: Optional[dict] = None) -> bool:
        """判断任务是否真的处于可停止的执行态，而不是仅在磁盘上残留为 pending/running。"""
        workflow_task = self._workflow_tasks.get(task_id)
        if workflow_task and not workflow_task.done():
            return True

        task = task or self.get_task(task_id)
        if not task:
            return False

        status = str(task.get("status", "")).strip().lower()
        if status not in {TaskStatus.RUNNING.value, TaskStatus.PENDING.value}:
            return False

        if not task.get("auto_started"):
            return False

        return task_id in self._active_tasks

    def update_paper_path(self, task_id: str, paper_path: str) -> None:
        """更新论文路径"""
        if task_id in self._active_tasks:
            self._active_tasks[task_id]["paper_path"] = paper_path
            self._save_task_info(task_id, self._active_tasks[task_id])

    # ============================================================
    # 历史任务管理
    # ============================================================

    def list_historical_tasks(self) -> list[dict]:
        """列出所有历史任务（扫描 work_dir）"""
        tasks = []
        work_root = Path(settings.WORK_DIR)

        if not work_root.exists():
            return tasks

        for task_dir in sorted(work_root.iterdir(), reverse=True):
            if not task_dir.is_dir():
                continue

            info_path = task_dir / "task_info.json"
            if not info_path.exists():
                continue

            paper_path = task_dir / "res.md"

            task_info = {}
            try:
                with open(info_path, "r", encoding="utf-8") as f:
                    task_info = self._normalize_loaded_task(task_dir.name, json.load(f))
            except Exception:
                continue

            has_paper = paper_path.exists()
            # 检查是否有修订版本
            revisions = list(task_dir.glob("res_v*.md"))
            can_stop = self.is_task_executing(task_info.get("task_id", task_dir.name), task_info)
            result_summary = {}
            registry_path = task_dir / "result_registry.json"
            if registry_path.exists():
                try:
                    with open(registry_path, "r", encoding="utf-8") as f:
                        result_summary = (json.load(f).get("summary") or {})
                except Exception:
                    result_summary = {}

            tasks.append({
                "task_id": task_info.get("task_id", task_dir.name),
                "question": task_info.get("question", "")[:120],
                "status": task_info.get("status", "unknown"),
                "task_type": task_info.get("task_type", "writing"),
                "workflow_mode": task_info.get("workflow_mode", "standard"),
                "created_at": task_info.get("created_at", ""),
                "has_paper": has_paper,
                "has_notebook": (task_dir / "notebook.ipynb").exists(),
                "revision_count": len(revisions),
                "is_revision": task_info.get("is_revision", False),
                "parent_task_id": task_info.get("parent_task_id", ""),
                "can_stop": can_stop,
                "can_delete": not can_stop,
                "result_coverage": result_summary,
            })

        # 也加入当前活跃但可能在磁盘上的任务
        for tid, info in self._active_tasks.items():
            if not any(t["task_id"] == tid for t in tasks):
                can_stop = self.is_task_executing(tid, info)
                tasks.append({
                    "task_id": tid,
                    "question": info.get("question", "")[:120],
                    "status": info.get("status", "pending"),
                    "task_type": info.get("task_type", "writing"),
                    "workflow_mode": info.get("workflow_mode", "standard"),
                    "created_at": info.get("created_at", ""),
                    "has_paper": os.path.exists(os.path.join(info["work_dir"], "res.md")),
                    "has_notebook": os.path.exists(os.path.join(info["work_dir"], "notebook.ipynb")),
                    "revision_count": 0,
                    "is_revision": info.get("is_revision", False),
                    "parent_task_id": info.get("parent_task_id", ""),
                    "can_stop": can_stop,
                    "can_delete": not can_stop,
                    "result_coverage": {},
                })

        return tasks

    def load_paper(self, task_id: str, version: int = 0) -> Optional[str]:
        """加载论文内容"""
        task_dir = os.path.join(settings.WORK_DIR, task_id)
        if version > 0:
            paper_path = os.path.join(task_dir, f"res_v{version}.md")
        else:
            paper_path = os.path.join(task_dir, "res.md")

        if os.path.exists(paper_path):
            with open(paper_path, "r", encoding="utf-8") as f:
                return f.read()
        return None

    def load_task_context(self, task_id: str) -> Optional[dict]:
        """加载任务的完整上下文（题目 + 论文 + 元信息）"""
        task = self.get_task(task_id)
        if not task:
            return None

        paper = self.load_paper(task_id)
        paper_v2 = self.load_paper(task_id, version=2)

        return {
            "task_id": task_id,
            "question": task.get("question", ""),
            "paper": paper,
            "latest_paper": paper_v2 or paper,
            "paper_version": 2 if paper_v2 else (1 if paper else 0),
            "status": task.get("status", "unknown"),
            "task_type": task.get("task_type", "writing"),
            "workflow_mode": task.get("workflow_mode", "standard"),
            "created_at": task.get("created_at", ""),
        }

    def save_revision(
        self, task_id: str, paper_content: str, version: int
    ) -> str:
        """保存修订版论文"""
        task_dir = os.path.join(settings.WORK_DIR, task_id)
        paper_path = os.path.join(task_dir, f"res_v{version}.md")
        normalized_paper = normalize_math_markdown(paper_content)
        with open(paper_path, "w", encoding="utf-8") as f:
            f.write(normalized_paper)

        md_to_docx(paper_path, os.path.join(task_dir, f"res_v{version}.docx"))
        logger.info(f"修订版论文已保存: {paper_path}")
        return paper_path

    async def send_progress(
        self,
        task_id: str,
        stage: str,
        progress: float,
        message: str = "",
    ) -> None:
        """发送任务进度"""
        progress_msg = TaskProgress(
            task_id=task_id,
            status=self._active_tasks.get(task_id, {}).get("status", "running"),
            stage=stage,
            progress=progress,
            message=message,
        )
        await redis_manager.publish_message(task_id, progress_msg)

    def cleanup(self, task_id: str) -> None:
        """清理任务"""
        self._active_tasks.pop(task_id, None)
        self._workflow_tasks.pop(task_id, None)


# 全局单例
task_manager = TaskManager()
