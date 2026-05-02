"""
WebSocket 路由 - 支持普通任务和修订任务
"""
import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.workflow import run_workflow, run_revision_workflow
from app.services.task_service import task_manager
from app.services.redis_manager import redis_manager
from app.schemas.enums import TaskStatus
from app.utils.log_util import logger

router = APIRouter()

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def _cancel_payload(task_id: str, is_revision: bool = False) -> dict:
    return {
        "type": "cancelled",
        "message": "修订任务已停止" if is_revision else "任务已停止",
        "data": {
            "task_id": task_id,
            "status": TaskStatus.CANCELLED.value,
            "stage": "done",
            "progress": 1,
            "message": "修订任务已停止" if is_revision else "任务已停止",
        },
    }


def _mark_task_auto_started(task_id: str, task: dict) -> None:
    """标记任务已进入后台执行，便于后续连接只做消息转发。"""
    if task.get("auto_started"):
        return

    task["auto_started"] = True
    task_manager._save_task_info(task_id, task)


def _should_relay_existing_task(task_id: str, task: dict) -> bool:
    """仅当同进程中已有执行器时，新的 WebSocket 连接才走转发模式。"""
    status = task.get("status", "pending")
    if status in TERMINAL_STATUSES:
        return False

    return bool(task.get("auto_started")) and task_id in task_manager._active_tasks


async def _safe_send_json(websocket: WebSocket, message: dict) -> bool:
    """尽力发送消息；客户端断开时返回 False，但不打断后台工作流。"""
    try:
        await websocket.send_json(message)
        return True
    except Exception:
        return False


@router.websocket("/ws/{task_id}")
async def websocket_endpoint(websocket: WebSocket, task_id: str):
    """WebSocket 连接 - 实时推送任务进度"""
    await websocket.accept()
    logger.info(f"WebSocket 连接: task_id={task_id}")

    task = task_manager.get_task(task_id)
    if not task:
        await websocket.send_json({"error": "任务不存在"})
        await websocket.close()
        return

    is_revision = task.get("is_revision", False)
    relay_only = False
    cancel_requested = False

    # 在后台启动工作流（普通 or 修订）
    if _should_relay_existing_task(task_id, task):
        relay_only = True
        workflow_task = asyncio.create_task(
            _relay_existing_task_stream(websocket, task_id)
        )
    elif is_revision:
        _mark_task_auto_started(task_id, task)
        workflow_task = asyncio.create_task(
            _run_revision_and_stream(websocket, task_id, task)
        )
    else:
        _mark_task_auto_started(task_id, task)
        workflow_task = asyncio.create_task(
            _run_and_stream(websocket, task_id, task["question"])
        )

    task_manager.register_workflow_task(task_id, workflow_task)

    try:
        while not workflow_task.done():
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(), timeout=1.0
                )
                msg = json.loads(data)
                if msg.get("action") == "cancel":
                    cancel_requested = True
                    workflow_task.cancel()
                    await websocket.send_json(
                        {"type": "cancelled", "message": "任务已取消"}
                    )
                    break
            except asyncio.TimeoutError:
                pass
            except WebSocketDisconnect:
                logger.info(f"WebSocket 断开: task_id={task_id}")
                break

    except Exception as e:
        logger.error(f"WebSocket 错误: {e}")
    finally:
        if (relay_only or cancel_requested) and not workflow_task.done():
            workflow_task.cancel()
        try:
            await websocket.close()
        except Exception:
            pass


@router.websocket("/task/{task_id}")
async def websocket_compat_endpoint(websocket: WebSocket, task_id: str):
    """兼容旧接口：/task/{task_id}"""
    await websocket_endpoint(websocket, task_id)


async def _run_and_stream(websocket: WebSocket, task_id: str, question: str):
    """运行普通建模工作流"""
    client_connected = True
    try:
        async for message in run_workflow(task_id, question):
            await redis_manager.publish_message(task_id, message)
            if client_connected:
                client_connected = await _safe_send_json(websocket, message)
    except asyncio.CancelledError:
        task_manager.update_status(task_id, TaskStatus.CANCELLED)
        payload = _cancel_payload(task_id)
        await redis_manager.publish_message(task_id, payload)
        if client_connected:
            await _safe_send_json(websocket, payload)
    except Exception as e:
        logger.error(f"工作流执行失败: {e}")
        error_payload = {"type": "error", "message": str(e)}
        await redis_manager.publish_message(task_id, error_payload)
        if client_connected:
            await _safe_send_json(websocket, error_payload)
    finally:
        task_manager.cleanup(task_id)


async def _run_revision_and_stream(
    websocket: WebSocket, task_id: str, task: dict
):
    """运行论文修订工作流"""
    client_connected = True
    try:
        async for message in run_revision_workflow(task_id, task):
            await redis_manager.publish_message(task_id, message)
            if client_connected:
                client_connected = await _safe_send_json(websocket, message)
    except asyncio.CancelledError:
        task_manager.update_status(task_id, TaskStatus.CANCELLED)
        payload = _cancel_payload(task_id, is_revision=True)
        await redis_manager.publish_message(task_id, payload)
        if client_connected:
            await _safe_send_json(websocket, payload)
    except Exception as e:
        logger.error(f"修订工作流执行失败: {e}")
        error_payload = {"type": "error", "message": str(e)}
        await redis_manager.publish_message(task_id, error_payload)
        if client_connected:
            await _safe_send_json(websocket, error_payload)
    finally:
        task_manager.cleanup(task_id)


async def _relay_existing_task_stream(websocket: WebSocket, task_id: str):
    """转发已在后台运行的任务消息。"""
    try:
        async for payload in redis_manager.subscribe(task_id):
            if isinstance(payload, str):
                try:
                    msg = json.loads(payload)
                except Exception:
                    msg = {"type": "message", "data": payload}
            else:
                msg = payload

            await websocket.send_json(msg)

            # 遇到终态时退出转发
            msg_type = msg.get("type") if isinstance(msg, dict) else None
            stage = (msg.get("data") or {}).get("stage") if isinstance(msg, dict) else None
            if msg_type in {"result", "error", "cancelled"} or stage == "done":
                break
    except Exception as e:
        logger.error(f"任务流转发失败: {e}")
