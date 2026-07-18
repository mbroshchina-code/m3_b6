"""Модуль Pydantic-схем для валидации запросов и ответов эндпоинтов чата BAG_ASSISTANT.

"""

from __future__ import annotations
from typing import Annotated, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.core.prompts import BAG_SYSTEM_PROMPT

# Ограничение типов ролей в диалоге
Role = Literal["system", "user", "assistant", "tool"]


class Message(BaseModel):
    """Схема структуры одиночного сообщения в истории диалога BAG_ASSISTANT."""
    role: Role
    # Защита от DoS и пустых промптов (минимум 1 символ, максимум 100к логов)
    content: Annotated[str, Field(min_length=1, max_length=100_000)]


class Usage(BaseModel):
    """Схема статистики использования токенов ИИ-моделью с расчетом стоимости."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0

    @classmethod
    def from_openai(cls, u) -> Usage:
        """Адаптирует сырую структуру usage из OpenAI SDK."""
        if u is None:
            return cls()
        return cls(
            prompt_tokens=getattr(u, "prompt_tokens", 0),
            completion_tokens=getattr(u, "completion_tokens", 0),
            total_tokens=getattr(u, "total_tokens", 0),
            # Расчет стоимости может производиться в LLMService динамически
            estimated_cost_usd=0.0 
        )


class ChatRequest(BaseModel):
    """Схема валидации входящего HTTP-запроса для эндпоинтов /chat и /chat/stream."""
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "messages": [
                                                {
                            "role": "system", 
                            "content": BAG_SYSTEM_PROMPT
                        },
                        {
                            "role": "user", 
                            "content": "не проходит оплата эквайринг Борис банк"
                        },
                    ],
                    "model": "gpt-4o-mini",
                    "temperature": 0.2,
                }
            ]
        }
    )

    # Ограничение на длину контекста (от 1 до 50 сообщений)
    messages: Annotated[list[Message], Field(min_length=1, max_length=50)]
    model: str = "gpt-4o-mini"
    
    # Жесткие диапазоны валидации
    temperature: Annotated[float, Field(ge=0.0, le=2.0)] = 0.2
    max_tokens: Annotated[int, Field(ge=1, le=16_000)] = 1024
    stream: bool = False
    user_id: str | None = None
    session_id: str | None = None  # Сохранено для твоей логики сессий

    @model_validator(mode="after")
    def _first_message_not_assistant(self) -> ChatRequest:
        """Гарантирует, что контекст не начинается с ответа ассистента."""
        if self.messages and self.messages[0].role == "assistant":
            raise ValueError("Первое сообщение в истории диалога не может быть от assistant")
        return self


class ChatResponse(BaseModel):
    """Единая схема исходящего ответа API, абстрагированная от сырой структуры SDK."""
    content: str
    model: str
    usage: Usage
    finish_reason: str | None = None
    cached: bool = False
    request_id: str | None = None

    @classmethod
    def from_openai(cls, raw) -> ChatResponse:
        """Класс-метод адаптации сырого ответа AsyncOpenAI к нашей схеме."""
        choice = raw.choices[0]
        return cls(
            content=choice.message.content or "",
            model=raw.model,
            usage=Usage.from_openai(raw.usage),
            finish_reason=choice.finish_reason,
            cached=False
        )


class ChatDelta(BaseModel):
    """Схема потокового кадра (chunk) для ручной SSE-сериализации стриминга."""
    content: str | None = None
    usage: Usage | None = None


# Полиморфные структуры провайдеров (Multi-Request) ───

class OpenAIParams(BaseModel):
    provider: Literal["openai"]
    model: str = "gpt-4o-mini"
    seed: int | None = None


class OllamaParams(BaseModel):
    provider: Literal["ollama"]
    model: str = "llama3.1:8b"
    base_url: str = "http://localhost:11434/v1"


ProviderParams = Annotated[
    Union[OpenAIParams, OllamaParams],
    Field(discriminator="provider"),
]


class ChatRequestMulti(BaseModel):
    """Схема для маршрутизации запросов между разными инстансами ИИ."""
    messages: list[Message]
    params: ProviderParams
    temperature: float = 0.7
    max_tokens: int = 1024
