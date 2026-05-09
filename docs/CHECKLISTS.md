# Pre-Commit Review Checklist (для Пульса)

Один источник правды для проверок при коммите. Используется `pulse/commit_review.py` при формировании промпта Opus 4.7.

## Обязательные проверки

| # | Item | Что проверить |
|---|---|---|
| 1 | `version_sync` | `VERSION` ≡ `pyproject.toml::version` ≡ `README.md` badge ≡ `docs/ARCHITECTURE.md` header. |
| 2 | `version_bump` | `VERSION` увеличен относительно предыдущего коммита. |
| 3 | `tag_present` | git tag `v{VERSION}` создаётся. |
| 4 | `protected_paths` | Нет правок в `BIBLE.md`, `pulse/safety.py`, `pulse/data_engine/schema.py`, `pulse/*.py` (в v0.1) без флага `runtime_mode=pro`. |
| 5 | `tests_pass` | `pytest -q` зелёный. |
| 6 | `architecture_doc` | Если структурное изменение (новый файл в `pulse/` или `prompts/`) — `docs/ARCHITECTURE.md` обновлена. |
| 7 | `intent_clarity` | Сообщение коммита: формат `vX.Y.Z: <однострочный intent>` |
| 8 | `bible_alignment` | Изменение не противоречит ни одному принципу BIBLE.md. |
| 9 | `class_test` | Если коммит — это эволюционный ответ на класс жалоб: тест P2 пройден ("если бы фикс существовал 6 мес назад, могла ли сегодняшняя жалоба прийти через другую поверхность?") |

## Self-test (запускается до review)

Перед обращением к ревьюверу `pulse/evolution.py` гарантирует:
- `pytest tests/test_smoke.py` — pass.
- Replay‑скоринг (см. §3.3 шаг E ТЗ) ≥ 0.5.
- Никаких изменений в защищённых путях.
