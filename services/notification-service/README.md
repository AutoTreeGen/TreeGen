# notification-service (Phase 8.0)

Лёгкий FastAPI-сервис, отвечающий за доставку нотификаций пользователю
по выбранным каналам (in-app, log; будущие email / push). См. ADR-0024.

## Запуск

```powershell
uv run uvicorn notification_service.main:app --reload --port 8002
```

## Endpoints

| Метод | Путь | Кто вызывает | Назначение |
|---|---|---|---|
| `GET`  | `/healthz` | infra | liveness |
| `POST` | `/notify` | другие сервисы | создать и доставить нотификацию |
| `GET`  | `/users/me/notifications` | frontend | список (с фильтром `?unread=true`) |
| `PATCH`| `/notifications/{id}/read` | frontend | отметить прочитанным |

## Использование из других сервисов

```python
import httpx

await httpx.AsyncClient().post(
    "http://notification-service/notify",
    json={
        "user_id": 1,
        "event_type": "hypothesis_pending_review",
        "payload": {"hypothesis_id": 42, "tree_id": 7},
        "channels": ["in_app", "log"],
    },
)
```

Idempotency: повторный вызов с тем же
`(user_id, event_type, payload.ref_id)` в окне 1 час вернёт прежний
`notification_id` без второго insert. Channel failure isolation —
если `log` падает, `in_app` всё равно проходит.

## Тесты

```powershell
uv run pytest services/notification-service
```

Используют testcontainers-postgres (как `dna-service` / `parser-service`).

## Чего тут нет

- Email (Phase 8.1).
- WebSocket / push (Phase 8.3).
- User preferences / digest (Phase 4.x follow-up).
- Auth — `X-User-Id` header пока mock (TODO заменить на JWT с
  появлением auth слоя).
