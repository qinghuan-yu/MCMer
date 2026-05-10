"""
MCMer - FastAPI 主入口
"""
import os
from contextlib import asynccontextmanager

from app.utils.proxy import clear_proxy_env, should_ignore_system_proxy


if should_ignore_system_proxy():
    clear_proxy_env()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.api.ws import router as ws_router
from app.services.redis_manager import redis_manager
from app.services.config_manager import config_manager
from app.utils.log_util import logger
from app.config.setting import settings


def _clear_proxy_env_with_log() -> None:
    """清理代理变量并记录日志。"""
    cleared = clear_proxy_env()
    if cleared:
        logger.warning("已清理进程代理环境变量: %s", ", ".join(cleared))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("🚀 MCMer 启动中...")
    if settings.IGNORE_SYSTEM_PROXY:
        _clear_proxy_env_with_log()

    try:
        await redis_manager.connect()
        logger.info("✅ Redis 连接成功")
    except Exception as e:
        logger.warning(f"⚠️ Redis 连接失败 (将使用内存模式): {e}")

    # 加载运行时配置
    logger.info("📋 已加载运行时配置")
    config_manager._apply_to_env()
    if settings.IGNORE_SYSTEM_PROXY:
        _clear_proxy_env_with_log()

    # 确保工作目录存在
    os.makedirs(settings.WORK_DIR, exist_ok=True)
    os.makedirs(os.path.join(settings.WORK_DIR, "data"), exist_ok=True)

    yield

    logger.info("🛑 MCMer 关闭中...")
    try:
        await redis_manager.disconnect()
    except Exception:
        pass


app = FastAPI(
    title="MCMer",
    description="专为数学建模设计的 Multi-Agent 系统",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(api_router, prefix="/api")
app.include_router(ws_router, prefix="/ws")

# 静态文件 (输出结果)
output_dir = os.path.abspath(settings.WORK_DIR)
if os.path.exists(output_dir):
    app.mount("/output", StaticFiles(directory=output_dir), name="output")


@app.get("/")
async def root():
    return {
        "name": "MCMer",
        "version": "0.1.0",
        "status": "running",
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
