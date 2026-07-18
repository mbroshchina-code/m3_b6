"""Модуль ядра бизнес-логики ИИ-сервиса (LLM API Gateway).

Полностью оптимизирован под structlog контексты и защищен от AttributeError.
"""

from __future__ import annotations
import asyncio
import hashlib
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.exceptions import (
    LLMAuthError,
    LLMContentFilterError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from app.core.prompts import BAG_SYSTEM_PROMPT
from app.schemas.chat import ChatDelta, ChatRequest, ChatResponse, Usage

try:
    from openai import (
        APIConnectionError,
        APITimeoutError,
        AuthenticationError,
        BadRequestError,
        RateLimitError,
    )
except ImportError:
    APIConnectionError = APITimeoutError = AuthenticationError = BadRequestError = RateLimitError = ()  # type: ignore

# Инициализируем локальный структурированный логгер
logger = structlog.get_logger()


class LLMService:
    """Сервис управления запросами к ИИ с поддержкой отказоустойчивости и кэша."""

    def __init__(self, llm: object, cache: object | None, ttl: int = 3600):
        """Инициализирует сервис компонентами из DI-контейнера."""
        self.llm = llm  # Наш асинхронный клиент AsyncOpenAI
        self.cache = cache  # Наш асинхронный клиент Redis
        self.ttl = ttl  # Время жизни кэша в секундах

    def _key(self, req: ChatRequest) -> str:
        payload = req.model_dump(exclude={"user_id", "stream", "session_id"})
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return "chat:" + hashlib.sha256(blob.encode()).hexdigest()

    def _prepare_messages(self, req: ChatRequest) -> list[dict]:
        raw_messages = [m.model_dump() for m in req.messages]
        has_system = any(m["role"] == "system" for m in raw_messages)
        if not has_system:
            raw_messages.insert(0, {"role": "system", "content": BAG_SYSTEM_PROMPT})
        return raw_messages

    async def simple_complete(self, prompt: str, req_model: str = "gpt-4o-mini") -> str:
        try:
            raw = await self.llm.chat.completions.create(
                model=req_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=150,
            )
            return (raw.choices.message.content or "").strip()
        except Exception:
            return ""

    def _sync_bug_search(self, query: str) -> str:
        db_path = Path(__file__).resolve().parent.parent.parent / "prompts" / "bugs_database.json"
        if not db_path.exists():
            return "Ошибка: Локальная база инцидентов BAG_ASSISTANT отсутствует."
            
        try:
            with open(db_path, "r", encoding="utf-8") as f:
                bugs = json.load(f)
            
            raw_words = [w.lower().strip() for w in query.split() if len(w) > 2]
            words = [w[:-2] if len(w) > 4 else w for w in raw_words]

            if not words:
                return "Поисковое облако пусто."

            found_bugs = []
            for bug in bugs:
                full_bug_text = " ".join(str(value).lower() for value in bug.values())
                if isinstance(bug.get("content"), dict):
                    full_bug_text += " " + " ".join(str(v).lower() for v in bug["content"].values())

                matches = sum(1 for word in words if word in full_bug_text)
                if matches >= 2:
                    found_bugs.append(bug)
                    
            if found_bugs:
                return json.dumps(found_bugs, ensure_ascii=False, indent=2)
            return "В базе BAG_ASSISTANT совпадений не найдено."
        except Exception as e:
            return f"Ошибка парсинга базы: {e}"

    async def execute_bug_search(self, query: str) -> str:
        return await asyncio.to_thread(self._sync_bug_search, query)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def _call(self, req: ChatRequest, queries_list: list[str], db_context: str) -> ChatResponse:
        """Выполняет защищенный сетевой вызов к OpenAI."""
        # Фиксируем время старта сетевого вызова ИИ
        llm_start_time = time.perf_counter()
        
        try:
            processed_messages = self._prepare_messages(req)
            tool_call_id = "call_search_bug_db_123"
            
            processed_messages.insert(1, {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": tool_call_id,
                    "type": "function",
                    "function": {
                        "name": "search_bug_database",
                        "arguments": json.dumps({"queries": queries_list}, ensure_ascii=False)
                    }
                }]
            })
            
            processed_messages.insert(2, {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": "search_bug_database",
                "content": db_context
            })
            
            raw = await self.llm.chat.completions.create(
                model=req.model,
                messages=processed_messages,  # type: ignore
                temperature=req.temperature,
                max_tokens=req.max_tokens,
            )
            
            # Переводим сырой ответ в нашу Pydantic-схему
            resp = ChatResponse.from_openai(raw)
            
            # Рассчитываем точную задержку сетевого вызова к ИИ
            latency_ms = (time.perf_counter() - llm_start_time) * 1000

            # 🌟 ТРЕБОВАНИЕ НАСТАВНИКА: Пишем строку llm_request_completed в JSON со всеми полями!
            # Благодаря contextvars, поля request_id, user_id, path, method добавятся сюда АВТОМАТИЧЕСКИ!
            logger.info(
                "llm_request_completed",
                model=resp.model,
                input_tokens=resp.usage.prompt_tokens,
                output_tokens=resp.usage.completion_tokens,
                latency_ms=round(latency_ms, 2),
                finish_reason=resp.finish_reason
            )
            
            return resp
            
        except RateLimitError as e:
            raise LLMRateLimitError(str(e)) from e
        except AuthenticationError as e:
            raise LLMAuthError(str(e)) from e
        except APITimeoutError as e:
            raise LLMTimeoutError(str(e)) from e
        except BadRequestError as e:
            msg = str(e).lower()
            if "content" in msg and ("filter" in msg or "policy" in msg):
                raise LLMContentFilterError(str(e)) from e
            raise LLMError(str(e)) from e
        except APIConnectionError as e:
            raise LLMError(f"connection error: {e}") from e

    async def complete(self, req: ChatRequest) -> ChatResponse:
        """Выполняет синхронный запрос ИИ, управляя логикой кэша, Query Expansion и логов."""
        start_time = time.perf_counter()
        
        # 1. Проверяем Redis-кэш (только при temperature == 0.0)
        key = self._key(req)
        if req.temperature == 0.0 and self.cache is not None:
            try:
                blob = await self.cache.get(key)
                if blob:
                    resp = ChatResponse.model_validate_json(blob)
                    resp.cached = True
                    
                    # Пишем лог успешного попадания в кэш
                    logger.info(
                        "llm_request_completed",
                        model=resp.model,
                        input_tokens=resp.usage.prompt_tokens if resp.usage else 0,
                        output_tokens=resp.usage.completion_tokens if resp.usage else 0,
                        latency_ms=round((time.perf_counter() - start_time) * 1000, 2),
                        finish_reason=resp.finish_reason
                    )
                    return resp
            except Exception:
                pass

        user_query = req.messages[-1].content if req.messages else ""

        # ─── Query Expansion ───
        expansion_prompt = (
            f"Напиши через запятую ровно 3 разные по звучанию технические фразы-синонима "
            f"для поискового запроса: '{user_query}'."
        )
        synonyms_str = await self.simple_complete(expansion_prompt, req_model=req.model)
        
        queries_list = [s.strip() for s in synonyms_str.split(",") if s.strip()][:3]
        if len(queries_list) < 3:
            queries_list.extend([user_query] * (3 - len(queries_list)))

        # 2. Локальный поиск по базе багов
        full_search_cloud = f"{user_query} " + " ".join(queries_list)
        db_context = await self.execute_bug_search(full_search_cloud)
        
        # Переменная для хранения итогового ответа
        resp: ChatResponse

        # Если совпадений нет — возвращаем бесплатный отлуп
        if db_context == "В базе BAG_ASSISTANT совпадений не найдено.":
            resp = ChatResponse(
                content="Подходящих багов в базе данных BAG_ASSISTANT не найдено. Запрос не относится к известным инцидентам.",
                model=req.model,
                usage=Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
                finish_reason="stop",
                cached=False
            )
        else:
            # 3. Кэш-промах и баг найден: идем в сеть к OpenAI
            resp = await self._call(req, queries_list, db_context)
            resp.cached = False
            
            if req.temperature == 0.0 and self.cache is not None:
                try:
                    await self.cache.setex(key, self.ttl, resp.model_dump_json())
                except Exception:
                    pass

        # 🌟 ЕДИНАЯ, ЗАЩИЩЕННАЯ ТОЧКА СТРУКТУРИРОВАННОГО ЛОГИРОВАНИЯ ПО ТЗ
        latency_ms = (time.perf_counter() - start_time) * 1000
        
        # Безопасно извлекаем токены, защищаясь от AttributeError
        in_t = resp.usage.prompt_tokens if resp.usage else 0
        out_t = resp.usage.completion_tokens if resp.usage else 0
        
        logger.info(
            "llm_request_completed",
            model=resp.model or req.model,
            input_tokens=in_t,
            output_tokens=out_t,
            latency_ms=round(latency_ms, 2),
            finish_reason=resp.finish_reason or "stop"
        )
                
        return resp

    async def stream(self, req: ChatRequest) -> AsyncIterator[ChatDelta]:
        """Реализует потоковую генерацию (Streaming) без кэширования."""
        processed_messages = self._prepare_messages(req)
        
        stream_engine = await self.llm.chat.completions.create(
            model=req.model,
            messages=processed_messages,  # type: ignore
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )
        
        async for chunk in stream_engine:
            if getattr(chunk, "choices", None):
                delta = chunk.choices.delta
                if getattr(delta, "content", None):
                    yield ChatDelta(content=delta.content)
            if getattr(chunk, "usage", None):
                yield ChatDelta(usage=Usage.from_openai(chunk.usage))