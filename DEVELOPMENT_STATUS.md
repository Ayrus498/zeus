# ZEUS Phase 1 — Development Status

Last updated: 2026-11-15. Legend: ✅ verified here · 🟡 written, not verifiable in this environment · ❌ not done.

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

- Backend: **59 passed** (`backend/.venv/bin/python -m pytest`) — permissions, path/symlink traversal, tools, runner (timeout/cancel), agent loop with a scripted provider, tool-call parsing, API/WebSocket.
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
- [x] User can inspect file diffs (approval dialog + Monaco diff viewer + A/M/D badges)
- [x] Conversations persist across restarts
- [x] Token usage displayed (Ollama-reported; KV-cache memory is the only estimate and is labelled)
- [~] Zeus Notes demonstration — succeeds with `qwen3-coder:30b`; not with `qwen2.5-coder:7b` (see above)

## Decisions and known limitations

- **Model choice matters.** For unattended multi-file work use `qwen3-coder:30b` (slow on 12 GB VRAM; partial CPU offload). `qwen2.5-coder:7b` is fine for small, supervised edits. The agent has loop/oscillation detection and a 40-step cap (`ZEUS_MAX_STEPS`).
- **Commands are gated, not sandboxed.** They run as the user in a normal shell with cwd = project. Blocked outright: `sudo`, `su`, `rm -rf /|~`, pipe-to-shell, `mkfs`, `dd of=/dev/…`. Trusted mode still asks for delete, installs, git commit/push.
- **Terminal panel**: a command the user types themselves counts as their explicit action (no extra dialog); it is still denied in Read-only mode and for blocked commands.
- **Folder picker** uses `zenity`/`kdialog` through the backend (works in browser dev mode too) instead of the Tauri dialog plugin; `zenity` is a DEB dependency.
- **`ollama list`**: model discovery uses the equivalent `/api/tags` endpoint, which also works for remote endpoints.
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
- Verified from the installed app (2026-11-15): an agent run created a file (`swap.cpp`) end to end.
- Post-install fixes: custom dropdown replaces native `<select>` (WebKitGTK popup was unreadable); copy buttons on code blocks; the text tool-call fallback now also parses Qwen-style `<function=…><parameter=…>` calls; accent color is purple; app icon regenerated with a purple bolt (confirmed updated by the user).
- Usage tab now shows live system metrics (`GET /api/metrics`, `backend/app/metrics.py`): `ollama ps` (loaded models, GPU/CPU split, context, keep-alive countdown), GPU utilization/VRAM/temp/power (nvidia-smi, AMD via sysfs), Ollama process CPU (serve + runner) and RAM, system CPU/RAM. Interactive hover charts, 1.5 s polling only while the tab is open. Verified against a real model under load (GPU 98%, runner 251% of one core). Intel GPUs are not detected (shown as "No GPU detected"). **Confirmed working in the installed app (user, 2026-11-15).**
- Remaining: clean-machine install test.
