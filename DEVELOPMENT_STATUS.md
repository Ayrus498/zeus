# ZEUS Phase 1 — Development Status

Last updated: 2026-11-15 (after first install and user testing). Legend: ✅ verified here · 🟡 written, not verifiable in this environment · ❌ not done.

## Summary

The backend, agent, permission engine, UI and persistence are implemented and verified against a **real local Ollama**
(no mocked model responses). The **Tauri shell was compiled and the .deb built, installed and launched on the developer's Ubuntu machine**
(user-confirmed 2026-11-15). Install created `/usr/lib/zeus/{backend,venv}` with all dependencies, plus `/usr/bin/zeus`
and the desktop entry. An agent run from the installed app created a file (user-confirmed). Not yet exercised: a test on a second, pristine machine.

## Milestones

| # | Milestone | Status | Evidence |
|---|---|---|---|
| 1 | UI foundation | ✅ (browser + Tauri window) | React/Tailwind/shadcn-style UI renders in Chromium; screenshots reviewed. `vite build` + `tsc` clean. Production bundle loaded under the exact CSP from `tauri.conf.json` with zero violations (Monaco included) |
| 2 | Ollama integration | ✅ | Status, model list (19 models discovered dynamically, none hard-coded), `/api/show` metadata (native context, tool capability, KV geometry), context presets + "max supported", streaming, configurable endpoint |
| 3 | Project workspace | ✅ | Add folder (native zenity picker via backend), create empty folder, switch, remove-without-deleting (tested), tree, permission validation, resume latest session on open |
| 4 | Coding agent | ✅ | 9 tools with schema validation; native tool calls + text-JSON fallback; real runs created files, edited them, created a venv, installed packages, ran pytest |
| 5 | Permissions | ✅ | 4 modes; backend-enforced; scoped session grants; diff shown for create/edit/delete; exact command shown; 20+ permission/tool tests; UI approve/reject clicked in a real browser against a real model |
| 6 | Terminal & history | ✅ | Streaming stdout/stderr, exit codes, timeout, cancel (process-group kill), history (↑/↓), persisted executions; sessions create/rename/delete/resume; verified across backend restart (81 messages, 22 executions, file changes) |
| 7 | Packaging & testing | ✅ | Rust compiled, DEB built with `packaging/build-deb.sh`, installed via `apt`, app launches. `postinst` created the venv and installed deps (`/usr/lib/zeus/venv`) |

## Tests run

- Backend: **225 passed** (`backend/.venv/bin/python -m pytest`) — permissions, path/symlink traversal, tools, runner (timeout/cancel), agent loop with a scripted provider, tool-call parsing, API/WebSocket.
- Frontend: **2 passed** (vitest) + typecheck + production build.
- Real-model runs (`scripts/e2e_demo.py`, same WebSocket protocol as the UI; an auto-approver stands in for the human and every approval is logged).
- UI run (Playwright + system Chromium → UI → backend → `qwen2.5-coder:7b`): approval dialogs appeared with real content; **Approve Once** wrote `hello.py` to disk; **Reject** on `python3 hello.py` ran nothing.

## The "Zeus Notes" demonstration (real models, nothing mocked)

Prompt: *"Create a simple FastAPI-based notes application with SQLite. Implement CRUD. Include requirements.txt, README.md and unit tests."* — Approval mode, 16K context.

| Model | Result |
|---|---|
| `qwen3-coder:30b` | ✅ **Project works.** Created `app/{__init__,main,database}.py`, `requirements.txt`, `README.md`, tests; created a venv; approved installs; iterated on test failures. Final state: **5 tests pass**, and the app served create/list/update/delete correctly over HTTP (404 after delete). The run was cut off by the 40-step cap while still polishing. ~35 tok/s, 45%/55% CPU/GPU at 16K ctx |
| `qwen2.5-coder:7b` | ❌ **Did not converge** in 4 attempts (25–40 steps). Pipeline worked every time (plan → files → venv → install → tests → edits) but the model wrote buggy code and broke indentation while "fixing" it, then oscillated. The 7B does not reliably clear this task unattended |

Caveats found in the 30B output (model quality, not platform): its `requirements.txt` pins old versions and omits `httpx`, so tests pass only because the agent installed `httpx` into the venv by hand.

Steps 14–15 (close/reopen, resume) were verified by restarting the backend and reloading the UI: the project, session, messages, executions and file changes all came back, and the UI re-opened the latest conversation.

## Acceptance criteria

