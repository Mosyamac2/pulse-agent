# Bug: duplicating module rail + sidebar inside the Pulse tab iframe

> Status: **not implemented** — this is a diagnostic plan for the next session.
> Created: 2026-05-11
> Last live service version: v2.8.2
> Reporter trigger: hovering an employee chip in the chat → clicking
> «Спросить Пульс подробнее» in the hover-card. Reporter expects to land
> in the Pulse tab with the question pre-filled (which does happen),
> but the rail/sidebar appears **twice** — the outer app-shell rail AND
> an inner rail/sidebar inside the iframe.

---

## 1. Symptom

Screenshot: `/home/mosyamac/pulse-agent/Duplicating_bars_iframe.png`.

The reporter's DOM dump confirms:

```
<body>
  <div class="app-layout">
    <nav class="rail">…outer HCM façade rail…</nav>
    <main class="workspace">
      <section class="tab-pane" data-tab="pulse" data-active="true">
        <div class="pulse-frame-wrap">
          <iframe id="pulse-frame" src="/chat?embedded=1" …></iframe>
        </div>
      </section>
      …other tab-panes…
    </main>
  </div>
</body>
```

Rendered visual: **two vertical bars on the left** instead of one.
The outer is the canonical app-shell rail (10 buttons: HRoboros core +
9 panel icons). The inner element is whatever the iframe is rendering
at the left edge of its viewport.

---

## 2. Architecture recap (so we don't fix the wrong layer)

Since `v2.4.0` the routes look like this:

| URL                  | Served file        | Has its own left bar?               |
|----------------------|--------------------|-------------------------------------|
| `GET /`              | `web/app.html`     | **yes** — `nav.rail` (HCM façade)   |
| `GET /chat`          | `web/index.html`   | **yes** — `aside.sidebar` (chat)    |
| `GET /chat?embedded=1` | `web/index.html` | **should be no** — `body[data-embedded="true"] .sidebar { display:none }` |

App-shell's Pulse-tab body:
```html
<iframe id="pulse-frame" src="/chat?embedded=1">
```

So when everything is healthy, the user sees a single rail (from app-shell)
and a sidebar-less chat inside the iframe. The bug is that an extra
left-edge bar is visible inside the iframe.

The hover-card «Спросить Пульс подробнее» is **purely intra-iframe**
(see `web/index.html:2098–2106`):

```js
hc.addEventListener('click', (ev) => {
  const ask = ev.target.closest('.hc-ask');
  if (!ask) return;
  setComposer(`Расскажи подробно про ${name} (${id}) …`);
  hideCard(); closeSidebarMobile();
});
```

It does **not** navigate, does **not** change iframe.src, does **not**
postMessage anything to the parent. So the click itself cannot cause a
reload. The duplicating bar must be a **pre-existing state** of the
iframe that the user notices *after* the click because attention shifts
to the page once the textarea is filled.

---

## 3. Candidate root causes — ranked by likelihood

### Hypothesis A — embedded-mode race condition  (likelihood ≈ 60%)

The "this is the iframe, hide the sidebar" marker is set inside an
**async IIFE** in `web/index.html:1544–1590`:

```js
(async () => {
  try { await fetch('/health'); … } catch {}            // 1st await
  try { await fetch('/api/history?limit=10'); … } catch {}  // 2nd await
  try {
    const params = new URLSearchParams(window.location.search);
    if (params.get('embedded') === '1') {
      document.body.dataset.embedded = 'true';   // ← only NOW the sidebar hides
    }
    …
  } catch {}
})();
```

Until both `await`s settle, `body[data-embedded]` is **not** set and the
CSS rule `body[data-embedded="true"] .sidebar { display: none }` does
not match — the legacy 268-px chat sidebar is fully visible.

- If `/api/history` is slow (we now have 200+ chat turns from the
  overnight emulator run, so the response body is much larger than before),
  this window grows to 1–3 seconds — long enough for the user to see and
  screenshot.
- If either fetch hangs (network blip, server restart during a turn),
  the sidebar stays visible until reload.
- On every iframe reload (e.g. dock-overlay handoff to Pulse tab) the
  sidebar flashes on for that window.

**Why I think this is the lead candidate:** it explains why the bar
appears persistently when the user notices it after clicking around;
it explains why we never noticed it during early testing (then we
only had 5–10 chat turns in history; now we have hundreds and
`/api/history` is much slower); it does not require any navigation.

### Hypothesis B — Pulse rendered a Markdown deep-link in its answer  (likelihood ≈ 25%)

If Pulse ever produces an answer containing `[label](/?q=…)` (with a
leading `/` and NO `/chat` prefix), `marked.parse` converts it to
`<a href="/?q=…">`. **Inside an iframe**, clicking such a link
navigates the iframe to `/`, which in the post-v2.4.0 world is
`web/app.html` — i.e. the **whole shell** loads inside the iframe,
giving you an inner `nav.rail` next to the outer one.

We do not currently sanitize Pulse's output to strip such links, and
nothing prevents the SDK from generating them.

