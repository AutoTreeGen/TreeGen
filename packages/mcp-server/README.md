# treegen-mcp

Model Context Protocol server для AutoTreeGen (Phase 10.8).

Подключите свою AutoTreeGen-учётку к Claude Desktop, ChatGPT или любому
другому MCP-host'у — и LLM сможет отвечать на вопросы про ваше дерево
(«расскажи про мою прапрабабушку», «найди всех Smith'ов в дереве»,
«какие сейчас активные гипотезы»).

Read-only в этом релизе: tools только читают; редактирование персон —
отдельной фазой.

## What it is

Тонкий stdio-сервер по
[Model Context Protocol](https://modelcontextprotocol.io). Внутри —
HTTP-клиент, который дёргает AutoTreeGen API gateway вашим API-ключом.
Базы данных не касается, секреты не хранит — всё через HTTP.

### Tools

| Name                  | Что делает                                              |
| --------------------- | ------------------------------------------------------- |
| `list_my_trees_tool`  | Список ваших деревьев                                   |
| `get_tree_context_tool` | Context-pack дерева (persons, families, ego, hypotheses) |
| `resolve_person_tool` | Натуральная фраза → person ID (`"my mother"`)           |
| `get_person_tool`     | Карточка персоны по UUID                                |
| `search_persons_tool` | Поиск по имени в дереве                                 |

### Resources

- `treegen://trees/{tree_id}/context` — context-pack дерева.
- `treegen://persons/{person_id}` — карточка персоны.

## Install

```bash
# Через uv tool (рекомендованно):
uv tool install treegen-mcp

# Либо через pip:
pip install treegen-mcp
```

## Configure

Создайте API-ключ в личном кабинете AutoTreeGen (`Settings → API keys`).
Затем добавьте сервер в конфиг своего MCP-host'а.

### Claude Desktop

Файл конфига:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "treegen": {
      "command": "uv",
      "args": ["tool", "run", "treegen-mcp"],
      "env": {
        "TREEGEN_API_URL": "https://api.autotreegen.example.com",
        "TREEGEN_API_KEY": "atg_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
      }
    }
  }
}
```

После сохранения перезапустите Claude Desktop. В чате сервер появится
как `treegen` — иконка инструментов рядом с полем ввода.

### ChatGPT custom connector

ChatGPT desktop поддерживает MCP-серверы через тот же stdio-протокол.
В настройках connector'ов укажите:

- **Name**: `treegen`
- **Command**: `uv`
- **Args**: `tool run treegen-mcp`
- **Env**:
  - `TREEGEN_API_URL` — base URL вашего AutoTreeGen API gateway.
  - `TREEGEN_API_KEY` — API-ключ.

> Если ваш ChatGPT-клиент пока без MCP — используйте Claude Desktop
> или любой другой совместимый host (Cursor, Continue.dev и т.д.).

## Environment variables

| Variable             | Required | Default                  | Описание                                   |
| -------------------- | -------- | ------------------------ | ------------------------------------------ |
| `TREEGEN_API_KEY`    | yes      | —                        | Bearer-токен AutoTreeGen API.              |
| `TREEGEN_API_URL`    | no       | `http://localhost:8000`  | Base URL API gateway.                      |
| `TREEGEN_API_TIMEOUT` | no      | `30`                    | HTTP-таймаут в секундах.                   |

## Run manually

Полезно для дебага конфига и проверки сетевой доступности:

```bash
export TREEGEN_API_URL=https://api.autotreegen.example.com
export TREEGEN_API_KEY=atg_live_...
uv run treegen-mcp
```

Сервер слушает stdio — без host'а команда «висит» (это нормально).
Завершите `Ctrl+C`. Если на старте падает с `MissingApiKeyError` или
HTTP-ошибкой — это и есть ваша диагностика.

## Tests

```bash
uv run --package treegen-mcp pytest packages/mcp-server/tests -v
```

Все тесты — без сети (`pytest-httpx` мокает HTTP).
