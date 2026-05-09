"""
应用配置管理
"""
import os
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# 加载 .env 文件
env_file = os.getenv("ENV", "DEV")
env_path = Path(__file__).parent.parent.parent / f".env.{env_file.lower()}"
if env_path.exists():
    load_dotenv(env_path)
else:
    load_dotenv(Path(__file__).parent.parent.parent / ".env.dev")


class Settings(BaseSettings):
    """全局配置"""

    # --- LLM ---
    DEFAULT_MODEL: str = "openai/gpt-4.1"
    FALLBACK_MODEL: str = "openai/gpt-4.1-mini"

    COORDINATOR_MODEL: Optional[str] = None
    MODELER_MODEL: Optional[str] = None
    CODER_MODEL: Optional[str] = None
    WRITER_MODEL: Optional[str] = None

    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = ""
    ANTHROPIC_API_KEY: str = ""
    DEEPSEEK_API_KEY: str = ""
    LLM_PROXY_URL: str = ""

    # --- MiMo ---
    MIMO_API_KEY: str = ""
    MIMO_BASE_URL: str = "https://api.xiaomimimo.com/v1"

    # --- Code Interpreter ---
    CODE_INTERPRETER: str = "local"
    E2B_API_KEY: str = ""
    DAYTONA_API_KEY: str = ""

    # --- Redis ---
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- Service ---
    ENV: str = "DEV"
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # --- Agent ---
    MAX_CHAT_TURNS: int = 30
    MAX_RETRIES: int = 5
    MAX_MEMORY: int = 12
    LLM_REQUEST_TIMEOUT: int = 120
    WORKFLOW_MODE: str = "standard"
    CODER_MAX_TOTAL_TOOL_CALLS: int = 8
    CODER_MAX_WALL_SECONDS: int = 180
    SOLVE_CODER_MAX_TOTAL_TOOL_CALLS: int = 16
    SOLVE_CODER_MAX_WALL_SECONDS: int = 420

    # --- Work Dir ---
    WORK_DIR: str = "./project/work_dir"

    # --- Log ---
    LOG_LEVEL: str = "INFO"
    IGNORE_SYSTEM_PROXY: bool = True

    model_config = {"env_file": str(env_path), "extra": "allow"}


settings = Settings()
