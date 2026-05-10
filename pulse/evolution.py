"""6-step evolution cycle (TZ §3.3).

A single cycle is a structural answer to the most-painful class of user
complaints. Steps:

  A. Aggregate feedback (read feedback.jsonl from last_offset)
  B. Classify (LLM → updated feedback-classes.md)
  C. Plan (LLM → YAML plan with intent + diff_targets + acceptance)
  D. Implement (SDK self-edit session with permission_mode="acceptEdits")
  E. Self-test (pytest smoke + protected-path guard)
  F. Commit + bump + tag, with single-Opus scope review on the diff

Anti-oscillator: if the last 3 cycles addressed the same class_id without
closing it, escalate (mark `requires_human_review`, set 7-day cooldown).

Concurrency: a single `data/state/evolution.lock` PID file prevents two
cycles from running simultaneously.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import PATHS, SETTINGS
from .git_ops import (
    changed_paths,
    commit_all_with_msg,
    create_annotated_tag,
    diff_with_head,
    is_protected_path,
    protected_paths_in_changes,
    push_to_origin_with_tags,
    rollback_workdir,
)
from .llm import _query_simple
from .memory import (
    backlog_path,
    feedback_classes_path,
    file_lock,
    patterns_path,
    read_text,
)
from .state import load_state, save_state
from .version_ops import bump, parse as parse_version

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOCK_FILE = "evolution.lock"
COOLDOWN_DAYS = 7
ANTI_OSCILLATOR_WINDOW = 3
DEFAULT_REPLAY_QUESTIONS = 5
SELF_TEST_TIMEOUT_S = 240


def _log_event(kind: str, **payload: Any) -> None:
    PATHS.ensure()
    rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind, **payload}
    with (PATHS.logs / "events.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Lock — one cycle at a time
# ---------------------------------------------------------------------------

class CycleLockBusy(RuntimeError): pass


def _lock_path() -> Path:
    PATHS.ensure()
    return PATHS.state / LOCK_FILE


def _acquire_lock() -> Path:
    p = _lock_path()
    if p.exists():
        try:
            pid = int(p.read_text().strip())
            # if the recorded process is gone, treat the lock as stale
            try:
                os.kill(pid, 0)
                raise CycleLockBusy(f"evolution cycle already running (pid {pid})")
            except OSError:
                p.unlink()
        except ValueError:
            p.unlink()
    p.write_text(str(os.getpid()), encoding="utf-8")
    return p


def _release_lock() -> None:
    p = _lock_path()
    if p.exists():
        try:
            p.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Step A — aggregate feedback
# ---------------------------------------------------------------------------

@dataclass
class FeedbackAggregate:
    new_downvotes: list[dict[str, Any]] = field(default_factory=list)
    new_upvotes: list[dict[str, Any]] = field(default_factory=list)
    downvotes_no_comment_share: float = 0.0
    new_offset: int = 0


def _read_chat_index() -> dict[str, dict[str, Any]]:
    """message_id -> chat record (question, answer, meta)."""
    p = PATHS.logs / "chat.jsonl"
    if not p.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for ln in p.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        try:
            rec = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if "message_id" in rec:
            out[rec["message_id"]] = rec
    return out


def aggregate_feedback() -> FeedbackAggregate:
    fb_path = PATHS.logs / "feedback.jsonl"
    state = load_state()
    last_offset = int(state.get("evolution", {}).get("last_offset", 0))
    if not fb_path.exists():
        return FeedbackAggregate(new_offset=last_offset)

    size = fb_path.stat().st_size
    if size <= last_offset:
        return FeedbackAggregate(new_offset=last_offset)

    with fb_path.open("r", encoding="utf-8") as f:
        f.seek(last_offset)
        chunk = f.read()
    new_offset = last_offset + len(chunk.encode("utf-8"))

    chat_idx = _read_chat_index()
    new_down: list[dict[str, Any]] = []
    new_up: list[dict[str, Any]] = []
    for ln in chunk.splitlines():
        if not ln.strip():
            continue
        try:
            rec = json.loads(ln)
        except json.JSONDecodeError:
            continue
        chat = chat_idx.get(rec.get("message_id"), {})
        merged = {
            "ts": rec.get("ts"),
            "message_id": rec.get("message_id"),
            "verdict": rec.get("verdict"),
            "comment": rec.get("comment"),
            "question": chat.get("question"),
            "answer": chat.get("answer"),
            "tools_called": [t["name"] for t in chat.get("meta", {}).get("tool_calls", [])],
        }
        if rec.get("verdict") == "down":
            new_down.append(merged)
        elif rec.get("verdict") == "up":
            new_up.append(merged)

    no_comment_share = 0.0
    if new_down:
        no_comment_share = sum(1 for d in new_down if not d.get("comment")) / len(new_down)

    return FeedbackAggregate(
        new_downvotes=new_down,
        new_upvotes=new_up,
        downvotes_no_comment_share=round(no_comment_share, 3),
        new_offset=new_offset,
    )


# ---------------------------------------------------------------------------
# Step B — classify complaints
# ---------------------------------------------------------------------------

async def classify_feedback(agg: FeedbackAggregate) -> str:
    """One LLM call. Returns the new feedback-classes.md content (markdown table)."""
    template = read_text(PATHS.prompts / "EVOLUTION_CLASSIFY.md")
    prompt = (
        template
        .replace("{current_feedback_classes}", read_text(feedback_classes_path()))
        .replace("{new_downvotes_json}", json.dumps(agg.new_downvotes, ensure_ascii=False, indent=2)[:20_000])
        .replace("{patterns}", read_text(patterns_path()))
        .replace("{bible}", read_text(PATHS.bible))
    )
    model = "opus" if len(agg.new_downvotes) > 20 else "sonnet"
    raw = await _query_simple(prompt, model=model, kind="evolution_classify")
    return raw.strip()


# ---------------------------------------------------------------------------
# Step C — plan
# ---------------------------------------------------------------------------

@dataclass
class EvolutionPlan:
    intent: str
    class_addressed: str
    diff_targets: list[str]
    plan: str
    expected_effect: str
    risks: str
    acceptance: str
    escalate_to_human: bool = False
    requires_human_review: bool = False
    raw_yaml: str = ""


_YAML_BLOCK_RX = re.compile(r"```(?:yaml)?\s*([\s\S]*?)\s*```", re.MULTILINE)


def _strip_yaml_fences(s: str) -> str:
    m = _YAML_BLOCK_RX.search(s)
    return m.group(1) if m else s


def _parse_plan_yaml(raw: str) -> EvolutionPlan:
    """Tiny hand-rolled YAML reader for the plan format only.

    We intentionally avoid bringing in PyYAML as a hard dep. The format is
    fixed: scalar `key: value` lines plus `key: |` blocks plus lists with
    `- "item"` entries. Anything fancier means a malformed plan.
    """
    text = _strip_yaml_fences(raw)
    lines = text.splitlines()
    fields: dict[str, Any] = {}
    i = 0
    while i < len(lines):
        ln = lines[i]
        m = re.match(r"^(\w+)\s*:\s*(\|)?\s*(.*)$", ln)
        if not m:
            i += 1
            continue
        key, pipe, rest = m.group(1), m.group(2), m.group(3)
        if pipe:
            block: list[str] = []
            i += 1
            while i < len(lines) and (lines[i].startswith("  ") or not lines[i].strip()):
                block.append(lines[i][2:] if lines[i].startswith("  ") else lines[i])
                i += 1
            fields[key] = "\n".join(block).strip()
            continue
        if rest:
            fields[key] = rest.strip().strip('"').strip("'")
            i += 1
            continue
        # multi-line list
        items: list[str] = []
        i += 1
        while i < len(lines) and re.match(r"^\s*-\s+", lines[i]):
            item = re.match(r"^\s*-\s+(.+)$", lines[i]).group(1).strip().strip('"').strip("'")
            items.append(item)
            i += 1
        fields[key] = items

    def _bool(v: Any) -> bool:
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("true", "yes", "1")

    return EvolutionPlan(
        # Cap at 1000 chars (was 240 — that truncated mid-word in v0.2.0
        # and v1.4.1 commit messages). With smart subject extraction in
        # commit_evolution we no longer need a tight cap here.
        intent=str(fields.get("intent") or "").strip()[:1000],
        class_addressed=str(fields.get("class_addressed") or "").strip(),
        diff_targets=list(fields.get("diff_targets") or []),
        plan=str(fields.get("plan") or "").strip(),
        expected_effect=str(fields.get("expected_effect") or "").strip(),
        risks=str(fields.get("risks") or "").strip(),
        acceptance=str(fields.get("acceptance") or "").strip(),
        escalate_to_human=_bool(fields.get("escalate_to_human", False)),
        requires_human_review=_bool(fields.get("requires_human_review", False)),
        raw_yaml=text,
    )


async def make_plan(*, feedback_classes_md: str, evolution_history: list[dict]) -> EvolutionPlan:
    template = read_text(PATHS.prompts / "EVOLUTION_PLAN.md")
    liked_examples_path = PATHS.logs / "chat.jsonl"
    liked_examples = ""
    if liked_examples_path.exists():
        # crude: just take last 5 chat entries — they are not necessarily liked,
        # but the proper join with feedback.jsonl is left as evolution-time work.
        lines = liked_examples_path.read_text(encoding="utf-8").splitlines()[-5:]
        liked_examples = "\n".join(lines)

    prompt = (
        template
        .replace("{bible}", read_text(PATHS.bible))
        .replace("{system_md}", read_text(PATHS.prompts / "SYSTEM.md"))
        .replace("{architecture_md}", read_text(PATHS.architecture_doc))
        .replace("{feedback_classes}", feedback_classes_md)
        .replace("{patterns}", read_text(patterns_path()))
        .replace("{backlog}", read_text(backlog_path()))
        .replace("{liked_examples}", liked_examples[:8_000])
        .replace("{evolution_history}", json.dumps(evolution_history[-5:], ensure_ascii=False))
    )
    raw = await _query_simple(prompt, model="opus", kind="evolution_plan")
    return _parse_plan_yaml(raw)


# ---------------------------------------------------------------------------
# Step D — implement (SDK self-edit session)
# ---------------------------------------------------------------------------

async def _apply_plan_via_sdk(plan: EvolutionPlan, *, time_box_s: int = 600) -> dict[str, Any]:
    """Run a fresh SDK session in `permission_mode='acceptEdits'`. The agent
    edits files itself based on the plan body. We never write to disk from
    here directly — the SDK's built-in Edit/Write/Read do.

    Returns {ok: bool, reason: str, tool_calls: int}.
    """
    from claude_agent_sdk import ClaudeSDKClient  # type: ignore

    from .llm import build_options
    from .tools import build_evolution_server, evolution_allowed_tools

    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return {"ok": False, "reason": "OAuth token missing — skipping self-edit", "tool_calls": 0}

    builtin = ["Read", "Edit", "Write", "Glob", "Grep"]
    options = build_options(
        system_prompt=(
            "Ты — Пульс в эволюционном режиме самосоздания. Тебе передан план "
            "изменения. Внеси правки точечно, согласно diff_targets. Не трогай "
            "защищённые пути из BIBLE.md (P3). Пиши UTF-8 без BOM. Любая "
            "правка — это маленький шаг, не переписывай файл целиком, если не нужно."
        ),
        allowed_tools=builtin + evolution_allowed_tools(),
        mcp_servers={"pulse-tools": build_evolution_server()},
        model="claude-opus-4-7",
        permission_mode="acceptEdits",
        max_turns=15,
        cwd=str(PATHS.repo),
    )

    body = textwrap.dedent(f"""
    План эволюции:

    intent: {plan.intent}
    class_addressed: {plan.class_addressed}
    diff_targets: {plan.diff_targets}

    plan:
    {plan.plan}

    expected_effect:
    {plan.expected_effect}

    risks:
    {plan.risks}

    acceptance:
    {plan.acceptance}

    Сделай правки. Запрещено: править .py файлы под pulse/, BIBLE.md, prompts/SAFETY.md.
    Когда закончишь — кратко (3 строки) опиши, что именно сделал.
    """).strip()

    tool_calls = 0
    try:
        async with asyncio.timeout(time_box_s):
            async with ClaudeSDKClient(options=options) as client:
                await client.query(body)
                async for msg in client.receive_response():
                    content = getattr(msg, "content", None)
                    if content and not isinstance(content, str):
                        for block in content:
                            if getattr(block, "name", None):
                                tool_calls += 1
        return {"ok": True, "reason": "applied", "tool_calls": tool_calls}
    except asyncio.TimeoutError:
        return {"ok": False, "reason": f"timed out after {time_box_s}s", "tool_calls": tool_calls}
    except Exception as ex:
        return {"ok": False, "reason": f"sdk error: {ex}", "tool_calls": tool_calls}


# ---------------------------------------------------------------------------
# Step E — self-test
# ---------------------------------------------------------------------------

@dataclass
class SelfTestResult:
    pytest_ok: bool
    protected_paths_touched: list[str]
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.pytest_ok and not self.protected_paths_touched


def run_self_test() -> SelfTestResult:
    notes: list[str] = []
    bad_paths = protected_paths_in_changes()
    if bad_paths:
        notes.append(f"protected paths touched: {bad_paths}")

    pytest_ok = True
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_smoke.py", "-q"],
            cwd=str(PATHS.repo),
            capture_output=True,
            text=True,
            timeout=SELF_TEST_TIMEOUT_S,
        )
        if proc.returncode != 0:
            pytest_ok = False
            notes.append(f"pytest exit={proc.returncode}; tail={proc.stdout[-500:]}")
    except subprocess.TimeoutExpired:
        pytest_ok = False
        notes.append(f"pytest timeout {SELF_TEST_TIMEOUT_S}s")
    except FileNotFoundError as ex:
        pytest_ok = False
        notes.append(f"pytest missing: {ex}")

    return SelfTestResult(pytest_ok=pytest_ok, protected_paths_touched=bad_paths, notes=notes)


# ---------------------------------------------------------------------------
# Step F — commit + bump + tag (gated by single-Opus review)
# ---------------------------------------------------------------------------

def _bump_level_for(plan: EvolutionPlan) -> str:
    """Heuristic from §3.3 step F.2: MAJOR if BIBLE.md changed; MINOR if a new
    skill or tool; PATCH default."""
    targets = plan.diff_targets or []
    paths = changed_paths()
    all_changed = set(targets) | set(paths)
    if any(p == "BIBLE.md" for p in all_changed):
        return "major"
    if any(p.startswith("skills/") for p in all_changed):
        return "minor"
    if any(p.startswith("pulse/tools/") for p in all_changed):
        return "minor"
    return "patch"


_SUBJECT_MAX = 72


def _build_commit_message(plan: EvolutionPlan, new_version: Any,
                            replay_score: float | None) -> str:
    """Build a Git-conventional commit message: short subject + full body.

    Subject is `v<X.Y.Z>: <first sentence or first 72 chars>`.
    Body carries the full intent (so long Opus-generated intents are
    not lost the way they were in v0.2.0 / v1.4.1) plus provenance
    trailers — `Self-Evolved-By` flags this as an autonomous commit
    so the GitHub timeline distinguishes evolution-cycle commits
    from human-driven ones.
    """
    intent_full = (plan.intent or "").strip()
    short = intent_full
    # Prefer the first sentence as the subject; fall back to a hard
    # 72-char cap with a word-boundary break.
    for sep in (". ", "! ", "? ", "; ", ": "):
        head, _, _rest = intent_full.partition(sep)
        if head and len(head) < len(short):
            short = head
    if len(short) > _SUBJECT_MAX:
        short = short[:_SUBJECT_MAX].rstrip()
        if " " in short[40:]:
            short = short.rsplit(" ", 1)[0]
        short += "…"
    subject = f"v{new_version}: {short}".strip()

    body_lines: list[str] = [""]
    if intent_full and len(intent_full) > len(short.rstrip("…")):
        body_lines.extend([intent_full, ""])
    body_lines.append(f"Class addressed: {plan.class_addressed or 'n/a'}")
    if replay_score is not None:
        body_lines.append(f"Replay score: {replay_score:.2f}")
    body_lines.extend([
        "",
        "Self-Evolved-By: pulse evolution_cycle (autonomous, since v1.5.0)",
        "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>",
    ])
    return subject + "\n" + "\n".join(body_lines)


async def commit_evolution(plan: EvolutionPlan, replay_score: float | None = None) -> dict[str, Any]:
    """Bump → review → commit → tag → push. If review blocks, rollback.

    Push step (since v1.5.0): if `PULSE_GITHUB_PAT` is set in the env,
    the commit + tag are pushed to origin master. Without the PAT the
    commit stays local and the cycle still reports success.
    """
    from . import commit_review

    level = _bump_level_for(plan)
    new_version = bump(level, changelog_line=plan.intent)

    diff = diff_with_head()
    msg = _build_commit_message(plan, new_version, replay_score)
    # Pass only the subject line to commit_review for compactness — the
    # body is mostly trailers and would dilute Opus's attention.
    review_subject = msg.splitlines()[0]
    verdict = await commit_review.review(
        diff=diff, new_version=str(new_version), commit_message=review_subject,
        intent=plan.intent, acceptance=plan.acceptance, replay_score=replay_score,
    )
    if verdict.is_block:
        rollback_workdir()
        _log_event("evolution_aborted", reason="commit_review_block",
                    findings=verdict.findings, new_version=str(new_version))
        return {"ok": False, "reason": "commit_review_block", "verdict": verdict.verdict}

    sha = commit_all_with_msg(msg)
    create_annotated_tag(f"v{new_version}", plan.intent or review_subject)
    _log_event("evolution_committed", version=str(new_version), sha=sha,
                intent=plan.intent, class_addressed=plan.class_addressed)

    # Auto-push to GitHub if PAT configured — see git_ops.push_to_origin_with_tags.
    push_result = push_to_origin_with_tags()
    if push_result.get("pushed"):
        _log_event("evolution_pushed", version=str(new_version), sha=sha)
    else:
        _log_event("evolution_push_skipped",
                    version=str(new_version),
                    reason=push_result.get("reason", "unknown"))

    return {"ok": True, "sha": sha, "version": str(new_version),
             "verdict": verdict.verdict, "pushed": push_result.get("pushed", False)}


# ---------------------------------------------------------------------------
# Anti-oscillator + history
# ---------------------------------------------------------------------------

def _push_history_and_check_oscillation(plan: EvolutionPlan, *, version: str) -> bool:
    """Add this cycle to state.evolution.history. Return True if anti-oscillator should fire."""
    state = load_state()
    history: list[dict] = state["evolution"].setdefault("history", [])
    history.append({
        "ts": _now_iso(),
        "intent": plan.intent,
        "class_addressed": plan.class_addressed,
        "version": version,
    })
    state["evolution"]["history"] = history[-20:]
    save_state(state)
    if plan.class_addressed and len(history) >= ANTI_OSCILLATOR_WINDOW:
        recent = [h["class_addressed"] for h in history[-ANTI_OSCILLATOR_WINDOW:]]
        if all(c == plan.class_addressed for c in recent):
            return True
    return False


def is_in_cooldown(class_id: str) -> bool:
    state = load_state()
    until = state["evolution"].get("cooldown", {}).get(class_id)
    if not until:
        return False
    try:
        return datetime.fromisoformat(until) > datetime.now(timezone.utc)
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------

@dataclass
class CycleResult:
    triggered: bool = False
    skipped_reason: str | None = None
    plan: EvolutionPlan | None = None
    self_test_ok: bool | None = None
    committed: bool | None = None
    version: str | None = None
    notes: list[str] = field(default_factory=list)


async def evolution_cycle(*, force: bool = False,
                           sdk_apply: bool = True) -> CycleResult:
    """One end-to-end evolution cycle. `force=True` bypasses the threshold check.
    `sdk_apply=False` skips the SDK self-edit step (used in tests)."""
    PATHS.ensure()
    try:
        _acquire_lock()
    except CycleLockBusy as ex:
        return CycleResult(triggered=False, skipped_reason=str(ex))

    try:
        _log_event("evolution_started", forced=force)
        agg = aggregate_feedback()
        if not force and len(agg.new_downvotes) < SETTINGS.downvote_threshold:
            return CycleResult(triggered=False,
                                skipped_reason=f"only {len(agg.new_downvotes)} new downvotes "
                                              f"(threshold {SETTINGS.downvote_threshold})")
        if not agg.new_downvotes:
            return CycleResult(triggered=False, skipped_reason="no new downvotes")

        # B. Classify
        new_classes_md = await classify_feedback(agg)
        if new_classes_md.strip():
            feedback_classes_path().write_text(new_classes_md, encoding="utf-8")

        # C. Plan
        history = load_state()["evolution"].get("history", [])
        plan = await make_plan(feedback_classes_md=new_classes_md,
                                evolution_history=history)
        notes: list[str] = []

        # Auto-apply policy (since v1.4.0): the human-review gate is
        # bypassed for plans that touch only non-protected paths. Per
        # BIBLE P3, self-test (pytest smoke + replay) and the single-
        # Opus commit-review against docs/CHECKLISTS.md are sufficient
        # filters for non-immune-core changes. Escalation is enforced
        # ONLY when the plan tries to modify the immune core
        # (BIBLE.md, prompts/SAFETY.md, pulse/data_engine/schema.py).
        if plan.escalate_to_human or plan.requires_human_review:
            protected_in_plan = [t for t in (plan.diff_targets or [])
                                  if is_protected_path(t)]
            if protected_in_plan:
                log.info("plan flagged human-review and touches protected paths %s — escalating",
                         protected_in_plan)
                notes.append(f"escalated: protected paths in plan {protected_in_plan}")
                from .improvement_backlog import append_entry
                append_entry(plan.intent or "needs human review",
                              provenance=f"evolution:{plan.class_addressed}",
                              human_review=True)
                return CycleResult(triggered=True, plan=plan,
                                    skipped_reason="escalated_to_human",
                                    notes=notes)
            log.info("plan flagged human-review but no protected paths — auto-applying (v1.4.0 policy)")
            notes.append("escalation bypassed: no protected paths in plan")

        if plan.class_addressed and is_in_cooldown(plan.class_addressed):
            return CycleResult(triggered=True, plan=plan,
                                skipped_reason=f"class {plan.class_addressed} in cooldown",
                                notes=notes)

        # D. Implement
        if sdk_apply:
            apply_result = await _apply_plan_via_sdk(plan)
            notes.append(f"sdk_apply: {apply_result['reason']}")

        # E. Self-test
        st = run_self_test()
        if not st.ok:
            rollback_workdir()
            _log_event("evolution_aborted", reason="self_test_failed", notes=st.notes)
            # commit feedback-classes.md offset advance still — we did *learn* the classes
            state = load_state()
            state["evolution"]["last_offset"] = agg.new_offset
            save_state(state)
            return CycleResult(triggered=True, plan=plan, self_test_ok=False,
                                committed=False, notes=st.notes)

        # F. Commit + bump + tag
        commit_res = await commit_evolution(plan)
        if not commit_res["ok"]:
            return CycleResult(triggered=True, plan=plan, self_test_ok=True, committed=False,
                                notes=notes + [commit_res["reason"]])

        # state — advance offset, log version, anti-oscillator check
        oscillating = _push_history_and_check_oscillation(plan, version=commit_res["version"])
        state = load_state()
        state["evolution"]["last_offset"] = agg.new_offset
        state["evolution"]["last_version"] = commit_res["version"]
        state["evolution"]["last_run_ts"] = _now_iso()
        if oscillating and plan.class_addressed:
            until = (datetime.now(timezone.utc) + timedelta(days=COOLDOWN_DAYS)).isoformat(timespec="seconds")
            state["evolution"].setdefault("cooldown", {})[plan.class_addressed] = until
            from .improvement_backlog import append_entry
            append_entry(f"escalate: фундаментальный фикс для {plan.class_addressed} не получается 3 цикла подряд",
                          provenance=f"evolution:{plan.class_addressed}",
                          human_review=True)
            notes.append(f"anti-oscillator: cooldown until {until}")
        save_state(state)

        return CycleResult(triggered=True, plan=plan, self_test_ok=True,
                            committed=True, version=commit_res["version"], notes=notes)
    finally:
        _release_lock()


__all__ = [
    "evolution_cycle",
    "aggregate_feedback",
    "classify_feedback",
    "make_plan",
    "run_self_test",
    "commit_evolution",
    "is_in_cooldown",
    "CycleResult",
    "EvolutionPlan",
    "FeedbackAggregate",
    "SelfTestResult",
]
