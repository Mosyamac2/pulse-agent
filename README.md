# Пульс — самоэволюционирующий HR‑агент

[![version](https://img.shields.io/badge/version-2.12.0-blue)](VERSION)

Пульс — становящийся цифровой ассистент сотрудника крупного банка. Помогает отслеживать «оптимальное боевое состояние»: эффективность, нагрузку, риск выгорания, маршруты роста. Идеологически наследник [Ouroboros](https://github.com/joi-lab/ouroboros-desktop), но без desktop‑овой инфраструктуры и с одним LLM‑бэкендом — Claude Agent SDK через OAuth Max‑подписку.

С версии **v2.0.0** у Пульса есть **HCM‑фасад** (P14) — узнаваемый набор из 9 вкладок поверх агентного ядра, повторяющий продуктовую раскладку «Пульс‑HCM». Фасад **read‑only**: каждый actionable‑элемент — приглашение в чат с предзаполненной формулировкой. Подробности — `docs/HCM_FACADE.md`.

## Что внутри

- FastAPI + единый процесс, без Docker и воркеров.
- 100 синтетических сотрудников, 24 месяца истории, движок «нового дня».
- Три ML‑модели: отток, рекомендации курсов, прогноз успешности.
- Цикл эволюции: на основе пользовательского фидбэка `👍/👎` и комментариев Пульс классифицирует жалобы и сам правит свои промпты, skills, identity, фасадные панели и synthetic‑генераторы (immune core по‑прежнему защищён).
- Конституция (`BIBLE.md`, 14 принципов), память‑нарратив (`identity.md`, `scratchpad.md`), реестр паттернов и бэклог улучшений — наследие Ouroboros.
- HCM‑фасад: 9 read‑only вкладок (Профиль · Подбор · Цели · Обучение · Оценка · Карьера · Аналитика · КЭДО · Коммуникации) + основная вкладка с чатом — каждая с presets «📎 Что спросить Пульса …».

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

UI открывается на `http://VM:8080`. Точки входа после v2.0.0:

| URL | Что отдаёт | Когда использовать |
|---|---|---|
| `/` | `web/app.html` — module rail + 9 HCM‑вкладок | Дефолтная страница для пользователя; вкладка `?tab=pulse` (default) iframe‑ит чат |
| `/chat` | `web/index.html` — прямой чат с Пульсом | Используется iframe‑ом из вкладки Pulse и для legacy‑ссылок `/?q=…` |
| `/dashboard` | CEO morning‑brief с 30‑дневным окном | Полный аналитический разбор; встраивается во вкладку Аналитика |
| `/api/chat/stream` | SSE‑стрим чат‑ответа | Принимает поле `tab_context` (v2.6.0+) |
| `/api/hcm/*` | 21 read‑only GET‑эндпоинт | Бэкенд для всех 9 фасадных панелей |

## Документация

- `BIBLE.md` — конституция Пульса (14 принципов; P14 — HCM Façade).
- `docs/ARCHITECTURE.md` — карта тела + раздел Façade layer.
- `docs/HCM_FACADE.md` — детальное описание фасадного слоя (для продукт‑оунеров).
- `docs/CHECKLISTS.md` — pre‑commit чек‑лист.
- `docs/DEVELOPMENT.md` — как разрабатывать.

## Changelog

- `v2.12.0` — Структурно закрыть fb-class-004 (ответ слишком длинный — главное тонет в воде, 38 жалоб high severity, последние — 2026-05-12: «две таблицы ещё лишние», «в 3 раза длиннее чем нужно», «одна таблица action items — полный ответ»). Текущий skill response_brevity полагается только на LLM-self-check и явно не справляется: класс продолжает расти и даже после введения BLUF в v2.11.0 жалобы про лишний объём идут. Реакция — ввести объективный length-guardrail в pulse/chat.py по аналогии с fallback_synth (fb-class-007): после получения финального текстового ответа от SDK измерять длину (слова) и число таблиц, определять тип вопроса (factual/analytical/strategic) через лёгкий LLM-классификатор и при превышении бюджета делать ровно один автоматический сжимающий ре-вызов LLM с директивой «сохрани BLUF + action-table + ключевые факты, убери преамбулы, повторы, вторые таблицы, мета-комментарии, дублирующие абзацы». Параллельно ужесточить skill response_brevity явными числовыми бюджетами, лимитом «≤1 т

- `v2.11.1` — Структурно закрыть fb-class-003 — пользователь видит сырой Markdown (звёздочки, решётки, пайпы) в UI чата вместо отрендеренного текста. Класс самый старый из неадресованных high (11 жалоб, 3 backlog-item повторно фиксируют пробел: #88, #149, #240). Реакция: переключить рендер сообщений в `web/index.html` (и, при необходимости, в `web/app.html`) на полноценный Markdown-парсер с GFM (таблицы, жирный, курсив, заголовки, списки, code-блоки, ссылки) и безопасной санитизацией (DOMPurify-allowlist). Текст ответа Пульса больше не вставляется как plain-text в DOM — он проходит через `marked` → `DOMPurify` → `innerHTML`. Старый stream-режим (постепенное добавление токенов) сохраняется: рендер выполняется на каждом ре-парсе накопленного буфера, чтобы пользователь видел форматирование уже во время стриминга, а финальный кадр — окончательный.

- `v2.11.0` — Структурно закрыть fb-class-026 (обратная иерархия: вывод и рекомендации в конце, корреляции/контекст в начале): ввести новый skill `inverted_pyramid` с жёстким self-check, что первое предложение и первый абзац ответа — это сам вывод/решение/действие, а не описание данных, корреляций, методологии или контекста. Skill активируется на любом аналитическом / HR-рекомендательном ответе и блокирует отправку, если первый абзац начинается с пены ('анализ показывает', 'в данных видно', 'корреляция между', 'контекст ситуации', 'для начала рассмотрим', 'прежде чем перейти'). Существующий BLUF в response_brevity это первая строка-ответ; inverted_pyramid дополняет — обязывает, что и второй–третий слой ответа (до первой таблицы) уже несут вывод и действие, а корреляции/метод/обоснование идут после таблицы, не до.

- `v2.10.3` — v2.10.3 — fix evolution SDK silently producing 0 tool calls: permission_mode was acceptEdits which only auto-accepts built-in file-edit tools (Read/Edit/Write). MCP tools (mcp__pulse-tools__*) and custom tools STILL required permission approval — but the SDK runs as a subprocess with no stdin/TTY so the prompt silently hung. Switched to bypassPermissions: safe because every change is gated by self-test + commit-review downstream.

- `v2.10.2` — v2.10.2 — observability: _apply_plan_via_sdk now logs tool_calls count, tool names, last text tail to journalctl. Previously the function ran silently — we could not distinguish SDK ran-but-did-nothing from SDK never-ran. Now journalctl shows: evolution.apply OK/TIMEOUT/ERROR class=... diff_targets=... tool_calls=N tools=[Write, Edit, ...] tail=<last reply>.

- `v2.10.1` — v2.9.1 — fix evolution SDK still refuses to edit existing pulse/*.py: system_prompt was vague («не трогай защищённые пути из BIBLE.md (P3)») without enumeration; body said «новых .py файлов» — implying creation only, not modification. Both rewritten: explicit immune-core enumeration, explicit permission to MODIFY existing pulse/*.py (chat.py, llm.py, server.py, tools/*, data_engine/*) AND create new files. Concrete execution rules (Read+Edit for modifications, Write for new) added so the SDK has unambiguous instructions.

- `v2.10.0` — Структурно закрыть fb-class-016 (отказ без альтернативы) — ввести skill no_dead_end_refusal: любой ответ, в котором Пульс собирается отказать (этический отказ, вне-скоупа, ложная предпосылка вопроса, P14-конфликт), обязан содержать reframe исходного намерения + 2–3 конкретных альтернативных пути, которые Пульс реально может пройти своими тулами. Чистый отказ становится структурно невозможен — self-check skill блокирует отправку ответа без альтернатив, а триггер активирует skill на широкий класс глаголов отказа («не могу», «не буду», «это не моя зона», «не лучший подход», «не помогу», «не рекомендую»).

- `v2.9.0` — Структурно закрыть fb-class-005 (ответ уходит в HR-теорию вместо прикладного разбора): ввести skill actionable_recommendations с обязательным блоком «Что делать» (actor + action + deadline + критерий успеха) и self-check, который активируется на любом ответе с HR-рекомендациями. Без блока — ответ не считается завершённым.

- `v2.8.5` — v2.8.5 — fix two bugs that made evolution structurally impossible: (1) _apply_plan_via_sdk body forbade .py files under pulse/ even though P3 v1.0.0 narrowed immune core to BIBLE/SAFETY/schema — SDK refused to create the new files plans required. (2) diff_with_head used plain git-diff-HEAD which never shows untracked files — even when SDK created new files, commit_review saw a diff without them and correctly blocked intent_clarity. Both fixed; smoke verified.

- `v2.8.4` — v2.8.4 — fix duplicating rails inside Pulse-tab iframe: paint-blocking <script> в <head> ставит html[data-embedded=true] синхронно (раньше маркер ставился асинхронно после /health+/api/history → видимый flash сайдбара ~600ms). Селекторы CSS переехали с body на html. Бонусом: <base target=_top> в embedded-mode чтобы любые markdown-ссылки в ответах Пульса навигировали parent app-shell вместо перезагрузки iframe в app.html.

- `v2.8.3` — v2.8.3 — rebalance immune system: COMMIT_REVIEW.md и ALIGNMENT_CHECK.md переписаны чтобы пропускать пользовательский фидбэк а не блокировать. P2 class-test превращён из абсолютного veto в graduated check по attempt_count (попытка 1-2 промпт-only OK, 3+ block). alignment_check default = aligned, needs_modification только при явной угрозе принципам. Жёсткие гейты остались: intent_clarity, protected_paths, tests_pass, replay<0.5, version_sync, anti-oscillator.

- `v2.8.2` — v2.7.14 — autonomous evolution dialed up: downvote threshold 5→2, COOLDOWN_DAYS 7→3, needs_modification auto-accepts the reformulated text and goes straight into evolution (no human gate). CEO emulator switched to adaptive Haiku 4.5 generation — questions/votes/general-feedback all driven by Haiku looking at recent dialogue (no round-robin). Loop cadence 5m → 1m.

- `v2.8.1` — Структурно закрыть fb-class-007 (пустой/усечённый ответ после длинной цепочки тулов): добавить в pulse/chat.py финальный fallback-синтезатор, который при отсутствии непустого текстового блока в SDK-ответе делает второй вызов LLM на компактном резюме tool-результатов и возвращает связный ответ; параллельно ввести в SYSTEM.md правило 'no-empty-final' (LLM обязана выдать содержательный финальный текст после tool-цепочки) и логировать инциденты в events.jsonl как fallback_synth для отслеживания частоты класса.

- `v2.8.0` — Ввести жёсткое правило краткости и BLUF (вывод первой строкой) с автотриггером на длину ответа — структурный ответ на fb-class-004 (ответ слишком длинный, главное тонет в воде) через новый skill response_brevity + правка SYSTEM.md, чтобы класс был структурно невозможен, а не подавлялся дисциплиной.

- `v2.7.13` — v2.7.13 — CEO emulator теперь полностью автономный: full_iteration mode (ask + auto-vote + occasional general-feedback + maybe_evolve в одном шаге). Question-bank расширен до 59 вопросов по всем 9 фасадным вкладкам. 18 CEO-style notes для /api/feedback/general. Авто-голосование: ~18% downvote → threshold (5) триггерится через 25-35 итераций при 5-мин интервале (один night = 100+ итераций → ≥1 эволюционный цикл с auto-push в origin через PULSE_GITHUB_PAT).

- `v2.7.12` — v2.7.12 — finally fix Markdown render: DOMPurify v3 exposes self as function (factory), not object — старый ready-check (typeof === object) был всегда false, чат и dock-overlay молча падали в plain-text. Заменили на проверку наличия конкретных методов: typeof window.marked.parse === function && typeof window.DOMPurify.sanitize === function. Та же правка в renderAssistantMarkdown (chat) и renderOverlayMarkdown (app.html).

- `v2.7.11` — v2.7.11 - fix Markdown render: vendored marked@12.0.2 + DOMPurify@3.1.4 в web/vendor/ (раньше /static/vendor/* отдавал 404, чат полагался только на CDN-fallback и иногда не успевал/блокировался). Cache-bust ?v=12.0.2 на script src чтобы браузеры обошли кеш 404. Теперь chat и dock-overlay рендерят headings/bold/lists/tables как HTML.

- `v2.7.10` — v2.7.10 — dock overlay теперь зеркало настоящего чата: postMessage в iframe чата (session.history, tab_context, тулы, лог); iframe эхает SSE обратно — overlay рендерит через marked+DOMPurify. Conversation мирорится в Pulse-таб. Floating button: «доработай Пульс под себя». h1 чата: Пульс + powered by HRoboros.

- `v2.7.9` — v2.7.9 — fix UI clash после возврата из «Дэшборд руководителя»: дашборд CSS (body{}, main{max-width:1200px}, a{}) утекали глобально. Теперь при переключении ВЫЕЗДА из analytics — content его таб-pane чистится (data-loaded=false), на возврате renderer перезапускается, дашборд перерисовывается с нуля. Никаких CSS-парсеров, никаких сложных скоупов.

- `v2.7.8` — v2.7.8 — переименование в шапке легаси-чата: «Пульс» → «HRoboros в Пульсе», «дашборд CEO» → «дашборд руководителя».

- `v2.7.7` — v2.7.7 — micro-clean: убран мелкий redundant rail-brand SVG над большой HRoboros-кнопкой (большая кнопка уже служит брендингом полосы). Минус 1 div, минус 8 строк CSS — rail стал визуально чище.

- `v2.7.6` — v2.7.6 — единый фидбэк-модал везде: на Pulse-вкладке скрыт верхний panel-feedback (там уже фигурирует фигурный «Заметка редактору» внутри iframe), а на остальных вкладках кнопка «заметка о вкладке» теперь открывает тот же фигурный модал, что и в чате. Модал переименован: «Редакции Пульса» → «Фидбэк для HRoboros — сердце Пульса». Тег вкладки показывается как chip в шапке + проставляется как [panel-X] префикс в body. Глобальный rename: «ядро Пульса» → «сердце Пульса» в rail-tip и aria-label.

- `v2.7.5` — v2.7.5 — Дашборд руководителя: убрали iframe и wrapper-текст, теперь дашборд рендерится инлайн через fetch /dashboard?embed=1 (новый параметр сервера выкидывает header+colophon). Виджет переехал на 2-е место в rail сразу после HRoboros core. Чтобы CSS не конфликтовал, фасадные KPI-классы переименованы: kpi-strip→fc-strip, kpi-label→fc-label, kpi-value→fc-value, kpi-foot→fc-foot, class="kpi"→class="fc-kpi".

- `v2.7.4` — v2.7.4 — UX-fix: убрана автопрокрутка чата на каждом стриминговом событии (раньше .scrollIntoView вызывался на каждом tool_call/text/status и дёргал страницу вниз). Теперь smart-scroll: листает только если пользователь уже у самого низа. Добавлена floating кнопка «Вниз» (bottom-right) — появляется когда юзер отскроллил вверх, исчезает у низа, click → smooth scroll.

- `v2.7.3` — v2.7.3 — UX-доработки по запросу: (1) Сквозная HR-аналитика → Дашборд руководителя: убрана дублирующая KPI-плашка, осталась чистая iframe-обёртка над /dashboard + chat-presets; CEO везде → руководитель в dashboard.html. (2) Демо-персона: DEFAULT_EMP_ID = emp_072 (Блинов Арсений, начальник отдела операций, 14 подчинённых, активная делегация); все вкладки теперь читают данные именно его, в UI вместо emp_NNN — «Блинов Арсений (emp_072)». (3) Профиль/Оценка: peer-сентимент → Отзывы коллег (с пояснением «сентимент −1…+1»).

- `v2.7.2` — v2.7.2 — hotfix теста: tests/test_hcm_panels.py::patched_paths теперь сохраняет и восстанавливает PATHS.db через try/finally. Раньше фикстура мутировала frozen-dataclass через object.__setattr__ и не откатывала, из-за чего test_smoke.py::test_pulse_config_paths видел удалённый tmp-путь и падал на assert PATHS.db.exists(). Полный pytest tests/ — 232/232 passed.

- `v2.7.1` — v2.7.1 — UX-патч rail: HRoboros (чат) — отдельная distinct-кнопка вверху rail (gradient, pulsing dot, 64×64), панели идут под ней с разделителем и подписью «панели». Iframe в /chat теперь грузится с ?embedded=1 → внутренний sidebar легаси-чата скрывается, чтобы не было double-rail. CSS body[data-embedded] в web/index.html.

- `v2.7.0` — Phase K — релиз v2.7.0: docs/HCM_FACADE.md (полное описание фасадного слоя для продукт-оунеров — инварианты P14, маппинг 9 вкладок на эндпоинты и таблицы, демо-сценарий из 5 кликов, защита self-evolution). README.md обновлён с секцией про P14, таблицей URL routing после v2.0.0 и ссылкой на HCM_FACADE.md.

- `v2.6.1` — Phase J — feedback-классы для панелей: per-tab кнопка «заметка о вкладке» в ws-header, отправляет в /api/feedback/general с префиксом [panel-<tab>]. EVOLUTION_PLAN.md: пункт 6 про классы panel-<tab>-… и правила для diff_targets, включающих web/app.html / pulse/hcm_panels.py.

- `v2.6.0` — Phase I — tab_context в /api/chat: ChatRequest получает поле tab_context (≤60 chars), pulse/chat.py::stream_chat_events и handle_chat принимают tab_context, _compose_user_message инъектит [Контекст вкладки: X] префикс перед history/question. Dock в web/app.html передает tab_context структурно вместо инлайна.

- `v2.5.2` — Phase H (H1+H2+H3) — Аналитика (KPI overview + iframe в /dashboard), КЭДО (календарь команды × месяц + каталог 6 типов обращений + мои запросы), Коммуникации (лента corp_events). Новая endpoint /api/hcm/comms/events.

- `v2.5.1` — Phase G (G1+G2+G3) — содержательное наполнение Обучение / Оценка / Карьера: AI-лента карточек с reason RU-маппингом, кампании 360+самооценка+peer summary в split-grid, карьерный статус + внутренние вакансии + делегирования (i/me).

- `v2.5.0` — Phase F (F1+F2+F3) — содержательное наполнение трёх первых вкладок: Профиль и структура (карточка + дерево юнитов), Подбор (KPI + chip-row статусов + таблица вакансий с раскрытием воронки кандидатов), Цели (KPI + карточки целей с progress bar и KR). Lazy-load на активацию таба, единый dispatch RENDERERS[name].

- `v2.4.1` — Phase E2 — persistent footer-dock «Спросите Пульса» во всех не-Pulse вкладках, slide-up overlay со стримом из /api/chat/stream (SSE), кнопка «Открыть в Пульсе» делает handoff в iframe через ?q=. Body[data-active-tab] определяет видимость dock.

- `v2.4.0` — Phase E1 — web/app.html: новый SPA-shell с module rail (10 кнопок: Pulse + 9 модулей Пульс-HCM), Pulse-таб через iframe в /chat (полная сохранность legacy-чата). Server: GET / отдает app.html, GET /chat — старый index.html (для iframe и legacy-ссылок). Tab routing client-side через ?tab=...

- `v2.3.1` — Phase D2 — pulse/hcm_panels.py: career (get_my_career, list_internal_vacancies, list_talent_search_results, list_delegations), profile (get_profile_full, get_org_structure), docs/КЭДО (list_my_hr_requests, get_team_calendar, get_request_catalog), analytics (get_hr_analytics_overview) + 10 GET /api/hcm/career/* /profile/* /structure /docs/* /analytics/overview эндпоинтов.

- `v2.3.0` — Phase D1 — pulse/hcm_panels.py: read-only фасадные агрегаты для табов Подбор/Цели/Обучение/Оценка + 10 GET-эндпоинтов /api/hcm/recruit/*, /api/hcm/goals/*, /api/hcm/learning/*, /api/hcm/assess/*. Стиль зеркалит pulse/dashboard.py — pure functions, optional db arg для тестов, 0 побочных эффектов.

- `v2.2.1` — Phase C2 — hcm_seed.py: gen_goals (архетип-driven, ~1000 строк, weights нормализованы), gen_key_results (~1600), gen_learning_feed (~1200, AI-лента), gen_talent_pool_status (one row per emp, open=0 для non-active), gen_delegations (~25), gen_hr_requests (~95, 70%+ done), gen_surveys_meta (~6 кампаний). Hooked into seed.py block 11.

- `v2.2.0` — Phase C1 — pulse/data_engine/hcm_seed.py: gen_vacancies (~22 шт, 5 распределённых статусов, 30%% internal_only) + gen_candidates (стадии воронки зависят от статуса вакансии, internal-кандидаты ссылаются на real emp_id, closed-вакансия = ровно 1 hired или все rejected). Hooked into seed.py block 11.

- `v2.1.0` — Phase B — pulse/data_engine/hcm_schema.py: 9 фасадных таблиц (vacancies, candidates, goals, key_results, learning_feed, talent_pool_status, delegations, hr_requests, surveys_meta) + create_hcm_tables() хук в seed.py. Schema-only, без данных — заполнятся в Phase C.

- `v2.0.0` — конституционное расширение P14 (HCM Façade). Подготовка к интеграции 8 модулей Пульс‑HCM как фасада над агентом, поверх существующего ядра самоэволюции.

- `v1.10.0` — **6 правок UX чата от CEO:** (1) русские названия архетипов в сайдбаре и hover-карточке (`pulse/employee_card.ARCHETYPE_RU` маппинг, API возвращает `archetype_label` / `label`); (2) peer-group means в карточке — для каждой из 4 метрик (Стресс/Сон/Фокус/Sentiment) отдельный мелкий шрифт «peers · 0.42» под значением, peer-group считается как `same position_id + same grade_level` за 30д, exclude self; (3) hover-tooltip на каждой метрике (CSS-only, через `data-tip` атрибут): что измеряется, как считается, границы нормы; (4) collapsible-сайдбар — каждая секция click-toggle с шевроном, по умолчанию архетипы и отделы свёрнуты; глобальный rail-toggle в шапке сворачивает весь сайдбар в 56-px колонку; состояние persist в `localStorage` под ключом `pulse.sidebar.v1`; (5) **fix регрессии**: markdown в pulse-bubble снова рендерится — новый `renderAssistantMarkdown(body, text)` вызывает `marked.parse + DOMPurify.sanitize` в `appendPulse` и в SSE-`done` handler, с graceful fallback на `textContent` если библиотеки недоступны и deferred re-render через `__pulseMdReady` promise; (6) flag-чипы в карточке тоже перевели на русский («в красной зоне», «выгорание», «риск ухода Х%»). Метрики `Стресс/Сон/Фокус/Sentiment` теперь с заглавной буквы. 14 unit-тестов в `tests/test_employee_card.py` и `tests/test_sidebar.py`.

- `v1.9.0` — **Sidebar + hover-cards + sparklines** в чате `/`. Двухколоночный layout: слева sticky-сайдбар (268 px) с 4 секциями — quick-prompts (6 шаблонов), архетипы с live-счётчиками, отделы с count, последние 8 разговоров; клик по любой строке предзаполняет composer. На мобильных (<800 px) — drawer с гамбургером + backdrop. Mention-walker: client-side `MutationObserver` в `#thread` оборачивает `emp_NNN` и все ФИО из `/api/employees/index` в `.emp-chip` с pill-стилем; hover на чип → fetch `/api/employees/{id}/card?window=30`, плавающая карточка с ФИО/должностью/отделом/архетипом/стажем + 4 метрики (стресс/сон/фокус/sentiment) с severity-окраской + risk/burnout/attrition badge'ы + кнопка «Спросить Пульс подробнее» (заполняет composer). Sparkline-инжектор: для каждой markdown-таблицы в pulse-bubble инфер `{emp, metric}` по заголовку колонки и первой ячейке строки, fetch `/api/employees/{id}/sparkline?metric=…` (30 точек), inline SVG рядом с числом. Цвет SVG зависит от direction метрики и тренда (последняя точка vs медиана). Бэкенд: `pulse/employee_card.py` (`get_employee_card` + `get_sparkline` с alias-резолвером для русских и английских названий метрик), новые функции в `pulse/dashboard.py` (`get_archetype_counts`, `get_department_counts`, `get_recent_threads`, `get_employee_index`), endpoints `/api/sidebar/{archetypes,departments,recent_threads}` + `/api/employees/index` + `/api/employees/{id}/{card,sparkline}`. 11 unit-тестов в `tests/test_employee_card.py` и `tests/test_sidebar.py`.

- `v1.8.0` — **Pulse-HCM design system.** Полная замена editorial-эстетики (Fraunces / Newsreader / parchment / oxblood) на корпоративный стиль материнского сайта pulse-hcm.ru. Шрифты: SB Sans Display / SB Sans Text (на Сберовских машинах) с Onest-фолбэком из Google Fonts. Палитра: `#fff` фон, `#252525` текст, primary blue `#0066ff` + deep `#0044bb`, secondary purple `#a729ff`, signature gradient `linear-gradient(140deg, #a729ff, #0763ff, #0066ff)`. Радиусы 8/12/20/32 px, мягкие тени `0 2px 10px rgba(0,0,0,0.07)`. Дашборд: hero-баннер с фирменным purple-blue градиентом и pill-метками вместо newspaper-masthead, KPI-карточки белые с soft-shadow + цветные delta-pill (зелёная/красная), heatmap в blue-red diverging вместо oxblood-forest, trust timeline blue/red бары, evolution-log self-tag в фирменном градиенте, cost area Opus=purple Sonnet=blue. Чат: bubble user — pale-blue tint, pulse — белый, focus-ring `box-shadow 0 0 0 3px rgba(0,102,255,0.12)`, primary-button blue с hover deep-blue. Виджет «заметка редактору» переехал из stamp-эстетики в pill-кнопку с fading purple-blue градиент-маркером и rounded-modal с soft shadow. Все CSS-токены `--ed-*` сохранены как алиасы — обратная совместимость. Минимум структурных правок — только визуальный слой.

- `v1.7.0` — **CEO-дашборд `/dashboard`** в editorial-эстетике v1.6.0 (Fraunces / Newsreader / JetBrains Mono, parchment + ink + oxblood). Hero-полоса из 4 чисел: AT-RISK (≥3/4 признака disengagement по 30-дневному окну), BURNOUT (≥3/4 признака overwork), HOT DEPT (отдел с худшим composite `sentiment − stress`), TRUST (like-rate `feedback.jsonl`). Под hero — три bundle'a: People (тепловая карта units × metrics + at-risk Top-7 + scatter архетипов stress×focus), Trust (diverging timeline лайков/дизлайков с маркерами релизов и self-evolved-флагом, лента коммитов, отвергнутые suggestion'ы), Cost (stacked area Opus/Sonnet + run-rate). Все actionable-элементы — это `/?q=<urlencoded>` ссылки: клик → главный чат с предзаполненным вопросом (CEO дописывает и шлёт). Бэкенд: `pulse/dashboard.py` — чистые агрегации над `data/sber_hr.db`, `data/logs/*.jsonl` и `git log` без нового storage; 8 GET-endpoint'ов в `pulse/server.py` (`/api/dashboard/{kpi,heatmap,at_risk,archetypes,trust_timeline,evolution_log,rejected,cost}`). Окно по умолчанию — 30 дней (CEO-ритм). 12 unit-тестов в `tests/test_dashboard.py`.

- `v1.6.0` — **«Заметка редактору» + конституционный гейт.** В UI добавлен отдельный плавающий виджет (правый верхний угол): `POST /api/feedback/general` принимает свободный текст (4-4000 симв.) и пишет в `data/logs/general_feedback.jsonl`. Перед попаданием в эволюционный цикл каждое предложение проходит **alignment check** — отдельный Opus-вызов `kind="alignment_check"` с промптом `prompts/ALIGNMENT_CHECK.md`, который оценивает совместимость с BIBLE.md + текущим SYSTEM.md + последними 30 строками improvement-backlog + identity/scratchpad. Вердикт: `aligned` → суггестия превращается в синтетический dislike-сигнал и идёт в общий `aggregate_feedback`; `needs_modification`/`rejected` → пишется в `data/memory/knowledge/rejected_suggestions.md` с обоснованием по конкретному принципу. Никаких отказов по «слишком сложно» — только по конституционным конфликтам. Эстетика виджета: «editorial morning brief» (Fraunces + Newsreader + JetBrains Mono, parchment + ink + oxblood). UI остаётся chat-ориентированным, виджет не трогает существующие стили чата.

- `v1.5.0` — **настоящая самоэволюция**: после успешного `commit_evolution` Pulse автоматически пушит ветку + тег на GitHub origin. `pulse/git_ops.py::push_to_origin_with_tags` читает `PULSE_GITHUB_PAT` из env, инжектит токен в URL только для одного `git push`, не персистится в `.git/config`. Без PAT — пуш пропускается с логом `evolution_push_skipped`, цикл считается успешным локально. Параллельно: исправлен truncation `intent[:240]` (мульти-словесный обрыв в subject коммитов v0.2.0/v1.4.1) — лимит поднят до 1000, и теперь `commit_evolution` строит Git-conventional message: краткий subject (≤72 символа, по первому предложению или word-boundary), полный intent в body, плюс trailers `Self-Evolved-By: pulse evolution_cycle (autonomous)` + `Co-Authored-By: Claude Opus 4.7`. На GitHub теперь видно, какие коммиты пришли от автономной эволюции, а какие — от человека.

- `v1.4.1` — Включить полноценный рендер Markdown в веб-чате (web/index.html): подключить marked + DOMPurify, рендерить ассистентские сообщения как HTML с поддержкой GFM (таблицы, заголовки, списки, code, жирный/курсив, ссылки), сохранить отображение по

- `v1.4.0` — auto-apply политика для evolution loop. `pulse/evolution.py` теперь обходит `escalate_to_human` гейт для планов, не затрагивающих immune core (`BIBLE.md`, `prompts/SAFETY.md`, `pulse/data_engine/schema.py`); продолжает через self-test → commit-review (P3 Immune Integrity). Если план таки затрагивает immune core — escalation сохраняется. `prompts/EVOLUTION_PLAN.md` переработан под v1.0.0+ полномочия (Python/web разрешены), убран обсолетный v0.1-запрет на правку `pulse/*.py`. `docs/CHECKLISTS.md` row 4 синхронизирован. Закрывает класс жалоб на бесконечную эскалацию `fb-class-003` (UI Markdown), который три цикла подряд останавливался на human-review.

- `v1.3.0` — `scripts/ceo_emulation.py` — overnight автономный CEO‑эмулятор. Драйвер для `/loop`-цикла: `ask` (выбрать вопрос из 30-элементного банка с deterministic shuffle, отправить в `/api/chat` с накопленной session_history, вернуть JSON), `feedback ID up|down comment` (записать в /api/feedback, обновить downvote-счётчик), `maybe_evolve` (триггерит /api/evolution force=false при ≥5 дизлайков), `status`. Состояние в `data/ceo_emulation/state.json`, лог в `log.jsonl`, ошибки в `errors.jsonl` — всё gitignored. Прогон 114 итераций × 5 мин ночью дал 102/21 like/dislike, 4 запуска evolution-цикла (1 committed v0.2.0, 3 escalated на fb-class-003 UI Markdown).

- `v1.2.0` — `run_python_analysis(code, timeout_s)` — sandboxed pandas/numpy execution. Subprocess-isolation через `multiprocessing.Process` + hard kill по таймауту, SQLite read-only (`?mode=ro` URI), restricted builtins (нет `open`/`__import__`/`exec`/`compile`/`eval`), pre-loaded DataFrames `df_employees`/`df_activity`/`df_digital`/`df_wearables`/`df_collab`/`df_peer` (последние 90 дней) + `pd`/`np`. Stdout cap 8KB, default timeout 15s, max 60s. 10 тестов покрывают исполнение, песочницу (open/import/exec заблокированы), read-only БД, kill по таймауту, обрезку вывода. Для разовых аналитических вопросов где нет витрины. Если запрос повторяется — Пульс создаёт постоянную витрину через эволюцию, а не запекает в sandbox.

- `v1.1.0` — аналитический слой витрин для типовых HR-вопросов. `pulse/data_engine/marts.py` — чистые SQL-агрегаты (top-N по любой метрике, распределение, разрез по unit/position/archetype/grade, top connectors, индекс эффективности `tasks/h × focus`). 6 новых MCP-тулов в `pulse/tools/mart_tools.py`: `list_available_metrics`, `top_employees_by_metric`, `metric_distribution`, `aggregate_metric_by`, `top_collab_connectors`, `efficiency_ranking`. Все вопросы с квантором («кто самый/у кого больше всех»/«в каком отделе») закрываются ОДНИМ SQL вместо цикла из 90 вызовов `get_employee_metrics`. SYSTEM.md дополнен правилом выбора one-emp vs витрина. Закрывает backlog #1, #2, #10, #11, #12.

- `v1.0.0` — **MAJOR: конституция и полномочия**. (1) `BIBLE.md` v2.0: новая секция «Профессиональная идентичность» (HR полного цикла) и Principle 13 «Profession» — Пульс позиционирован как HR-профессионал с целью «постоянно совершенствовать рекомендации руководителям и сотрудникам». (2) Снят запрет эволюции править `pulse/*.py`: immune core сужен до `BIBLE.md`, `prompts/SAFETY.md`, `pulse/data_engine/schema.py`. Гейтами достаточности остаются self-test и Opus commit-review (P3). (3) Чинит backlog #6: `pulse/git_ops.py::create_annotated_tag` теперь форсит ident `Pulse Builder <pulse@local>` через `custom_environment` — не падает на systemd-юзере без `~/.gitconfig`. (4) `prompts/SYSTEM.md` и `identity.md` пересобраны вокруг новой идентичности. P9 MAJOR: 0.2.1 → 1.0.0.

- `v0.2.1` — фикс класса жалоб `fb-class-002` (потеря контекста сессии между репликами). UI копит `session.history` в памяти таба (гидрируется из `/api/history?limit=10` на загрузке), отправляет последние 10 турнов на каждый `POST /api/chat/stream`. Бэкенд (`pulse/chat.py::_compose_user_message`) пре-пендит блок «[Контекст диалога]» к user-message перед вызовом SDK; кап 10 турнов или 8KB. Лог чата по-прежнему пишет bare question, без раздутия. Защищённый Python-путь — правка с явного разрешения пользователя.

- `v0.2.0` — структурный ответ Пульса (через evolution cycle) на класс жалоб «голые метрики без интерпретации». Новое правило в `prompts/SYSTEM.md`: каждое число в ответе сопровождается расшифровкой (что измеряется + шкала) и качественным маркером (норма/повышено/тревожно). Новый skill `metrics_interpretation` (шаблон, глоссарий жаргона на русском, чек-лист перед отправкой, антипаттерны). MINOR: семантика общения с топ-менеджером изменилась.

- `v0.1.4` — `POST /api/chat/stream` serves the same chat turn as Server-Sent Events (status → tool_call → tool_result → text → done) so the UI can show live progress instead of a static "думаю…". `web/index.html` renders an activity log inside the placeholder bubble while events arrive, swaps to the final answer on `done`, and keeps the run trace under a collapsed `<details>`. `/api/chat` (non-streaming) preserved unchanged for tests/scripts.

- `v0.1.3` — `.env.example` now documents `HTTPS_PROXY`/`HTTP_PROXY`/`NO_PROXY` for installs in regions where Anthropic OAuth Max is blocked. The `claude` CLI honors these env vars (HTTP CONNECT only, no SOCKS) and the SDK subprocess inherits them through systemd's `EnvironmentFile=.env`. Fixes `403 Request not allowed` on Yandex Cloud.

- `v0.1.2` — bootstrap.sh now generates a tailored systemd/pulse.service.generated with the correct User= and WorkingDirectory= for the local install. Removes the manual sed-patching step (was failing on dev installs where the pulse user does not exist).

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
