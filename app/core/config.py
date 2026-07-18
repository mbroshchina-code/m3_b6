"""Модуль конфигурации приложения дипломного проекта.

Строго соответствует структуре Nested Settings (Pydantic-settings v2).
"""

from __future__ import annotations
from functools import lru_cache
import os
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Защита: Исключаем локальный Redis из прокси-туннеля операционной системы
os.environ["NO_PROXY"] = "localhost,127.0.0.1,redis"


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LLM_")

    openai_api_key: SecretStr = SecretStr("sk-test-placeholder")
    default_model: str = "gpt-4o-mini"
    request_timeout: float = 30.0
    max_retries: int = 3
    
    # Твои поля для считывания прокси из .env
    openai_proxy_url: str | None = None
    base_url: str = "https://api.openai.com/v1"


class Settings(BaseSettings):
    """Глобальный класс настроек бэкэнда (config_obj)."""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",  # Позволяет писать настройки как LLM__DEFAULT_MODEL
        extra="ignore",
    )

    app_name: str = "BAG_ASSISTANT"
    debug: bool = False
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])
    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 3600
    
    # Вложенная структура настроек LLM
    llm: LLMSettings = Field(default_factory=LLMSettings)


# @lru_cache-обёртка get_settings()
@lru_cache
def get_settings() -> Settings:
    """Возвращает закэшированный экземпляр глобальных настроек."""
    return Settings()
