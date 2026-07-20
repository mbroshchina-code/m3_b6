# m3_b6-b7
К FastAPI-сервису из Б3.4 (с Docker-стеком из Б3.5) добавляется готовый observability-слой:

 - Phoenix поднят как сервис в docker-compose , UI открывается на
http://localhost:6006 ;

 - любой запрос к /chat автоматически создаёт трейс с input/output/токенами/latency;

 - JSON-лог на каждый запрос содержит request_id , model , токены, latency_ms , finish_reason — без сырых PII;

 - в docs/observability/ лежит скриншот одного трейса с подписью «что видно». В корне проекта лежат скриншоты трейсов в Phoenix

## Тесты 
warning: The `tool.uv.dev-dependencies` field (used in `pyproject.toml`) is deprecated and will be removed in a future release; use `dependency-groups.dev` instead
======================================================================= test session starts ========================================================================
platform win32 -- Python 3.12.10, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\Users\m.roschina\Desktop\Эвотор\мое\Обучение\bag_assistant
configfile: pyproject.toml
plugins: anyio-4.14.1, arize-phoenix-client-2.13.0, asyncio-1.4.0, mock-3.15.1
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collected 8 items                                                                                                                                                   

tests\unit\test_llm_core.py ........                                                                                                                          [100%]

======================================================================== 8 passed in 2.42s =========================================================================
