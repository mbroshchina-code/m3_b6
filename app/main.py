"""Главный модуль FastAPI приложения BAG_ASSISTANT.

Полностью укомплектован под structlog-контексты, автотрассировку Phoenix
и маскирование PII на базе регулярных выражений. Без CORS.
"""

import json
import time
import uuid
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as redis
import structlog
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from openai import AsyncOpenAI

from app.core.config import get_settings
from app.core.exceptions import LLMError
from app.observability.logging import setup_logging
from app.observability.pii import redact_pii, prompt_hash
from app.observability.tracing import setup_tracing
from app.routers import chat, health, models

# Активируем глобальные настройки structlog по ТЗ
setup_logging(level="INFO")
log = structlog.get_logger()

settings = get_settings()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Управление жизненным циклом приложения (Lifespan)."""
    
    # Запускаем трейсинг ДО создания клиентов
    try:
        setup_tracing(project_name="bag-assistant")
        log.info("Автоинструментация Arize Phoenix успешно активирована.")
    except Exception as e:
        log.error("Не удалось запустить трассировку Phoenix", error=str(e))

    # Инициализация асинхронного клиента OpenAI
    openai_key = settings.llm.openai_api_key.get_secret_value() if settings.llm.openai_api_key else None
    if openai_key:
        app.state.llm = AsyncOpenAI(
            api_key=openai_key,
            base_url=settings.llm.base_url,
            http_client=httpx.AsyncClient(
                proxy=settings.llm.openai_proxy_url if settings.llm.openai_proxy_url else None,
                timeout=settings.llm.request_timeout,
            )
        )
        log.info("ИИ-клиент AsyncOpenAI успешно инициализирован.")
    else:
        app.state.llm = None
        log.warning("OPENAI_API_KEY отсутствует — генерация недоступна.")

    # Инициализация асинхронного Redis
    try:
        app.state.redis = redis.from_url(settings.redis_url, decode_responses=True)
        await app.state.redis.ping()
        log.info("Успешное подключение к Redis. Кэширование активировано.")
    except Exception as e:
        app.state.redis = None
        log.warning("Redis недоступен — продолжаем без кеша", error=str(e))

    yield

    # Безопасное закрытие сессий при выключении сервера
    if app.state.llm:
        try:
            await app.state.llm.close()
            log.info("Сессия AsyncOpenAI успешно закрыта.")
        except Exception:
            pass
            
    if app.state.redis:
        try:
            await app.state.redis.close()
            log.info("Подключение к Redis успешно закрыто.")
        except Exception:
            pass


# Инициализируем FastAPI без лишних настроек
app = FastAPI(title=settings.app_name, lifespan=lifespan)


# Middleware со structlog contextvars
@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    """Middleware для привязки контекстных переменных structlog и PII-маскирования."""
    
    # Читаем x-request-id или генерируем СРЕЗ до 12 символов
    request_id = request.headers.get("X-Request-ID") or request.headers.get("x-request-id")
    if not request_id:
        request_id = uuid.uuid4().hex[:12]

    user_id = request.headers.get("X-User-ID", "anonymous")

    # Привязываем базовый контекст в начале запроса
    structlog.contextvars.bind_contextvars(
        request_id=request_id,
        user_id=user_id,
        path=request.url.path,
        method=request.method,
    )

    # Извлекаем входящий промпт до обработки роутера для маскирования
    raw_user_prompt = ""
    if "/chat" in request.url.path and request.method == "POST":
        try:
            body_bytes = await request.body()
            async def receive():
                return {"type": "http.request", "body": body_bytes, "more_body": False}
            request._receive = receive
            
            req_json = json.loads(body_bytes)
            messages = req_json.get("messages", [])
            if messages:
                raw_user_prompt = messages[-1].get("content", "")
        except Exception:
            pass

    # Маскируем промпт быстрыми регулярками наставника из pii.py
    prompt_preview_safe = redact_pii(raw_user_prompt)[:120]

    # Дописываем замаскированные поля в контекст structlog
    structlog.contextvars.bind_contextvars(
        prompt_hash=prompt_hash(raw_user_prompt),
        prompt_preview=prompt_preview_safe
    )

    start_time = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        duration_ms = (time.perf_counter() - start_time) * 1000
        
        # Обычный лог для системных запросов (/health, /ready, /models)
        if "/chat" not in request.url.path:
            log.info("request_processed", status=response.status_code, latency_ms=round(duration_ms, 2))
            
        # Очищаем контекст для следующего запроса
        structlog.contextvars.clear_contextvars()

    # Ставим заголовок на ответ
    response.headers["X-Request-ID"] = request_id
    return response


# --- Базовые обработчики ошибок ---
@app.exception_handler(LLMError)
async def llm_exception_handler(request: Request, exc: LLMError) -> Response:
    return Response(
        status_code=502,
        content=json.dumps({"error": {"code": "llm_error", "message": str(exc)}}),
        media_type="application/json",
    )

@app.exception_handler(RequestValidationError)
async def handle_validation(request: Request, exc: RequestValidationError) -> Response:
    errors = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
    return Response(
        status_code=422,
        content=json.dumps({"error": {"code": "validation_error", "fields": errors}}),
        media_type="application/json",
    )


app.include_router(health.router)
app.include_router(models.router)
app.include_router(chat.router)
