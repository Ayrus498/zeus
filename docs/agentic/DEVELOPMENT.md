# Development

Owners: **A3 to A6** (engineers by area), **A10** (docs and release). Read [ORCHESTRATION.md](ORCHESTRATION.md) for how work is assigned and
[DESIGN.md](DESIGN.md) for what you are changing.

## 1. Who changes what

| Agent | Area | Paths | Typical work |
|---|---|---|---|
| A3 Backend Engineer | backend | `backend/app/**` except `cli/` | API, agent loop and prompts, tools, permissions, providers, persistence, conversion |
| A4 Frontend Engineer | desktop UI | `frontend/src/**` | features, stores, services, hooks, styling |
| A5 CLI Engineer | terminal app | `backend/app/cli/**`, `packaging/zeus-cli`, `packaging/zeus-code` | layout, commands, cards, shell runner |
| A6 Desktop and Packaging Engineer | shell and package | `src-tauri/**`, `packaging/**` | window, backend lifecycle, launcher dispatch, detaching, `.deb`, desktop entry |
| A10 Docs and Release Engineer | docs and releases | `README.md`, `docs/**`, `RELEASE_NOTES.md`, `DEVELOPMENT_STATUS.md`, screenshots, version files | documentation, screenshots, release |

An engineer asks the orchestrator, not the other engineer, when a change needs a path they do not own.

## 2. Environment

| Need | Version | Notes |
|---|---|---|
| Python | 3.10 or newer | backend and terminal app; `backend/.venv` is created by `scripts/run-backend.sh` |
| Node | 20 or newer | frontend |
| Rust | stable (1.99 was used) | `src-tauri`; Tauri system libraries for the window (see the README) |
| Ollama | running, with at least one tool-capable model | `qwen3-coder` works best; small models need the fallbacks |
| zenity | any | native folder picker |
| Extras for checks | Xvfb, Chromium or Playwright | launcher and browser checks (see [TESTING.md](TESTING.md)) |

Isolate your runs: set `ZEUS_HOME` and `ZEUS_DATA_DIR` to throw-away folders so nothing touches the owner's `~/.zeus` or database.

## 3. Everyday commands

