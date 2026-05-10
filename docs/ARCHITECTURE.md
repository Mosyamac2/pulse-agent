# Pulse v2.7.13 — Architecture Map

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

Узкий immune core (с v1.0.0). `pulse/git_ops.py::is_protected_path` сверяется с этим списком и больше ни с чем:

- `BIBLE.md` — конституция
- `prompts/SAFETY.md` — нерушимые запреты
- `pulse/data_engine/schema.py` — схема БД

Остальной Python-код в `pulse/` редактируется эволюцией; фильтрами достаточности служат self-test (pytest + replay) и Opus commit-review против `docs/CHECKLISTS.md`.

## Façade layer (с v2.0.0, P14)

Фасад — узнаваемые продукт‑оунерам HCM‑панели поверх агента. Read‑only, evolvable, вне immune core.

| Слой | Файлы | Иммунный статус |
|---|---|---|
| Конституция фасада | P14 в `BIBLE.md` | immune (MAJOR-only) |
| Расширение схемы | `pulse/data_engine/hcm_schema.py` (новые таблицы: vacancies, candidates, goals, key_results, learning_feed, talent_pool_status, delegations, hr_requests, surveys_meta) | редактируем эволюцией |
| Синтетика | `pulse/data_engine/hcm_seed.py` (архетип-driven генераторы) | редактируем эволюцией |
| Бэкенд панелей | `pulse/hcm_panels.py` (read-only агрегаты), `pulse/server.py::/api/hcm/*` (GET-only эндпоинты) | редактируем эволюцией |
| UI shell | `web/app.html` (module rail + 9 вкладок), `web/index.html` (вкладка «Пульс» через iframe) | редактируем эволюцией |

Маршруты после v2.0.0:
- `GET /` → `web/app.html` (новый shell, дефолтная вкладка `pulse`)
- `GET /chat` → `web/index.html` (старый прямой чат, используется в iframe и legacy-ссылках)
- `GET /dashboard` → `web/dashboard.html` (CEO morning-brief, без изменений)
- `GET /api/hcm/*` → новые read-only эндпоинты (см. `pulse/hcm_panels.py`)
- `GET /api/dashboard/*`, `GET /api/sidebar/*`, `POST /api/chat*` — без изменений.

## Версионирование

См. P9 в `BIBLE.md`. Артефакты, обновляемые в каждом коммите:
- `VERSION`
- `pyproject.toml::version`
- `README.md` (badge + строка changelog, с лимитом: 5 patch / 5 minor / 2 major)
- этот файл (header)
- annotated git tag `v{VERSION}`
