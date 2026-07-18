"""Модуль автоматической трассировки OpenTelemetry через Arize Phoenix."""

import os
from openinference.instrumentation.openai import OpenAIInstrumentor
from phoenix.otel import register


def setup_tracing(project_name: str = "diploma-fastapi") -> None:
    """Инициализирует TracerProvider и активирует автоинструментацию для OpenAI SDK.
    
    Читает PHOENIX_COLLECTOR_ENDPOINT из переменных окружения.
    """
    # Достаем адрес коллектора Phoenix. Внутри Docker-сети наставник просит http://phoenix:4317 (или 6006 в зависимости от версии протокола OTLP)
    endpoint = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:4317")
    
    # Регистрируем провайдер трассировки в системе Arize Phoenix
    tracer_provider = register(project_name=project_name, endpoint=endpoint)
    
    # МАНКИ-ПАТЧ: Подключаем автоинструментацию к OpenAI SDK
    OpenAIInstrumentor().instrument(tracer_provider=tracer_provider)
