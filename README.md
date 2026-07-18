# m3_b6
К FastAPI-сервису из Б3.4 (с Docker-стеком из Б3.5) добавляется готовый observability-слой:

 - Phoenix поднят как сервис в docker-compose , UI открывается на
http://localhost:6006 ;

 - любой запрос к /chat автоматически создаёт трейс с input/output/токенами/latency;

 - JSON-лог на каждый запрос содержит request_id , model , токены, latency_ms , finish_reason — без сырых PII;

 - в docs/observability/ лежит скриншот одного трейса с подписью «что видно». В корне проекта лежат скриншоты трейсов в Phoenix

