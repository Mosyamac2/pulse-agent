# Pulse v0.1.0-rc.0 — Architecture Map

Это операционная карта Пульса. Single source of truth для разработки, отладки и саморевью.

---

## High-level

```
FastAPI (uvicorn) → chat_loop / evolution_loop / consciousness_loop
                  → Claude Agent SDK (OAuth Max) → Opus 4.7 / Sonnet 4.6
                  → in-process MCP tools
                  → SQLite (data/sber_hr.db)
```

## Модули (rationale включён)

- `pulse/server.py` — FastAPI. Тонкий, без бизнес-логики. Ловит REST, проксирует в `chat.py`.
- `pulse/chat.py` — chat-loop через ClaudeSDKClient. Stateful клиент на сессию.
- `pulse/evolution.py` — 6-шаговый цикл (см. §3 ТЗ).
- `pulse/consciousness.py` — фоновой thread, проходит maintenance protocol.
- `pulse/llm.py` — единственная точка вызова Claude SDK.
- `pulse/safety.py` — single-cheap-call safety check на write/edit-тулы.
- `pulse/data_engine/` — чисто Python: генерация / тик / ML.
- `pulse/tools/` — определения @tool для SDK.
- `pulse/memory.py` — read/write для `identity.md`, `scratchpad.md`, `knowledge/`.
- `pulse/git_ops.py` — `commit`, `tag`, `rollback`.
- `pulse/commit_review.py` — single Opus call vs CHECKLISTS.md.
- `pulse/version_ops.py` — атомарный bump VERSION + sync во всех артефактах.

## Логи и состояние

| Файл | Назначение |
|---|---|
| `data/logs/chat.jsonl` | каждый поворот диалога |
| `data/logs/feedback.jsonl` | лайки/дизлайки |
| `data/logs/events.jsonl` | task_received, task_done, evolution_started, evolution_committed, daily_tick, etc. |
| `data/logs/tools.jsonl` | каждый tool-call |
| `data/logs/task_reflections.jsonl` | сгенерированные рефлексии |
| `data/logs/budget.jsonl` | usage по моделям |
| `data/state/state.json` | last_evolution_offset, last_version, cooldowns, ml.needs_refresh |

## Защищённые пути (immune system)

Сборное понятие, шарится между `pulse/safety.py` и `pulse/commit_review.py`:

- `BIBLE.md`
- `pulse/safety.py`
- `prompts/SAFETY.md`
- `pulse/data_engine/schema.py`
- `pulse/*.py` (в v0.1; снимется в v0.x)

## Версионирование

См. P9 в `BIBLE.md`. Артефакты, обновляемые в каждом коммите:
- `VERSION`
- `pyproject.toml::version`
- `README.md` (badge + строка changelog, с лимитом: 5 patch / 5 minor / 2 major)
- этот файл (header)
- annotated git tag `v{VERSION}`