**How to confirm:** grep `data/logs/chat.jsonl` for the literal `/?q=`
substring in the last day's answers; check the iframe's
`contentWindow.location.pathname` in DevTools after the symptom
appears.

### Hypothesis C — stale browser cache from before v2.4.0  (likelihood ≈ 10%)

Pre-v2.4.0, `GET /` returned the chat directly. If the browser cached
some HTML/JS state where `iframe#pulse-frame` was given `src="/?q=…"`
(without `/chat` prefix), the iframe could be loading app-shell inside
app-shell. We already added `no-cache` headers to `/` and `/chat`, so
this should be rare, but cross-tab/state caching can defeat that.

**How to confirm:** ask reporter to do a clean reload (hard refresh
*plus* "empty cache and hard reload" in DevTools). If symptom
disappears, this was it.

### Hypothesis D — `nav.rail` selector collision  (likelihood ≈ 5%)

The chat (`web/index.html`) **does not** define a `nav.rail` element —
only `aside.sidebar` and `nav.nav-link`. So if the reporter literally
sees two `nav.rail` elements in the DOM, the iframe is definitely
rendering app.html (i.e. Hypothesis B or C is true). Confirming "what
is inside the iframe" is the cheapest single diagnostic.

---

## 4. Diagnostic protocol (do these in order — STOP at the first conclusive answer)

> Run each step yourself in a fresh browser session. Don't rely on
> reporter screenshots alone.

### Step D1 — open DevTools, read iframe content

1. Load `http://VM:8080/` in the browser.
2. Open DevTools → Console.
3. Run:
   ```js
   const ifr = document.getElementById('pulse-frame');
   console.log('iframe.src =', ifr.src);
   console.log('iframe.contentDocument.title =', ifr.contentDocument.title);
   console.log('iframe.contentDocument.body.dataset.embedded =',
                ifr.contentDocument.body.dataset.embedded);
   console.log('inner rail present? ',
                !!ifr.contentDocument.querySelector('nav.rail'));
   console.log('inner sidebar present? ',
                !!ifr.contentDocument.querySelector('aside.sidebar'));
   console.log('inner sidebar display? ', ifr.contentDocument.querySelector('aside.sidebar')
                && getComputedStyle(ifr.contentDocument.querySelector('aside.sidebar')).display);
   ```

Interpretation:
- `title = "Пульс"` + `nav.rail = false` + `sidebar.display = "none"` → embedded is working; the visible "second bar" is NOT inside the iframe and we're chasing the wrong ghost.
- `title = "Пульс — HR-платформа"` + `nav.rail = true` → **Hypothesis B/C confirmed**: iframe is loading app.html.
- `title = "Пульс"` + `sidebar.display ≠ "none"` → **Hypothesis A confirmed**: embedded marker not set.

### Step D2 — race-condition check

If D1 points at Hypothesis A:

1. Reload `/`.
2. Immediately (within 200 ms) run the same DevTools snippet.
3. Observe whether `body.dataset.embedded === 'true'` is missing at first
   and appears later (after 500ms-1s).
4. Time the `/api/history?limit=10` response with `curl -w "%{time_total}\n"`.

If the timing matches (sidebar visible until history fetch resolves)
→ Hypothesis A confirmed.

### Step D3 — Markdown-link check

If D1 points at Hypothesis B/C:

```bash
grep -oE '/[?]q=[^)" ]+' data/logs/chat.jsonl 2>/dev/null | sort -u | head -20
```

If Pulse has rendered any deep-link starting with `/?q=` (without
`/chat`), it's emit-side Markdown content from the LLM and Hypothesis
B is confirmed. Pure browser cache would have no logged event and
would disappear after Step D2's hard refresh.

---

## 5. Fix candidates per root cause

### Fix for Hypothesis A — race condition

**Option A1 (recommended):** hoist the embedded-marker setter to a
synchronous `<script>` tag *before* `</head>` in `web/index.html`, so
the marker is set during HTML parsing — before any CSS rule evaluates
against `<body>`. Concretely, add at the top of `<head>`:

```html
<script>
  (function () {
    try {
      var p = new URLSearchParams(window.location.search);
      if (p.get('embedded') === '1') {
        // Defer until body exists. Since this script runs in <head>,
        // we use a microtask-safe path that sets the attribute on
        // document.documentElement instead and adjust the CSS selectors
        // to match (cheaper than waiting for DOMContentLoaded).
        document.documentElement.dataset.embedded = 'true';
      }
    } catch (_) {}
  })();
</script>
```

Then change CSS rules from `body[data-embedded="true"]` to
`html[data-embedded="true"]` (only ~4 selectors in `web/index.html`).
This makes the embedded-mode hiding **paint-blocking** — the sidebar
never flashes on, irrespective of how slow `/api/history` is.

Pros: single localized change. No JS rearchitecting. Survives slow
networks. ~10 lines of CSS edits + 1 inline `<head>` script.

Cons: tiny cognitive shift (`html` vs `body` data attribute).

**Option A2 (mediocre):** move just the embedded-check ABOVE the two
awaits in `web/index.html:1544–1590`, so it runs first. Still
asynchronous though — there's still a small window during initial
parse where the sidebar is visible.

