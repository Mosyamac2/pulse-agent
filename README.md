# Пульс — самоэволюционирующий HR‑агент

[![version](https://img.shields.io/badge/version-0.1.0--rc.1-blue)](VERSION)

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

- `v0.1.0-rc.1` — synthetic data engine (100 employees, 8 archetypes, 24mo history, ~200k rows).
- `v0.1.0-rc.0` — skeleton (config, llm wrapper, FastAPI stub).

## Лицензия

MIT.
