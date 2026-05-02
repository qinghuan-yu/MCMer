"""
Redis 管理器 - 用于 WebSocket 消息广播和任务状态管理
支持无 Redis 时的内存回退模式
"""
import asyncio
import json
import os
from datetime import datetime
from typing import Optional

from app.config.setting import settings
from app.utils.log_util import logger


class RedisManager:
    """Redis 连接管理器 (支持内存回退)"""

    def __init__(self):
        self.redis = None
        self._use_memory = False
        self._memory_channels: dict[str, list] = {}
        self._memory_data: dict[str, dict] = {}
        self._message_log_dir = os.path.join("logs", "messages")
        os.makedirs(self._message_log_dir, exist_ok=True)

    def _append_message_log(self, channel: str, data: str) -> None:
        """将消息持久化到 logs/messages/{task_id}.json。"""
        try:
            log_path = os.path.join(self._message_log_dir, f"{channel}.json")
            entry = {
                "timestamp": datetime.now().isoformat(),
                "task_id": channel,
                "payload": json.loads(data),
            }
            if os.path.exists(log_path):
                with open(log_path, "r", encoding="utf-8") as f:
                    arr = json.load(f)
            else:
                arr = []
            arr.append(entry)
            with open(log_path, "w", encoding="utf-8") as f:
                json.dump(arr, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug(f"消息写盘失败(channel={channel}): {e}")

    def get_task_messages(self, task_id: str) -> list[dict]:
        """从消息日志读取某任务的历史消息。"""
        log_path = os.path.join(self._message_log_dir, f"{task_id}.json")
        if not os.path.exists(log_path):
            return []
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    async def connect(self) -> None:
        """连接 Redis"""
        try:
            import redis.asyncio as aioredis
            self.redis = aioredis.from_url(
                settings.REDIS_URL, decode_responses=True
            )
            await self.redis.ping()
            self._use_memory = False
            logger.info("Redis 连接成功")
        except Exception as e:
            logger.warning(f"Redis 连接失败，使用内存模式: {e}")
            self._use_memory = True
            self.redis = None

    async def disconnect(self) -> None:
        """断开连接"""
        if self.redis:
            await self.redis.close()
            self.redis = None

    async def publish_message(self, channel: str, message) -> None:
        """发布消息到频道"""
        data = json.dumps(
            message if isinstance(message, dict) else message.model_dump(),
            ensure_ascii=False,
        )
        self._append_message_log(channel, data)
        if self._use_memory:
            if channel not in self._memory_channels:
                self._memory_channels[channel] = []
            self._memory_channels[channel].append(data)
        elif self.redis:
            await self.redis.publish(channel, data)

    async def subscribe(self, channel: str):
        """订阅频道 (仅 Redis 模式支持 pub/sub)"""
        if self._use_memory:
            # 内存模式: 返回已有消息并持续轮询
            if channel in self._memory_channels:
                for msg in self._memory_channels[channel]:
                    yield msg
            # 持续轮询新消息
            last_idx = len(self._memory_channels.get(channel, []))
            while True:
                await asyncio.sleep(0.5)
                current = self._memory_channels.get(channel, [])
                while last_idx < len(current):
                    yield current[last_idx]
                    last_idx += 1
        elif self.redis:
            pubsub = self.redis.pubsub()
            await pubsub.subscribe(channel)
            try:
                async for message in pubsub.listen():
                    if message["type"] == "message":
                        yield message["data"]
            finally:
                await pubsub.unsubscribe(channel)

    async def set_task_data(self, task_id: str, key: str, value: str) -> None:
        """设置任务数据"""
        if self._use_memory:
            if task_id not in self._memory_data:
                self._memory_data[task_id] = {}
            self._memory_data[task_id][key] = value
        elif self.redis:
            await self.redis.hset(f"task:{task_id}", key, value)

    async def get_task_data(self, task_id: str, key: str) -> Optional[str]:
        """获取任务数据"""
        if self._use_memory:
            return self._memory_data.get(task_id, {}).get(key)
        elif self.redis:
            return await self.redis.hget(f"task:{task_id}", key)

    async def get_all_task_data(self, task_id: str) -> dict:
        """获取所有任务数据"""
        if self._use_memory:
            return self._memory_data.get(task_id, {})
        elif self.redis:
            return await self.redis.hgetall(f"task:{task_id}")

    async def delete_task(self, task_id: str) -> None:
        """删除任务数据"""
        log_path = os.path.join(self._message_log_dir, f"{task_id}.json")
        if os.path.exists(log_path):
            try:
                os.remove(log_path)
            except Exception as e:
                logger.warning(f"删除任务消息日志失败(task_id={task_id}): {e}")

        if self._use_memory:
            self._memory_data.pop(task_id, None)
            self._memory_channels.pop(task_id, None)
        elif self.redis:
            await self.redis.delete(f"task:{task_id}")


# 全局单例
redis_manager = RedisManager()