| Task | Command |
|---|---|
| Run the backend | `scripts/run-backend.sh` (127.0.0.1:8765) |
| Run the UI in a browser | `cd frontend && npm install && npm run dev` (http://localhost:1420) |
| Run the terminal app from source | `packaging/zeus-cli` |
| Run the desktop shell in development | `cd src-tauri && cargo tauri dev` (needs the Tauri CLI; debug builds stay attached to the terminal) |
| Backend tests | `cd backend && .venv/bin/python -m pytest -q` |
| Frontend type check, tests, build | `cd frontend && npx tsc --noEmit && npx vitest run && npx vite build` |
| Shell unit test and build | `cd src-tauri && cargo test --lib && cargo build --release` |
| Build the `.deb` | `packaging/build-deb.sh` (runs the backend tests, builds the UI, runs `tauri build`) |

## 4. Conventions

**General**
* Small, reviewable changes; one purpose per change. Do not reformat code you are not changing.
* No placeholders or half-implemented paths. If something cannot be finished, say so in the change summary.
* A new behaviour ships with a test that fails without it. A defect fix ships with a test that reproduces it.
* Keep names and messages in the product voice: the product is **Zeus-Agent**; the command is `zeus`; paths and variables keep the lower-case or `ZEUS_` forms.

**Backend**
* `api/main.py` wires only; logic lives in the owning package. Mutating tools always call `PermissionManager.authorize` first.
* Anything that touches paths goes through the workspace resolver. Anything that comes from a model, a file under `.zeus/` or the network is untrusted input.
* Prompts are behaviour: change them only with a failing real-model case or a clear defect, and record the before and after.

**Frontend**
* TypeScript strict mode; state in zustand stores; server access only through `services/`.
* Streaming content must not fight the reader: use `useStickScroll` for anything that follows new content.
* Confirmations are in-app dialogs (the webview has no native `confirm()`).

**Terminal app**
* Output goes through the rich console into the message window; nothing prints directly to the terminal.
* Tests drive the real screen through a pseudo-terminal and a screen emulator.

**Shell**
* Decisions that can be pure functions are pure functions with unit tests (for example `detach_wanted`).
* Debug builds never detach.

**Commits and history**
* Messages state what changed and why, in the imperative. Commit only when the owner asks. Never rewrite published history.

## 5. Definition of done (every change)

- [ ] The acceptance criteria of the requirement are met and traceable (see [REQUIREMENTS.md](REQUIREMENTS.md)).
- [ ] `tsc --noEmit`, `vite build`, the backend suite and `vitest` pass; `cargo test --lib` and `cargo build --release` pass for shell changes.
- [ ] A new or changed test covers the behaviour.
- [ ] Evidence for user-visible behaviour: a real-model run, a browser run or a pseudo-terminal run, as the change requires.
- [ ] Security review done when the triggers in [DESIGN.md §6](DESIGN.md#6-security-design) apply.
- [ ] README, docs, status table and release notes match; screenshots are current.
- [ ] The change summary lists files changed, commands run with results and anything not verified.

## 6. Working on a work item

1. Read the task card, the requirement and the design note.
2. Reproduce first (defects) or write the failing test (features).
3. Implement inside your paths. Run the fast checks after each step.
4. Run the area checks, then the full suites.
5. Write the change summary and hand it to the reviewer through the orchestrator.

## 7. Adding things

| To add… | Do this |
|---|---|
| a tool | schema and implementation in `tools/engine.py` (validate arguments, call `authorize`), a test in `test_tools.py`, a line in the system prompt only if the model needs guidance, a row in the permission table if it is a new action |
| a REST endpoint | handler in `api/main.py` delegating to a package; a test in `test_api.py`; update [DESIGN.md §5](DESIGN.md#5-interfaces) |
| a WebSocket event | agree it in a design note; emit in `SessionRuntime`; handle it in the UI (`stores/app.ts`) and the terminal app (`cli/render.py`); document it in DESIGN §5.2 |
| a terminal slash command | entry in `COMMANDS` in `cli/app.py`, a `cmd_<name>` method, a test in `test_cli.py`, a row in `docs/COMMANDS.md` and the README |
| a UI feature | a folder under `frontend/src/features/`, state in the store, a unit test for any logic |
| a model family to the guide | catalogue entry with purpose, examples and fit guidance; a test in `test_models_library.py` |

## 8. Release checklist

Owner: **A10**, with the owner's approval for every outward step.

1. Choose the version. Update it in one change: `backend/app/config.py` (`VERSION`), `src-tauri/tauri.conf.json`, `src-tauri/Cargo.toml`, the `zeus` entry in `src-tauri/Cargo.lock`, `frontend/package.json` and the top of `frontend/package-lock.json`.
2. Write the release notes (`RELEASE_NOTES.md`): what is new, improved, fixed, how to upgrade, known limits.
3. Run every gate (G3 to G5) on the exact tree that will be released.
4. Build the package with `packaging/build-deb.sh`. The script copies every `.deb` it finds in the Tauri bundle folder, so delete older versions from `src-tauri/target/release/bundle/deb/` first.
5. Inspect the artifact, not the source: `dpkg-deb -f` (version), `dpkg-deb -c` (`/usr/bin/zeus`, `/usr/bin/zeus-cli`, the desktop entry) and extract it to confirm the new code is inside. Record its SHA-256 in the release notes.
6. **Owner approval**, then commit, push and tag (`v02_00` style tags have been used).
7. The owner uploads the `.deb` to the release page (the command-line release tool is not installed on the development machine) and updates public links. The installing user runs `sudo apt install --reinstall ./zeus_<version>_amd64.deb`.

## 9. Known gaps

* There is no continuous-integration workflow in the repository; the gates run locally.
* Installing the package with `apt` on a clean machine and launching the native window against the installed package are not verified here.
* Browser and pseudo-terminal checks are scripts, not part of the pytest run.