- [x] Native Linux desktop application launches — confirmed on the dev machine after `apt install`
- [x] DEB package builds and installs — `zeus_0.1.0_amd64.deb` (3.3 MB) built and installed via apt
- [x] Ollama detected automatically
- [x] Installed models listed dynamically
- [x] User can select a model
- [x] Context window configurable (8K/16K/32K/64K/model max, capped by model metadata) with memory-fit warning labelled *estimate*
- [x] User can select a local project folder (native picker via zenity; Tauri dialog not needed)
- [x] User can enter prompts
- [x] Responses stream in real time
- [x] AI can read / create / edit files
- [x] File operations respect permissions
- [x] Terminal execution respects permissions
- [x] User can inspect file diffs (inline approval card + Monaco diff viewer + A/M/D badges)
- [x] Conversations persist across restarts
- [x] Token usage displayed (Ollama-reported; KV-cache memory is the only estimate and is labelled)
- [~] Zeus Notes demonstration — succeeds with `qwen3-coder:30b`; not with `qwen2.5-coder:7b` (see above)

## Decisions and known limitations

- **Model choice matters.** For unattended multi-file work use `qwen3-coder:30b` (slow on 12 GB VRAM; partial CPU offload). `qwen2.5-coder:7b` is fine for small, supervised edits. The agent has loop/oscillation detection and a 40-step cap (`ZEUS_MAX_STEPS`).
- **Commands are gated, not sandboxed.** They run as the user in a normal shell with cwd = project. Blocked outright: `sudo`, `su`, `rm -rf /|~`, pipe-to-shell, `mkfs`, `dd of=/dev/…`. Trusted mode still asks for delete, installs, git commit/push.
- **Terminal panel**: a command the user types themselves counts as their explicit action (no extra dialog); it is still denied in Read-only mode and for blocked commands.
- **Folder picker** uses `zenity`/`kdialog` through the backend (works in browser dev mode too) instead of the Tauri dialog plugin; `zenity` is a DEB dependency.
- **`ollama list`**: model discovery uses the equivalent `/api/tags` endpoint, which also works for remote endpoints.
- **Model guide**: the catalog is curated by hand and will go stale; each entry links to its ollama.com page. Download size/fit figures are estimates.
- **Offline install**: the DEB bundles the backend's Python wheels (`packaging/make-wheelhouse.sh`, ~4 MB, built once at build time) and `postinst` installs them with `--no-index`, so installing needs no network. Wheels are tied to the build machine's Python (cp312, x86_64); on a different Python `postinst` says so and falls back to PyPI. Runtime deps trimmed to fastapi, uvicorn, websockets, httpx. Not bundled: Ollama, models, git, and system packages (apt resolves `python3`, `python3-venv`, `zenity`, WebKitGTK). **Confirmed in the installed build (user, 2026-11-15).**
- **Python shim**: Ubuntu has no bare `python`; commands get an appended-PATH shim to `python3` because models habitually write `python`.
- Monaco is bundled locally (no CDN) with Monarch grammars only; no TS/JSON language-service workers.
- GPU monitoring: only `nvidia-smi` free/total VRAM is read, for the context warning. No live GPU panel.
- Not implemented by design (Phase 2): multi-agent orchestration, memory/RAG, dashboards, MCP.

## Build and install (verified)

```bash
packaging/build-deb.sh     # tests, UI build, cargo fetch (slow-network tolerant), `tauri build --bundles deb`
sudo apt install ./src-tauri/target/release/bundle/deb/zeus_0.1.0_amd64.deb && zeus
```

Notes from the first real build:
- `beforeBuildCommand` was removed from `tauri.conf.json` (it ran from `frontend/`); `build-deb.sh` builds the UI itself.
- crates.io was very slow from this network; `build-deb.sh` raises cargo's HTTP timeouts and pre-fetches crates.
- (fixed) `postinst` used to pip-install from PyPI at install time and printed retry warnings on slow links; wheels are now bundled.
- `apt` prints `N: Download is performed unsandboxed as root` when the .deb is under `$HOME`; harmless.

## Changes since the first install (user-testing iterations)

All verified on the developer's machine; items marked *(browser)* were verified in Chromium against a real Ollama model and not yet re-checked in the installed desktop build.

