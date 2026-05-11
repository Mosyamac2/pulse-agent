"""fb-class-007 — no-empty-final после tool-цепочки.

Class: пользователь получает пустой или preamble-only финал
(«Запрашиваю…», «Анализирую…») после ≥1 вызова инструмента.
Структурный ответ из плана эволюции 2.8.1 состоит из двух слоёв:

1. **Prompt-уровень** — правило `no-empty-final` в
   `prompts/SYSTEM.md` (раздел «Перед каждым ответом»). LLM обязана
   завершать ход содержательным текстом; пустой/preamble-only финал
   объявлен нарушением. Этот слой приземлился в этом цикле и
   тестируется здесь напрямую — собранный системный промпт должен
   нести правило, иначе регрессия SYSTEM.md пройдёт незамеченной.

2. **Транспорт-уровень** — fallback-синтезатор в `pulse/chat.py`,
   подменяющий пустой финал результатом второго LLM-вызова поверх
   резюме tool-результатов, и логирование инцидента в
   `data/logs/events.jsonl` как `fallback_synth`. Этот слой живёт
   в `pulse/*.py` — путь временно вне зоны записи этого цикла,
   поэтому сами проверки помечены `pytest.mark.skip` с явной
   причиной. Тела тестов оставлены как **исполняемая спецификация**:
   когда транспорт-слой приземлится, достаточно снять skip-маркер.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator

import pytest


# ---------------------------------------------------------------------------
# Prompt-уровень — правило no-empty-final в SYSTEM.md
# ---------------------------------------------------------------------------

def test_system_md_carries_no_empty_final_rule() -> None:
    """SYSTEM.md явно перечисляет правило no-empty-final.

    Регрессия: если кто-то вырежет пункт «6. no-empty-final» из раздела
    «Перед каждым ответом», prompt-слой защиты от fb-class-007 пропадёт
    молча. Этот тест ловит такую правку до коммита.
    """
    p = Path(__file__).resolve().parent.parent / "prompts" / "SYSTEM.md"
    text = p.read_text(encoding="utf-8")
    assert "no-empty-final" in text, "SYSTEM.md должен содержать правило no-empty-final"
    assert "fb-class-007" in text, "правило должно ссылаться на класс жалоб"
    # Правило должно жить именно в разделе «Перед каждым ответом»,
    # а не где-то в подвале — иначе LLM не прочтёт его в момент,
    # когда оно нужно.
    pre_idx = text.index("Перед каждым ответом")
    rule_idx = text.index("no-empty-final")
    fmt_idx = text.index("Формат ответа (BLUF)")
    assert pre_idx < rule_idx < fmt_idx, (
        "правило no-empty-final должно стоять между «Перед каждым ответом» "
        "и «Формат ответа (BLUF)»"
    )


def test_build_system_prompt_threads_no_empty_final_rule() -> None:
    """build_system_prompt() склеивает SYSTEM.md с BIBLE.md и т.д. — правило
    должно остаться видимым в финальной сборке, иначе LLM его не получит."""
    from pulse.chat import build_system_prompt
    sp = build_system_prompt()
    assert "no-empty-final" in sp
    assert "fb-class-007" in sp


# ---------------------------------------------------------------------------
# Транспорт-уровень — fallback-синтезатор
# ---------------------------------------------------------------------------
# Эти тесты — спецификация того, как должен вести себя fallback-синтезатор,
# когда он приземлится в pulse/chat.py. Сейчас они помечены skip потому что
# pulse/*.py редактирование заблокировано в этом цикле эволюции.

_TRANSPORT_SKIP = pytest.mark.skip(
    reason="fallback-синтезатор живёт в pulse/chat.py; pulse/*.py вне зоны "
           "записи в этом цикле (см. план эволюции 2.8.1). Тесты-спецификация: "
           "снять skip после приземления транспорт-слоя."
)


class _FakeBlock:
    """Минимальный stand-in для блока контента из claude_agent_sdk."""
    def __init__(self, *, name: str | None = None, input: Any = None,
                 id: str | None = None, tool_use_id: str | None = None,
                 is_error: bool = False, text: str | None = None) -> None:
        if name is not None:
            self.name = name
        if input is not None:
            self.input = input
        if id is not None:
            self.id = id
        if tool_use_id is not None:
            self.tool_use_id = tool_use_id
            self.is_error = is_error
        if text is not None:
            self.text = text


def _fake_msg(blocks: list[_FakeBlock]) -> Any:
    return SimpleNamespace(content=blocks)


@_TRANSPORT_SKIP
def test_fallback_triggers_on_empty_final_after_tool_chain(
    monkeypatch: pytest.MonkeyPatch, tmp_repo: Path,
) -> None:
    """Сценарий fb-class-007: 3 tool_use/tool_result + пустой финальный текст
    → fallback должен сработать, итоговый answer непустой, инцидент
    залогирован в events.jsonl как fallback_synth."""
    from pulse import chat
    from pulse.config import PATHS

    # mock SDK-клиента: эмулируем tool-цепочку + пустой текстовый блок
    messages = [
        _fake_msg([
            _FakeBlock(name="mcp__pulse-tools__predict_attrition",
                       input={"top_n": 5}, id="tu_1"),
            _FakeBlock(tool_use_id="tu_1"),
            _FakeBlock(name="mcp__pulse-tools__get_employee_profile",
                       input={"emp_id": "emp_001"}, id="tu_2"),
            _FakeBlock(tool_use_id="tu_2"),
            _FakeBlock(text=""),  # пустой финал
        ]),
    ]

    class _FakeClient:
        def __init__(self, *a: Any, **kw: Any) -> None: ...
        async def __aenter__(self) -> "_FakeClient": return self
        async def __aexit__(self, *a: Any) -> None: ...
        async def query(self, _q: str) -> None: ...
        async def receive_response(self) -> AsyncIterator[Any]:
            for m in messages: yield m

    monkeypatch.setattr("claude_agent_sdk.ClaudeSDKClient", _FakeClient, raising=False)

    synth_calls: list[dict[str, Any]] = []
    async def _fake_synth(*, question: str, tool_summary: str, model: str) -> str:
        synth_calls.append({"question": question, "summary_len": len(tool_summary),
                            "model": model})
        return "BLUF: топ-5 риска атриции — emp_001…emp_005. Главные факторы: …"
    monkeypatch.setattr(chat, "_fallback_synthesize", _fake_synth, raising=False)

    import asyncio
    out = asyncio.get_event_loop().run_until_complete(
        chat.handle_chat("дай топ-5 риска атриции")
    )

    assert out["answer"], "после fallback ответ не должен быть пустым"
    assert "топ-5" in out["answer"]
    assert len(synth_calls) == 1, "fallback должен сработать ровно раз"

    events_path = PATHS.logs / "events.jsonl"
    assert events_path.exists()
    rec = json.loads(events_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["kind"] == "fallback_synth"
    assert rec["message_id"] == out["message_id"]


@_TRANSPORT_SKIP
def test_fallback_skipped_on_normal_final(monkeypatch: pytest.MonkeyPatch,
                                            tmp_repo: Path) -> None:
    """Регрессия: если LLM сама отдала содержательный финал, fallback не
    срабатывает и не вешает лишний LLM-вызов."""
    from pulse import chat

    messages = [_fake_msg([
        _FakeBlock(name="mcp__pulse-tools__list_employees",
                   input={"limit": 10}, id="tu_1"),
        _FakeBlock(tool_use_id="tu_1"),
        _FakeBlock(text="BLUF: всего 100 активных сотрудников. Распределение по "
                         "отделам: …"),
    ])]

    class _FakeClient:
        def __init__(self, *a: Any, **kw: Any) -> None: ...
        async def __aenter__(self) -> "_FakeClient": return self
        async def __aexit__(self, *a: Any) -> None: ...
        async def query(self, _q: str) -> None: ...
        async def receive_response(self) -> AsyncIterator[Any]:
            for m in messages: yield m

    monkeypatch.setattr("claude_agent_sdk.ClaudeSDKClient", _FakeClient, raising=False)

    synth_calls: list[Any] = []
    async def _fake_synth(**kw: Any) -> str:
        synth_calls.append(kw); return "should not be used"
    monkeypatch.setattr(chat, "_fallback_synthesize", _fake_synth, raising=False)

    import asyncio
    out = asyncio.get_event_loop().run_until_complete(
        chat.handle_chat("сколько у нас активных сотрудников")
    )

    assert "BLUF" in out["answer"]
    assert synth_calls == [], "при нормальном финале fallback не должен звониться"


@_TRANSPORT_SKIP
def test_fallback_triggers_on_preamble_only_final(monkeypatch: pytest.MonkeyPatch,
                                                    tmp_repo: Path) -> None:
    """Только «Запрашиваю…»/«Сейчас посмотрю…» без сути — это тоже нарушение
    no-empty-final, fallback должен сработать."""
    from pulse import chat

    messages = [_fake_msg([
        _FakeBlock(name="mcp__pulse-tools__get_collab_neighbors",
                   input={"emp_id": "emp_042"}, id="tu_1"),
        _FakeBlock(tool_use_id="tu_1"),
        _FakeBlock(text="Сейчас посмотрю..."),
    ])]

    class _FakeClient:
        def __init__(self, *a: Any, **kw: Any) -> None: ...
        async def __aenter__(self) -> "_FakeClient": return self
        async def __aexit__(self, *a: Any) -> None: ...
        async def query(self, _q: str) -> None: ...
        async def receive_response(self) -> AsyncIterator[Any]:
            for m in messages: yield m

    monkeypatch.setattr("claude_agent_sdk.ClaudeSDKClient", _FakeClient, raising=False)

    synth_calls: list[Any] = []
    async def _fake_synth(**kw: Any) -> str:
        synth_calls.append(kw)
        return "BLUF: коннекторы emp_042 — emp_011, emp_023, emp_077."
    monkeypatch.setattr(chat, "_fallback_synthesize", _fake_synth, raising=False)

    import asyncio
    out = asyncio.get_event_loop().run_until_complete(
        chat.handle_chat("кто коннекторы для emp_042")
    )

    assert "BLUF" in out["answer"]
    assert len(synth_calls) == 1, "preamble-only финал → fallback должен сработать"
