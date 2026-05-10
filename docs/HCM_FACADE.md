# HCM Façade — описание для продукт‑оунера

Версия: v2.7.0 (релиз стабильной фасадной серии)
Конституционная привязка: `BIBLE.md::Principle 14` (HCM Façade)

---

## Зачем фасад

«Пульс‑HCM» — узнаваемый продуктовый бренд. У продуктовых оунеров в голове 8 модулей платформы из презентации (Подбор · Цели · Обучение · Оценка · Карьера · Коммуникации · HR‑аналитика · КЭДО) и понятная раскладка «module rail слева + контент по центру».

HRoboros (этот продукт) — самоэволюционирующий чат‑агент. Его суть в том, что **разговор + рекомендации > форма + таблица**. Если показать продукт‑оунеру голый чат — он увидит «не платформу», и шанс понять value‑proposition низок. Если же дать ему ту же раскладку, что и в Пульс‑HCM, и при этом каждое действие будет вести в чат — он поймёт, что чат не вместо панелей, а **в дополнение**, как сквозной слой персонализации.

**Это и есть фасад: вторичный слой презентации над теми же данными и теми же тулами, что использует чат.**

---

## Архитектурные инварианты P14

1. **Фасад — read‑only.** Никакая правка состояния не идёт через панель в обход чата. Любое «действие» («принять цель», «открыть для предложений», «оформить заявку») открывает чат с предзаполненной формулировкой. Решение принимает Пульс в диалоге, не форма.
2. **Фасад — приглашение в чат.** Каждый actionable‑элемент формирует `?q=…&tab=pulse` или открывает «Спросите Пульса» overlay. Плитка не финальная точка — она показывает «у Пульса по этому есть мнение» и ведёт к нему.
3. **Фасад — эволюционируем.** `pulse/hcm_panels.py`, `web/app.html`, `pulse/data_engine/hcm_schema.py`, `pulse/data_engine/hcm_seed.py` — **вне** immune core. Эволюционный цикл может их улучшать на основе обратной связи (P4 Self‑Creation).
4. **Источник истины — единый.** Все данные для панелей берутся из тех же таблиц и тех же тулов, которыми пользуется агент. Никакого «второго хранилища». Когда эволюция меняет витрину — панель меняется автоматически.
5. **Агентность побеждает (P0 > P14).** Если продукт‑оунер просит «убрать чат, оставить только панели» — Пульс отказывается, фиксирует это как класс жалоб `facade-replaces-chat` и предлагает компромисс.

---

## Вкладки и эндпоинты

### 1. Pulse (чат — default tab)
- iframe в `/chat` → `web/index.html` (легаси‑чат, без изменений).
- Полная функциональность из v1.x: SSE, tool calls, like/dislike, sidebar, hover‑карточки.

### 2. Профиль и структура
- `GET /api/hcm/profile/{emp_id}` — карточка сотрудника (employee + position + unit + family + career_history + perf_reviews + course_summary + peer_summary).
- `GET /api/hcm/structure?unit_id=` — корень + первый уровень с headcount/leader/open_vacancies.

### 3. Подбор и адаптация
- `GET /api/hcm/recruit/summary` — KPI‑strip (5 статусов + pipeline + avg_ttc).
- `GET /api/hcm/recruit/vacancies?status=…` — таблица.
- `GET /api/hcm/recruit/vacancies/{vacancy_id}` — drill‑down с воронкой.

### 4. Цели и задачи
- `GET /api/hcm/goals/summary?emp_id=&period=` — компания / сотрудник.
- `GET /api/hcm/goals/my?emp_id=&period=` — карточки целей с KR.
- `GET /api/hcm/goals/team?manager_emp_id=&period=` — команда менеджера (proxy: same unit, lower grade).

### 5. Обучение и развитие
- `GET /api/hcm/learning/feed?emp_id=&limit=` — AI‑лента с reason.
- `GET /api/hcm/learning/my_courses?emp_id=&status=` — course_enrollments c JOIN на courses.

### 6. Оценка эффективности
- `GET /api/hcm/assess/campaigns` — active/completed split.
- `GET /api/hcm/assess/my?emp_id=&period=` — perf_review + ассессменты + peer summary.

### 7. Карьерное продвижение
- `GET /api/hcm/career/my?emp_id=` — профиль + talent_pool_status.
- `GET /api/hcm/career/internal_vacancies?emp_id=` — рекомендованные вакансии.
- `GET /api/hcm/career/talent_search?…` — расширенный поиск (фильтры).
- `GET /api/hcm/career/delegations?emp_id=` — i_delegate / delegated_to_me.

