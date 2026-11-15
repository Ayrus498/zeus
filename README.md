# ZEUS ⚡

**A local, Claude-Code-style coding agent for your desktop, powered by [Ollama](https://ollama.com).**
Pick a project folder, pick a local model, describe what you want. ZEUS reads, creates, edits and runs code in
your folder, and every write or command passes through a permission engine you control. Nothing leaves your machine.

> **Status: Phase 1 (single agent).** Working on Ubuntu/Debian as an installable `.deb`. See
> [DEVELOPMENT_STATUS.md](DEVELOPMENT_STATUS.md) for exactly what is verified and what is not.

## Features

- **Native desktop app** (Tauri 2) with a Claude-Code-Desktop-inspired UI: projects sidebar, chat timeline, Monaco file/diff viewer, terminal panel.
- **Any local Ollama model**: models are discovered dynamically (nothing hard-coded); choose context size (8K / 16K / 32K / 64K / model max) with a memory warning.
- **Tool-using agent**: `list_files`, `read_file`, `search_files`, `create_file`, `edit_file`, `delete_file`, `run_command`, `git_status`, `git_diff`. Works with native tool calls and falls back to parsing JSON / `<function=...>` tool calls printed as text by weaker models.
- **Backend-enforced permissions**: read-only / approval / trusted / custom, inline approval cards in the conversation (diffs and the exact command, Approve Once / Approve for Session / Reject), path and symlink validation.
- **Live Usage tab**: `ollama ps` (loaded models, GPU/CPU split, keep-alive), GPU utilization / VRAM (NVIDIA, AMD), Ollama and system CPU/RAM, with interactive hover charts, plus real token counts from Ollama.
- **Persistent sessions** in SQLite, copy buttons on code blocks, streaming terminal with cancel and timeout.
- **Offline install**: the `.deb` bundles its Python dependencies; installing needs no network.

## Architecture

```
Tauri 2 shell ──► React + TS UI ──REST/WebSocket──► Python FastAPI backend ──► Ollama
 (window, backend      (Tailwind, Monaco,             ├ projects   ├ providers (Ollama)
  lifecycle)            zustand)                      ├ sessions   ├ agent loop
                                                      ├ tools      ├ permissions
                                                      ├ terminal   └ SQLite
                                                      ▼
                                              your project folder (edited in place)
```

The **backend owns every file operation and every authorization decision**. The UI only renders requests
and sends the user's answer; a rejected or unanswered request never executes. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Run from source (development)

Requirements: Python 3.10+, Node 20+, [Ollama](https://ollama.com) running with at least one model (tool-capable models such as `qwen3-coder` work best), and `zenity` (native folder picker).

```bash
scripts/run-backend.sh                 # FastAPI on 127.0.0.1:8765 (creates backend/.venv on first run)
cd frontend && npm install && npm run dev   # UI on http://localhost:1420 (works in any browser)
```

Desktop shell (needs the Tauri system libraries, see below):

```bash
sudo apt install libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev \
  libsoup-3.0-dev libjavascriptcoregtk-4.1-dev libssl-dev patchelf build-essential pkg-config
cd frontend && npm install && cd ..
# in another terminal: scripts/run-backend.sh   and   (cd frontend && npm run dev)
frontend/node_modules/.bin/tauri dev
```

## Build the .deb

```bash
packaging/build-deb.sh        # runs backend tests, builds the UI, runs `tauri build --bundles deb`
sudo apt install ./dist/zeus_0.1.0_amd64.deb
```

Building needs network once (Rust crates, npm packages, and the Python wheels, which `packaging/make-wheelhouse.sh`
downloads into `packaging/wheelhouse/`). The resulting package is self-contained: its `postinst` creates a virtualenv in
`/usr/lib/zeus/venv` and installs the bundled wheels **offline**. The wheels target the build machine's Python
(e.g. cp312, x86_64); on a different Python, `postinst` falls back to PyPI.
Ollama and models are not bundled. Install them separately.

**Run / uninstall**

```bash
zeus                       # or launch "zeus" from the application menu (it starts its own backend on 127.0.0.1:8765)
sudo apt remove zeus       # your data stays in ~/.local/share/zeus (delete it to remove sessions/history)
```

## Permissions

| Mode | Behaviour |
|---|---|
| Read only | Agent can read; every write/command is denied (no prompt) |
| Approval | Reads allowed; everything else asks (default) |
| Trusted | File create/edit and ordinary commands allowed **inside the workspace**; delete, package install, git commit/push still ask |
| Custom | Per-operation allow / ask / deny |

Always enforced, in every mode: paths must resolve inside the project (`..` and symlink escapes rejected);
existing files are never overwritten by `create_file`; `sudo`, `su`, `rm -rf /`, pipe-to-shell and similar
commands are blocked outright. "Approve for Session" is scoped to the exact operation (same action + same
file/command), not to a whole category.

> Limitation: commands run as your user in a normal shell. ZEUS shows you the exact command and gates it, but it
> does not sandbox it — approve commands the way you would paste them into your own terminal.

## Tests

```bash
cd backend && .venv/bin/python -m pytest      # permissions, path safety, tools, runner, agent loop, API
cd frontend && npm test && npm run build      # unit tests + typecheck + production bundle
python3 scripts/e2e_demo.py --parent /tmp/zeus-demo --name zeus-notes   # REAL-model demo (needs running backend + Ollama)
```

## Repository layout

```
backend/      FastAPI backend (agent, tools, permissions, providers, sessions, terminal, metrics) + tests
frontend/     React + TypeScript + Tailwind UI (feature folders under src/features)
src-tauri/    Tauri 2 desktop shell (spawns/stops the backend) and app icons
packaging/    build-deb.sh, make-wheelhouse.sh, Debian postinst/prerm
scripts/      run-backend.sh (dev) and e2e_demo.py (real-model demo)
docs/         ARCHITECTURE.md
```

## Roadmap

Phase 2 (not started): multi-agent orchestration on top of the existing `AgentEngine`/`AgentSpec`. The data model and
event stream already carry an `agent` id. Memory, RAG and dashboards are out of scope for Phase 1.

## License

[MIT](LICENSE) © 2026 Ayrus498
