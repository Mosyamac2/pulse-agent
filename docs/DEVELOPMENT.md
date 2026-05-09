# DEVELOPMENT.md — Краткая инструкция разработчику

## Локальная установка

```bash
python3.11 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# вписать CLAUDE_CODE_OAUTH_TOKEN в .env (получить через `claude setup-token`)
python -m scripts.seed --force
python -m pulse.data_engine.ml_train
pytest -q
python -m pulse.server   # http://127.0.0.1:8080
```

## Раскладка

- `pulse/` — модули агента.
- `prompts/` — промпты, эволюционируются Пульсом.
- `skills/` — instruction skills.
- `data/` — БД, память, логи (gitignore).
- `tests/` — pytest, **не бьют живой Claude API** — патчат `pulse.llm`.
- `scripts/` — CLI обёртки.
- `systemd/pulse.service` — unit для prod.

## Коммиты

Каждый коммит = релиз. См. `docs/CHECKLISTS.md`. Пульс сам соблюдает P9 в evolution loop.

## Тесты

```bash
pytest -q                              # все
pytest tests/test_smoke.py -q          # smoke
pytest tests/test_data_seed.py -q      # данные
```

## Анти-чеклист

- Никакого `ANTHROPIC_API_KEY` в `.env`. Только `CLAUDE_CODE_OAUTH_TOKEN`.
- Никаких `pip install` за пределами `requirements.txt`.
- Никаких прямых HTTP вызовов Anthropic API в коде.
- Не трогать `BIBLE.md`, `pulse/safety.py`, `pulse/data_engine/schema.py` без явного pro-режима.