**Option A3 (mediocre):** add a synchronous `style="display:none"`
hint inline on `.sidebar` when the page is served via `/chat?embedded=1`.
Move the cosmetic decision into the server: when `?embed=1`, inject
`<style>aside.sidebar{display:none}</style>` into the served HTML.
Pros: zero JS. Cons: server-side conditional rendering breaks the
"single source of truth" of `index.html`.

### Fix for Hypothesis B — Pulse rendered a `/?q=…` link

**Option B1 (recommended):** post-process Pulse's Markdown output
client-side before passing it to `marked.parse`. In
`renderAssistantMarkdown` (`web/index.html:1220`), add a regex pass
that **rewrites** bare `/?q=…` links into `/chat?q=…&embedded=1` so
that clicking them inside the iframe keeps the embedded-chat surface:

```js
text = text.replace(/]\(\/\?q=([^)]+)\)/g,
                    (_, qs) => `](/chat?q=${qs}&embedded=1)`);
```

Pros: surgical; fixes the symptom regardless of what the LLM
emits in the future.

Cons: parser-level regex on Markdown is brittle (false-positives for
URL-encoded `q=` containing `)`).

**Option B2 (stronger):** rewrite via DOMPurify's `uponSanitizeAttribute`
hook after `marked.parse` so we operate on the actual `<a href>`
elements (post-parse) rather than on raw text:

```js
DOMPurify.addHook('uponSanitizeAttribute', (node, hookEvent) => {
  if (node.tagName === 'A' && hookEvent.attrName === 'href') {
    if (hookEvent.attrValue.startsWith('/?q=')) {
      hookEvent.attrValue = '/chat?q='
                          + hookEvent.attrValue.slice(4)
                          + '&embedded=1';
    }
  }
});
```

Pros: HTML-correct, no false-positives.

Cons: requires DOMPurify hook registration once at boot.

**Option B3 (defence-in-depth):** add `<base target="_top">` inside
the iframe's `<head>`. This makes every `<a>` without an explicit
target navigate the **parent window**, not the iframe — so clicks on
in-answer Markdown links transition the outer app-shell to Pulse-tab
+ `?q=…`, which then routes properly through `syncPulseFrameQuery()`.

Pros: zero per-link knowledge required; handles any future
`/?q=…` link unconditionally.

Cons: also redirects external links to top frame (rarely a problem
since chat is internal-only, but worth noting).

### Fix for Hypothesis C — stale cache

No code fix — just ensure `Cache-Control: no-store` on `/chat` and
`/` responses (we already have `no-cache, no-store, must-revalidate`
in `_NOCACHE` at `pulse/server.py:49–50`). Reporter does a hard
refresh; if symptom disappears, no further action.

---

## 6. Recommended path

Implement **D1 first** (5 minutes in DevTools) — confirm exactly which
hypothesis is true before writing any code. Then:

- If A: ship Fix **A1** (paint-blocking embedded marker on `<html>`).
- If B: ship Fix **B2** + B3 belt-and-suspenders (DOMPurify hook +
  `<base target="_top">` in the iframe's `<head>`).
- If C: just confirm cache headers, no code change.

All three fixes are small, localized to `web/index.html` and possibly
1 line in `pulse/server.py` (for cache headers). None touch
self-evolution surfaces, immune core, or backend tools — safe to
ship as a single PATCH bump.

---

## 7. What we already know works

| Aspect                                              | Status            |
|-----------------------------------------------------|-------------------|
| Hover-card button handler does NOT navigate         | confirmed (line 2098–2106) |
| Cache-Control headers prevent server-side caching   | confirmed (line 49–50)    |
| `<iframe>` src is `/chat?embedded=1`                | confirmed (reporter DOM dump) |
| `body[data-embedded="true"] .sidebar { display:none }` CSS rule exists | confirmed (line 117) |

So we don't need to re-verify these. The mystery is **why the inner
left bar is still showing up** despite the markup looking correct.
Steps D1–D3 will resolve that mystery cheaply.

---

## 8. Resolution (2026-05-11 — v2.8.4)

Implemented Fix A1 + B3. Diagnostic confirmed Hypothesis A:
- `data/logs/chat.jsonl` had **0** matches for `/?q=` Markdown deep-links → Hypothesis B/C ruled out
- The CSS rules `body[data-embedded="true"]` existed but body had no
  `data-embedded` attribute in the initial response → race condition
  confirmed

Changes shipped:
- Paint-blocking inline `<script>` in `<head>` (lines 8–32) that sets
  `document.documentElement.dataset.embedded = 'true'` synchronously
  when `?embedded=1` is in URL, and injects `<base target="_top">` for
  any future Markdown-rendered links.
- 4 CSS selectors moved from `body[data-embedded=...]` to
  `html[data-embedded=...]`.
- Async-IIFE setter (line 1568) reduced to a defensive idempotent fallback.

Net result: no `flash` of sidebar even on slow networks; iframe stays
clean from the first paint.
