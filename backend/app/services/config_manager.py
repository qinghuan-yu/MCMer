"""
运行时配置管理 - 支持前端动态修改 API Keys 等配置
"""
import os
import json
from pathlib import Path
from typing import Optional

from app.utils.log_util import logger


class ConfigManager:
    """运行时配置管理器 - 存储 API Keys 到本地 JSON 文件"""

    BASE_DIR = Path(__file__).resolve().parent.parent.parent
    CONFIG_FILE = BASE_DIR / ".local" / "runtime_config.json"
    LEGACY_CONFIG_FILE = BASE_DIR / "project" / "runtime_config.json"

    def __init__(self):
        self._config: dict = {}
        self._load()

    def _load(self) -> None:
        """从文件加载配置"""
        try:
            self._migrate_legacy_config()
            if self.CONFIG_FILE.exists():
                with open(self.CONFIG_FILE, "r", encoding="utf-8") as f:
                    self._config = json.load(f)
                logger.info(f"已加载运行时配置: {len(self._config)} 项")
                self._apply_to_env()
        except Exception as e:
            logger.warning(f"加载运行时配置失败: {e}")
            self._config = {}

    def _save(self) -> None:
        """保存配置到文件"""
        try:
            self.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(self.CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self._config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存运行时配置失败: {e}")

    def _migrate_legacy_config(self) -> None:
        """兼容旧位置的本地运行时配置。"""
        if self.CONFIG_FILE.exists() or not self.LEGACY_CONFIG_FILE.exists():
            return

        try:
            self.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            self.LEGACY_CONFIG_FILE.replace(self.CONFIG_FILE)
            logger.info("已迁移运行时配置到本地目录: %s", self.CONFIG_FILE)
        except Exception as e:
            logger.warning(f"迁移旧运行时配置失败: {e}")

    def get_all_keys(self) -> dict:
        """获取所有 API Key 配置（脱敏显示）"""
        masked = {}
        for k, v in self._config.items():
            if self._should_mask(k, v):
                val = str(v)
                if len(val) > 8:
                    masked[k] = val[:4] + "****" + val[-4:]
                else:
                    masked[k] = "****"
            else:
                masked[k] = v or ""
        return masked

    @staticmethod
    def _should_mask(key: str, value: object) -> bool:
        """仅对敏感凭据脱敏，保留模型名与 base_url 原文。"""
        if not value:
            return False
        upper_key = key.upper()
        sensitive_tokens = ("API_KEY", "TOKEN", "SECRET", "PASSWORD")
        return any(token in upper_key for token in sensitive_tokens)

    def update_keys(self, keys: dict) -> None:
        """更新 API Keys"""
        valid_prefixes = (
            "OPENAI_", "ANTHROPIC_", "DEEPSEEK_", "MIMO_", "E2B_",
            "DEFAULT_MODEL", "COORDINATOR_MODEL", "MODELER_MODEL",
            "CODER_MODEL", "WRITER_MODEL", "OPENAI_BASE_URL", "LLM_PROXY_URL",
        )
        for k, v in keys.items():
            if any(k.startswith(p) for p in valid_prefixes) or k in valid_prefixes:
                if v and v.strip():
                    self._config[k] = v.strip()
                elif k in self._config:
                    del self._config[k]

        self._save()
        self._apply_to_env()
        logger.info(f"已更新 {len(keys)} 项运行时配置")

    def _apply_to_env(self) -> None:
        """将运行时配置应用到环境变量"""
        from app.config.setting import settings

        for k, v in self._config.items():
            os.environ[k] = str(v)
            try:
                object.__setattr__(settings, k.lower(), v)
            except Exception:
                pass

    def get_effective_key(self, key_name: str) -> Optional[str]:
        """获取有效 API Key（运行时配置优先于 .env）"""
        runtime_val = self._config.get(key_name)
        if runtime_val:
            return runtime_val

        from app.config.setting import settings
        return getattr(settings, key_name, None) or os.environ.get(key_name)

    def get_effective_model(self, key_name: str) -> Optional[str]:
        """获取有效模型配置。

        优先级：运行时专用模型 > 运行时默认模型 > 环境专用模型 > 环境默认模型。
        """
        runtime_model = self._config.get(key_name)
        if runtime_model:
            return runtime_model

        runtime_default = self._config.get("DEFAULT_MODEL")
        if runtime_default:
            return runtime_default

        from app.config.setting import settings

        env_model = getattr(settings, key_name, None) or os.environ.get(key_name)
        if env_model:
            return env_model

        return getattr(settings, "DEFAULT_MODEL", None) or os.environ.get("DEFAULT_MODEL")


# 全局单例
config_manager = ConfigManager()
