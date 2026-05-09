# Пульс — самоэволюционирующий HR‑агент

[![version](https://img.shields.io/badge/version-0.1.1-blue)](VERSION)

Пульс — становящийся цифровой ассистент сотрудника крупного банка. Помогает отслеживать «оптимальное боевое состояние»: эффективность, нагрузку, риск выгорания, маршруты роста. Идеологически наследник [Ouroboros](https://github.com/joi-lab/ouroboros-desktop), но без desktop‑овой инфраструктуры и с одним LLM‑бэкендом — Claude Agent SDK через OAuth Max‑подписку.

## Что внутри

- FastAPI + единый процесс, без Docker и воркеров.
- 100 синтетических сотрудников, 24 месяца истории, движок «нового дня».
- Три ML‑модели: отток, рекомендации курсов, прогноз успешности.
- Цикл эволюции: на основе пользовательского фидбэка `👍/👎` и комментариев Пульс классифицирует жалобы и сам правит свои промпты, skills, identity.
- Конституция (`BIBLE.md`), память‑нарратив (`identity.md`, `scratchpad.md`), реестр паттернов и бэклог улучшений — наследие Ouroboros.

## Быстрый запуск

```bash
bash scripts/bootstrap.sh                  # системные зависимости
claude setup-token                          # один раз, под пользователем pulse
cp .env.example .env && $EDITOR .env        # вставить sk-ant-oat01-...
.venv/bin/python -m scripts.seed --force    # генерация БД
.venv/bin/python -m pulse.data_engine.ml_train  # обучение моделей
sudo cp systemd/pulse.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now pulse
```

UI открывается на `http://VM:8080`.

## Документация

- `BIBLE.md` — конституция Пульса (13 принципов).
- `docs/ARCHITECTURE.md` — карта тела.
- `docs/CHECKLISTS.md` — pre‑commit чек‑лист.
- `docs/DEVELOPMENT.md` — как разрабатывать.

## Changelog

- `v0.1.1` — bootstrap.sh: detect system python (3.10+) instead of hardcoding 3.11; auto-detect repo dir from script location for dev mode; --system flag for production install with pulse user + systemd. Fixes Ubuntu 24.04 (Noble) install where python3.11 is not in default repos.

- `v0.1.0` — initial release. Self-evolving HR agent on Claude Agent SDK + OAuth Max. 82 tests across 9 modules. 100 synthetic employees, 24mo history, 3 ML models, 14 chat tools, 6-step evolution loop, consciousness daemon, deep self-review.

- `v0.1.0-rc.9` — consciousness loop (5 maintenance steps, rotated; daemon thread on FastAPI startup) + deep_self_review (full memory pack → Opus → data/memory/deep_review.md). POST /api/deep_self_review.

- `v0.1.0-rc.8` — evolution loop (6 steps from §3.3): aggregate→classify→plan→implement(SDK)→self-test→commit. Anti-oscillator with 7-day cooldown, human escalation, atomic state machine, /api/evolution POST.

- `v0.1.0-rc.7` — versioning + commit review (atomic bump, GitPython wrapper, single-Opus scope review with JSON verdict parser).

- `v0.1.0-rc.6` — memory + reflection: file-locked identity/scratchpad, improvement-backlog table store, pattern register, post-task reflection (Sonnet, BACKLOG: extraction → backlog).
- `v0.1.0-rc.5` — chat loop (ClaudeSDKClient + system prompt assembled from BIBLE/SYSTEM/identity/scratchpad/registry on every turn) + FastAPI endpoints (`/api/chat`, `/api/feedback`, `/api/history`, `/api/employees/*`) + minimal HTML UI with thumbs.
- `v0.1.0-rc.4` — in-process MCP tools (data, ML, JIRA, memory, knowledge, feedback, self). 14 chat tools + 17 evolution tools registered via `create_sdk_mcp_server`.
- `v0.1.0-rc.3` — daily tick engine + state.json. Stochastic events (hire/term/promote/sick/JIRA/peer_feedback). Idempotent, weekend-aware.
- `v0.1.0-rc.2` — three ML models (attrition GBM AUC≈0.99, kNN course recommender, role-success LogReg AUC≈0.81).
- `v0.1.0-rc.1` — synthetic data engine (100 employees, 8 archetypes, 24mo history, ~200k rows).
- `v0.1.0-rc.0` — skeleton (config, llm wrapper, FastAPI stub).

## Лицензия

MIT.