| Change | Detail | Verified |
|---|---|---|
| Offline install | `.deb` bundles the backend's Python wheels (`packaging/make-wheelhouse.sh`, ~4 MB); `postinst` installs with `--no-index`, falls back to PyPI on a different Python. Runtime deps trimmed to fastapi, uvicorn, websockets, httpx | ✅ installed build (user) |
| Custom dropdown | Replaced native `<select>` (WebKitGTK popup was white-on-white and dropped clicks) with a dark in-app dropdown; model dropdown widened to 300 px | ✅ installed build (user); width *(browser)* |
| Text tool-call fallback | Parses Qwen-style `<function=name><parameter=key>…` calls in addition to JSON, so weaker models still create files | ✅ installed build (user) |
| Theme | Accent changed to purple `#8b5cf6` (buttons, icons, focus); app icon regenerated (all sizes, `.ico`, `.icns`) | ✅ installed build (user) |
| Usage tab metrics | `GET /api/metrics` (`backend/app/metrics.py`): `ollama ps` (GPU/CPU split, context, keep-alive), NVIDIA/AMD GPU util/VRAM/temp/power, Ollama process (serve + `llama-server` runner) and system CPU/RAM; interactive hover charts, polls only while the tab is open. Intel GPUs not detected | ✅ installed build (user); real-load check: GPU 98%, runner 251% of one core |
| Copy buttons | Copy icon on code blocks, tool results, command output, diffs, approval commands and the file viewer | ✅ |
| "Thunderstorm is approaching…" | Animated working indicator while the agent runs ("Waiting for your approval…" while blocked on you) | *(browser)* |
| Delete session / remove project | Native `confirm()` is a silent no-op in the webview, so deletes never ran; replaced with an in-app confirm dialog and error toasts. Cascade delete verified | *(browser)* |
| Database self-heal | The backend opens SQLite per call and only created its data folder at startup; deleting `~/.local/share/zeus` while the app ran made every request 500 (this broke "New project > Create"). It now recreates the folder/schema automatically (test added). Data in a deleted folder is not recoverable | ✅ test; fix needs rebuild |
| Inline approvals | Approval is now a card in the conversation (diff / exact command, Approve Once / Approve for Session / Reject) instead of a modal; `PermissionDialog` removed. Nothing is written before approval | *(browser, real model `qwen3-coder-16k`)*; Reject path not re-clicked in the UI (same handler as before, backend-enforced) |
| Models window / model guide | (Originally a right-pane tab; **now a popup window opened from Settings → Models**: search box top-left, collapsible family list on the left - Qwen, DeepSeek, Llama, Gemma, Mistral, Phi, OpenAI, IBM Granite, Embeddings, Other - with installed/loaded/best markers, Overview + Auto mode, details and Download/Unload/Use on the right; installed custom variants such as `qwen3-coder-16k` are attached to their family; collapsed state persists; a download badge shows on the Settings button; `frontend/src/lib/modelGroups.ts`, 25 unit tests, verified in a browser against 19 real models) Curated catalog of 32 Ollama models (`backend/app/providers/catalog.py`: purposes, ZEUS fit, capabilities, approx. sizes, GPU/RAM fit estimate), installed list with **Unload** (`keep_alive: 0`) and **Use**, **Download** via `/api/pull` with aggregated layer progress, Cancel/Retry/Dismiss. Download status and errors are persisted in SQLite (`downloads` table; survive restarts, interrupted ones are marked). Errors show in the panel and as a persistent toast. When Ollama is missing/empty an in-app setup guide links to the Ollama install/docs pages (opened through an allow-listed `/api/open-url`, never arbitrary URLs). `GET /api/ollama/{catalog,overview}`, `POST /api/ollama/{pull,unload}` | real Ollama: overview, re-pull of an installed model, real "manifest does not exist" error, load → Unload; fake Ollama server (multi-layer 4.7 GB progress, failed pull, unload) in browser; 20 new backend tests. Catalog is a hand-maintained snapshot (sizes approximate), not a live feed; a real multi-GB download was not run |
| Resizable panes | Left (min 256 px) and right (min 440 px) panes drag-resize, double-click resets, keyboard arrows, widths remembered; center never below 420 px | *(browser)* |
| `.zeus/` session memory | Conversations mirrored to `<project>/.zeus/sessions/<uid>.json` (debounced, atomic, mode 0600, `.gitignore` = `*`) and settings + project list to `~/.zeus/settings.json`; restored on startup, on project add/open, and after a mid-run database wipe. `sessions.uid` column + migration for old DBs. Permissions are never loaded from files; imports are validated/size-limited; the agent cannot write to or list `.zeus/`. Settings dialog explains the storage | 11 new tests (restore after DB loss, idempotency, delete/readd, hostile files, agent write block, migration, debounce); real-model E2E: file created, DB wiped, backend restarted → project, session and messages restored and the model answered a follow-up from the restored context |
| Dependency security | `npm audit` flagged Monaco's bundled DOMPurify (critical, XSS advisories). Pinned `dompurify@3.4.18` via `overrides` in `frontend/package.json` instead of `npm audit fix --force` (which would downgrade Monaco and break the editor imports). `npm audit`: 0 vulnerabilities; Monaco still renders with syntax highlighting *(browser)* | ✅ |
| Auto model mode | "✨ Auto" option in the model dropdown. `backend/app/agent/router.py`: rule-based intent classifier (code / think / keep for short follow-ups, `/think` `/code` overrides) + scoring of installed chat models from catalog knowledge, Ollama's real capability flags (`/api/show`), GPU/RAM fit, size prior (tiny models and CPU-spilling dense models are penalised), already-loaded bonus and hysteresis against pointless reloads. One decision per prompt, kept for the whole agent run; reasoning-only models (no tool calling) run that turn with tools disabled. Emits a `model_selected` event (shown in chat, header chip, status bar); `sessions.last_model` stored and mirrored to `.zeus`. Models tab "Auto mode" card: current coding/thinking picks, per-role pins, warnings (reload cost, no-tools), download suggestions, dry-run "try a prompt". `GET /api/auto/plan`, `POST /api/auto/preview`, `PUT /api/auto/pins` | 27 new tests (classifier table, profiles, picks, hysteresis, pins, engine routing/no-tools, regression for a tiny-model mis-pick found on real hardware); real models: coding prompt → `qwen2.5-coder:7b` created and ran a file, explanation prompt → `deepseek-r1:14b`, "yes thanks" kept it; UI *(browser)*. Heuristic routing can misclassify; the pick is always shown and overridable |
| Streaming code blocks | Fixed flicker and runaway height while a long code block streams. Causes: inline `components` made React rebuild every code block per token; whole-message re-highlight with language auto-detect; no height cap; per-token scroll jumps. Fix: module-level markdown components + `memo`, `detect:false`, tokens coalesced (80 ms), code blocks capped at `min(26rem,55vh)` with their own scrollbar that follows the newest line (until you scroll up), chat auto-scroll only while you are at the bottom. Measured with a real model streaming a ~250-line program: code-block rebuilds **1032 → 0**, tallest block **2864 px → 416 px**, longest frame **128 → 40 ms**, scroll events **115 → 5** | *(browser, real model)* |
| Files not created (model prints code / bad tool-call JSON) | Reported with a 6.5 KB "generate one HTML file" prompt: no `index.html`. Root causes found in the stored session: (1) the model printed the page in chat as the prompt said ("output ONLY the HTML") and never called `create_file`; (2) a follow-up `create_file` call came back as JSON text with one unescaped quote in 16 KB of HTML, so the strict parser dropped it. Fixes: tolerant `create_file` JSON recovery (`toolparse.lenient_file_call`; recovers the user's real failing message: 16,326 chars, valid HTML); up to 2 automatic retries when a reply looks like a broken tool call; prompt rule "generated files are saved, not just shown"; **rescue** (`agent/rescue.py`): a clear "generate a file" request answered with exactly one large code block and no file written becomes a normal `create_file` call through the approval flow (never overwrites); "Save as file" button on every finished code block (`POST /api/projects/{id}/files`, same path rules, read-only blocked). Also fixed an Auto-routing flaw found while testing (a loaded model spilling to CPU outranked a fast GPU model) | 21 new tests; Save button *(browser)*; real-model run of the user's exact 6.5 KB prompt (`qwen2.5-coder:7b` via Auto): reply was printed, rescue saved `index.html` (21,001 chars, valid HTML, all requested sections present) after approval. That run also exposed and fixed two bugs (file named `zeus.yaml` from an example in the spec; Auto preferring a CPU-spilling loaded model) |
| Attach a `.md` → mission loop | Paperclip / drag-and-drop attaches a Markdown document (≤200 KB) that ZEUS runs as a **mission**: parse objective + acceptance criteria (checkboxes or Acceptance-criteria-style sections; otherwise the model extracts them) → agent rounds with the normal tool loop → evidence (files written, commands + exit codes, files named in criteria checked on disk) → reviewer pass per criterion → ZEUS computes acceptance itself and **objective checks overrule the reviewer** (tests-pass needs the latest test command to exit 0, named files must exist; tests are **re-run once to confirm**, catching flaky/state-dependent suites) → unmet criteria + failing output go back to the agent. Stops: accepted / Stop / stalled / round limit. Mission card (criteria ✓✗ with evidence, round, reviewer feedback, how requests were prepared), pinned progress bar, persisted in `missions` table + `.zeus`. `engine.run` refactored into a reusable `loop()`. Weak-model guards found on real runs: plan-only replies pushed to act, printed `# file.py` blocks saved via `create_file` (approval flow), fabricated `<tool_response>` text rejected, a denial is never nagged | 40 new tests (parsing, verdicts, accept/retry/stall/limit/stop, hard checks, rescue, flaky-test confirmation, permissions, WS validation, `.zeus` round trip); real models: `qwen3.5:9b` **converged in 4 rounds (268 s)**, reviewer-found test bugs fixed from feedback, independent `python3 -m unittest` OK; `qwen2.5-coder:7b` creates some files but stalls (reported honestly, never falsely accepted); UI attach/preview/validation, live card updates, restored card *(browser)* |
| Model-aware prompting | Per-model requests from Ollama `/api/show` (capabilities, the creators' recommended `parameters`) + family rules: sampling options, `think` only for thinking-capable models (off for fast tool steps on hybrid Qwen3, on for review; levels for gpt-oss), system prompt folded into the user turn for DeepSeek-R1/gemma, temperature floor for R1/QwQ, text tool protocol + no `tools` param for models without native tool calling (Ollama would 400), stale `<think>` stripped, `num_ctx` raised to fit big documents (announced once per size). System prompt no longer invites plan-only replies | 12 new tests incl. what is actually sent to Ollama; observed on real hybrid model (`qwen3.5:9b`: temp 0.7 cap, thinking off, ctx raised to 32K) |
| Attach any document (MarkItDown) | `POST /api/convert?filename=` (raw body, ≤25 MB) + `GET /api/convert/formats`; the paperclip accepts pdf/docx/pptx/xlsx/xls/csv/html/json/xml/epub/ipynb/zip/txt/md, shows a "converting…" state, a preview and a "converted from" note, names the result `<file>.md` and feeds it into the existing mission flow. Conversion (`backend/app/convert/`) runs in an **isolated subprocess**: hard timeout (killed), CPU/memory/file-size rlimits, **network blocked after imports**, an explicit allow-list of converters (no audio/image/YouTube/Bing/Wikipedia/RSS/Outlook/Azure; no plugins), binary formats get no plain-text fallback (found: MarkItDown otherwise returns raw bytes/mojibake for a corrupt file), file-signature checks, output cut at 200 KB with a visible note, scanned PDFs give an OCR hint. Images/audio are rejected on purpose | 38 new tests (14 formats incl. built-in fixtures, rejects, corrupt/empty/textless files, truncation, filename sanitising, missing-package 503, timeout incl. a really hung process, network/rlimit/converter-allow-list proofs, endpoint, converted doc → mission); *(browser)* real PDF/DOCX/PPTX/XLSX/HTML attach, errors, truncation warning, mission started from a converted HTML. Packaging: wheelhouse is now 51 wheels / **103 MB** (deb ~100 MB, ~500 MB installed; offline `postinst` verified: 12 s, converts documents with the network cut). Not verified: scanned-PDF OCR (unsupported), audio/images (unsupported) |
| Repository | Source published to `github.com/Ayrus498/zeus` (MIT), README rewritten | ✅ |

## Packaged build verification (2026-11-15, rebuilt `.deb`)

`packaging/build-deb.sh` (backend tests, UI build, cargo, `tauri build`) produced `zeus_0.1.0_amd64.deb` (105 MiB, 225 tests passing during the build). Checked from the artifact itself, not the source tree:
- package contents: 51 bundled wheels, all new modules (`convert/`, `agent/mission.py`, `modelprefs.py`, `rescue.py`, `router.py`), postinst/prerm, desktop entry, icon, **no `__pycache__`** (build script now clears them); the built UI bundle's hash is embedded in the binary
- extracted the package and ran its real `postinst` with the network cut: 11 s, 394 MB venv, every dependency imports (incl. markitdown, pandas, magika)
- started the **packaged backend** (its own code + venv) on a separate port and ran real-model missions against it: (B) a `.docx` converted by MarkItDown → criteria extracted → file created → accepted in 8 s; (A) the build's own UI bundle (API address rewritten to the test port) in Chromium: attach `.docx` (converted note) and `.md` (preview), Run mission with `qwen3.5:9b` → card working/reviewing across 3 rounds → **accepted**, pinned bar shown, no raw document leaked, an independent `python3 -m unittest` passes twice in a row
- **Not verified**: installing with `apt` (needs sudo), launching the native Tauri window against the installed package (it would attach to the already-running backend on port 8765)

## Still open

- Clean-machine install test (second, pristine Ubuntu/Debian machine; ideally a different Python than 3.12).
- Re-check the *(browser)* items in the rebuilt desktop app.
- Native folder picker (zenity) flow for **New project** after the database self-heal fix, in the desktop app.
