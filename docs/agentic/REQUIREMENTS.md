# Requirements

Owner: **A1 Requirements Analyst**. Consumers: A2 (design), A3 to A6 (implementation), A7 and A9 (verification), A10 (docs).
Each requirement has an id, a testable acceptance criterion, an owner agent and the evidence that verifies it. A requirement
that cannot be tested is rewritten until it can.

## 1. Product vision

Zeus-Agent is a local coding agent for developers who want an assistant that reads, edits and runs code in their own project
with their own models, on their own machine. A model served by [Ollama](https://ollama.com) does the thinking; the Zeus-Agent
backend does every file and command operation behind a permission engine the user controls. It is used from a desktop app
and from a terminal.

**Principles:** local first (no cloud model, no telemetry); the user decides (every write and command is gated); honest
(failures, limits and unverified claims are shown, never hidden); small models must be usable (the agent works around the
habits of 7B to 30B local models).

## 2. Users

| Persona | Need |
|---|---|
| Solo developer on Linux | run a capable assistant offline on a laptop GPU and review every change |
| Privacy-conscious team member | keep code and prompts on the machine |
| Terminal-first developer | the same agent from a shell, in the project folder, without a window |
| Model tinkerer | try, switch and manage many local models and see how they behave |

## 3. Scope

**In scope:** Ubuntu or Debian on amd64, Ollama as the only model backend, a desktop app (Tauri) and a terminal app,
an offline-installable `.deb`.
**Out of scope for now:** cloud model providers, Windows and macOS packages, multi-user servers, a plugin marketplace,
running several agents concurrently in one session (designed for, not built; see [DESIGN.md](DESIGN.md)).

## 4. Functional requirements

Priority: **M** must, **S** should. Verification evidence names the suite or check in [TESTING.md](TESTING.md).

### Workspace and models
| ID | Requirement and acceptance criterion | Pri | Owner | Evidence |
|---|---|---|---|---|
| FR-01 | **Projects.** Add an existing folder or create an empty one, switch between projects, remove a project from the list without touching files. Acceptance: files stay on disk after removal; recent projects survive a restart. | M | A3, A4 | `test_api.py`, `test_zeusdir.py` |
| FR-02 | **Ollama integration.** Discover installed models dynamically (none hard-coded), show connection status and version, let the user set the endpoint, choose a model and a context window. Acceptance: with Ollama stopped the UI explains how to start it; models appear as soon as they are installed. | M | A3, A4 | `test_api.py`, `test_models_library.py` |
| FR-03 | **Model management.** Browse a curated, searchable model guide grouped by family, with purpose, examples and live details from ollama.com; download with progress, unload, and uninstall installed models with a confirmation. Acceptance: uninstalling removes only the chosen model; an in-progress download cannot be uninstalled. | S | A3, A4 | `test_model_details.py`, `test_models_library.py` |
| FR-03a | **The whole Ollama library.** Every family on ollama.com/library is listed in the Models window (not only the curated guide), searchable together with community models, each with description, capabilities, sizes, an `ollama pull` command and a Download button per version. Acceptance: a model that exists only in the public library (for example `ornith`) can be found, opened and pulled; community models are marked with a warning; only the typed search text is sent to ollama.com; with no network the last copy is shown. | S | A3, A4 | `test_library.py`, `modelGroups.test.ts`, `scripts/manual/models_library_check.py` |
| FR-04 | **Auto model mode.** Optionally pick a coding or a reasoning model per prompt from the installed models, with a stated reason, avoiding needless reloads. Acceptance: reasoning-only models run without tools; pins override scoring. | S | A3 | `test_auto_router.py` |

### The agent
| ID | Requirement and acceptance criterion | Pri | Owner | Evidence |
|---|---|---|---|---|
| FR-05 | **Agent loop with nine tools** (list, read, search, create, edit and delete files, run a command, git status and diff). Acceptance: arguments are validated against a schema; the loop is bounded by a step limit; the model is told when an action is denied and must adapt. | M | A3 | `test_agent.py`, `test_tools.py` |
| FR-06 | **Tool-call fallbacks for small models.** Accept tool calls printed as text, repair almost-valid JSON, save a file the model printed instead of creating it, reject fabricated tool results. Acceptance: a reply that prints one large file is saved through the normal approval path. | M | A3, A9 | `test_rescue.py`, `test_agent.py`, real-model runs |
| FR-07 | **Run means run.** When the user says run, execute or launch a file, the agent executes it (`./name`, `python3 file.py`) and does not just read it. Acceptance: "run the swap.out and print the output" runs `./swap.out`; "show me what is inside" still reads it. | M | A3, A9 | real-model runs (3 of 3), prompt tests |
| FR-08 | **Missions.** Attach a Markdown document that becomes a verified objective: the agent works in rounds until each acceptance criterion is met with evidence, stalls, is stopped or reaches the round limit. Acceptance: exit codes and files on disk overrule the reviewer's opinion. | S | A3, A4 | `test_mission.py` |
| FR-09 | **Document attachments.** Convert PDF, Word, PowerPoint, Excel and similar files to Markdown locally and attach the result. Acceptance: conversion has no network access; unsupported or oversized files are rejected with a clear message. | S | A3, A4 | `test_convert.py` |

### Control and safety
| ID | Requirement and acceptance criterion | Pri | Owner | Evidence |
|---|---|---|---|---|
| FR-10 | **Permissions.** Modes read only, approval (default), trusted and custom, enforced by the backend. Acceptance: no write or command runs without an allowed decision; deletes always ask; path traversal and symlink escapes are rejected; a rejected or unanswered request never executes. | M | A3, A8 | `test_permissions.py`, `test_tools.py` |
| FR-11 | **Inline approval.** Each write or command appears in the conversation with its diff or exact command and the choices reject, approve for session, approve once. Acceptance: the same flow works in the terminal app. | M | A4, A5 | UI and pty checks |
| FR-12 | **Terminal and history.** Streaming command output with exit codes, a timeout, cancellation that kills the process group, and a history of executions. | M | A3, A4 | `test_runner.py` |
| FR-13 | **Session memory.** Conversations persist in SQLite and are mirrored to `<project>/.zeus/sessions/` and `~/.zeus/settings.json` so they reload even if the database is lost. Acceptance: files under `.zeus/` are treated as untrusted input and the agent cannot modify them. | M | A3 | `test_zeusdir.py` |

### Desktop and terminal experience
| ID | Requirement and acceptance criterion | Pri | Owner | Evidence |
|---|---|---|---|---|
| FR-14 | **Terminal app (`zeus --cli`).** A full-screen app in the current folder with a pinned prompt box, slash commands (`/settings`, `/ollama-model`, `/ollama-ctx`, `/permissions`, `/project`, `/shell`, `/mission`, `/fold` and more), organised answer and command-output cards, inline approvals and foldable prompts. Acceptance: shell output is always shown; the prompt stays at the bottom while output scrolls above. | M | A5 | `test_cli.py`, pty runs with a real model |
| FR-15 | **Launch behaviour.** `zeus` opens the desktop app and gives the terminal back: the app runs in its own session, survives closing that terminal, and logs to `~/.local/share/zeus/zeus.log`. `zeus --cli` opens the terminal app (`-cli`, `cli` and the old `-code` also work). `zeus --foreground` keeps the app attached. Launching from the desktop menu (no terminal) runs in-process. Debug builds stay attached. Acceptance: see the checks in [TESTING.md](TESTING.md#7-shell-and-launcher). | M | A6 | Rust unit test; release binary under a pseudo-terminal and a virtual display |
| FR-16 | **Scrolling while generating.** The reader can scroll up in the conversation, in a command's output and in a streaming code block while text is still arriving, and the view stays where they put it. Following the newest content resumes when they scroll back to the end or press "Jump to latest"; sending a message always goes to the end. Acceptance: in a real browser, with a streaming answer, a wheel scroll up holds for at least 5 s while the content grows. | M | A4 | `stickScroll.test.ts` (8 tests); `scripts/manual/scroll_check.py` |
| FR-17 | **Settings, About and usage.** A settings menu (models, endpoint, current model, installed models, about with version, founder, platform and device information) and live GPU, VRAM, CPU and RAM usage. | S | A3, A4 | `test_about.py`, UI checks |
| FR-18 | **Packaging and release.** One `.deb` installs offline (bundled wheels), creates the Python environment, installs `zeus` and `zeus-cli`, and a desktop entry named Zeus-Agent. Acceptance: version numbers agree everywhere; the package's checksum is recorded in the release notes. | M | A6, A10 | `packaging/build-deb.sh`, package inspection |

## 5. Non-functional requirements

| ID | Requirement | Measure |
|---|---|---|
| NFR-01 | **Privacy.** No telemetry; the only network traffic is to the configured Ollama endpoint and, on request, ollama.com for the model library, model details and the search text the user types in the Models window. | code review; no other outbound calls in `backend/app` |
| NFR-02 | **Safety by construction.** Mutating tools cannot bypass the permission engine; backend enforcement does not depend on the UI. | `test_permissions.py`, security review (A8) |
| NFR-03 | **Responsiveness.** The UI stays usable while tokens stream; scrolling, typing and approving are never blocked by generation. | browser check (FR-16) |
| NFR-04 | **Resilience.** The backend recreates a deleted database and restores state from `.zeus/`; a failed detached launch falls back to running in the terminal. | `test_zeusdir.py`, launcher checks |
| NFR-05 | **Portability.** Ubuntu or Debian amd64, Python 3.10 or newer, no network needed to install. | package inspection |
| NFR-06 | **Testability.** Every requirement above has a named check; new behaviour ships with a test that fails without it. | gate G5 |
| NFR-07 | **Maintainability.** Backend packages have single responsibilities (see [DESIGN.md](DESIGN.md)); shared contracts change through a design note. | review (G4) |
| NFR-08 | **Honest reporting.** Status documents state what was verified and what was not. | `DEVELOPMENT_STATUS.md` |
| NFR-09 | **Licence.** MIT. | `LICENSE` |

## 6. Constraints and assumptions

* Local models vary widely in quality: 7B models may not converge on large tasks (recorded in `DEVELOPMENT_STATUS.md`), so the
  product reports honestly instead of promising results.
* The Tauri webview does not support native `confirm()`, so confirmations are in-app dialogs.
* The backend listens on `127.0.0.1:8765` by default (`ZEUS_PORT`).

## 7. Traceability

| Requirement | Design section | Code | Tests |
|---|---|---|---|
| FR-05 to FR-07 | DESIGN §4.1 | `backend/app/agents/`, `toolchain/` | `test_agent.py`, `test_tools.py`, `test_rescue.py` |
| FR-10, FR-11 | DESIGN §4.2, §6 | `backend/app/toolchain/permissions.py`, `frontend/src/features/permissions/` | `test_permissions.py` |
| FR-13 | DESIGN §4.5 | `backend/app/workspace/zeusdir.py` | `test_zeusdir.py` |
| FR-14 | DESIGN §4.6 | `backend/app/cli/` | `test_cli.py` |
| FR-15 | DESIGN §4.7 | `src-tauri/src/main.rs`, `lib.rs` | Rust unit test, launcher checks |
| FR-16 | DESIGN §4.4 | `frontend/src/lib/stickScroll.ts`, `hooks/useStickScroll.ts` | `stickScroll.test.ts`, `scripts/manual/scroll_check.py` |
| FR-18 | DESIGN §7 | `packaging/`, `src-tauri/tauri.conf.json` | build script |

## 8. Definition of ready for a requirement

* It has an id, one sentence of intent and an acceptance criterion a stranger could check.
* The owner agent and the verifying check are named.
* Open questions are answered by the repository owner, not assumed.

## 9. Open questions

* Which cloud-free providers beyond Ollama (for example llama.cpp servers) should the provider interface target first?
* Should the terminal app and the desktop app share one conversation list at the same time, or only after a restart?