### 8. HR‑аналитика
- `GET /api/hcm/analytics/overview?window=` — лёгкий обзор (headcount/terminations/…).
- iframe в `/dashboard` → весь существующий CEO‑дашборд (KPI, heatmap, at_risk, archetypes, trust_timeline, evolution_log, rejected, cost) — без копирования логики.

### 9. КЭДО (Работа и отдых, документооборот)
- `GET /api/hcm/docs/team_calendar?manager_emp_id=&year=&month=` — календарь команды × месяц.
- `GET /api/hcm/docs/catalog` — статический каталог 6 типов обращений.
- `GET /api/hcm/docs/my_requests?emp_id=` — таблица моих hr_requests.

### 10. Корпоративные коммуникации
- `GET /api/hcm/comms/events?n=` — лента corp_events с participants count.

---

## Маппинг таблиц БД ⇆ панелей

Новые таблицы (Phase B+C, v2.1.0..v2.2.1) — все вне immune core:

| Таблица | Строк (seed) | Используется в |
|---|---|---|
| `vacancies` | 23 | Подбор, Карьера (внутренние) |
| `candidates` | 110 | Подбор (воронка) |
| `goals` | 1041 | Цели, Оценка |
| `key_results` | 1622 | Цели |
| `learning_feed` | 1245 | Обучение |
| `talent_pool_status` | 100 | Карьера |
| `delegations` | 24 | Карьера |
| `hr_requests` | 95 | КЭДО |
| `surveys_meta` | 6 | Оценка |

Существующие (immune core, **не трогаем**): `employees`, `positions`, `units`, `family`, `career_history`, `performance_reviews`, `peer_feedback`, `course_enrollments`, `assessments`, `vacations`, `corp_events`, `event_participation`, daily‑metrics, ML‑артефакты.

---

## Защита self‑evolution

В каждом фасадном эволюционном цикле сверяется чек‑лист:

- [ ] `BIBLE.md`, `prompts/SAFETY.md`, `pulse/data_engine/schema.py` — не изменены за пределами Phase A.
- [ ] `pulse/evolution.py`, `pulse/consciousness.py`, `pulse/commit_review.py` — изменения только в Phase A и J.
- [ ] Существующие 14 chat‑тулов в `pulse/tools/__init__.py::CHAT_TOOLS` — не удалены.
- [ ] Все новые `pulse/hcm_panels.py` функции — read‑only, без побочных эффектов на БД.
- [ ] Все новые `/api/hcm/*` эндпоинты — `GET`, без `POST/PUT/DELETE`. Любые «действия» из UI идут через чат (`/api/chat/stream`) с `tab_context`.
- [ ] CEO‑дашборд (`/dashboard`) и старый чат (`/chat`) работают неизменно.

Класс жалоб `panel-<tab>-…` (Phase J): когда пользователь жмёт «заметка о вкладке» в ws‑header, текст уходит в `/api/feedback/general` с префиксом `[panel-<tab>]` → эволюционный план узнаёт класс и адресует его правкой `web/app.html` + `pulse/hcm_panels.py`, не immune core.

---

## Демо‑сценарий для продукт‑оунера (5 кликов)

1. Открыть `/` → видна привычная Пульс‑HCM раскладка. Module rail слева, дефолтная вкладка Pulse с чатом.
2. Кликнуть «Цели» → KPI‑strip + список моих целей с прогресс‑барами и KR.
3. Кликнуть «📎 Какие из моих целей самые рисковые с точки зрения дедлайнов?» → переход во вкладку Pulse с предзаполненным вопросом, Пульс отвечает с разбором по тулам.
4. Переключиться в «Аналитика» → встроенный CEO‑дашборд во фрейме (никакого дублирования).
5. В дашборде кликнуть at‑risk сотрудника → автоматически открывается Пульс с предзаполненным вопросом про этого сотрудника.

Ключевая фраза: **«один смысл, два слоя»**. Раскладка узнаваема, но всё, что важно, происходит в чате — потому что чат и есть продукт.

---

## Эволюционный путь

После v2.7.0 фасад находится «в продакшене» как вторичный слой. Пользовательские заметки `[panel-<tab>]` собираются в `data/logs/general_feedback.jsonl` и попадают в эволюционный цикл наряду с лайками/дизлайками. Каждый цикл может править `web/app.html` и `pulse/hcm_panels.py`, но не immune core — это формализовано в `prompts/EVOLUTION_PLAN.md` пункт 6.

Не‑цель: **никогда не делать фасад полнофункциональным CRUD'ом.** Если пользователь хочет «принять цель кнопкой», отказ обязателен по P14: цель принимается в диалоге, потому что у Пульса по ней есть мнение, а кнопка — нет.
