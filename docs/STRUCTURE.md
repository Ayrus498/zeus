# Project structure

How the repository is organised, which package may use which, and where new work goes. The rules in the second section are checked by `backend/tests/test_architecture.py`:
a change that breaks them fails the test suite.

## The map

```
backend/app/                 the backend (python -m app) and the terminal app (python -m app.cli)
  core/                      configuration (config.py) and the SQLite database (database.py)           base layer, imports nothing of ours
  system/                    this computer: device information (about), live metrics, free GPU/RAM and "does this context fit" (hardware)
  modelconnectors/           the model engines ("connectors") and what they share
    base.py                    the contract every connector keeps (Connector protocol, ConnectorError)
    registry.py                Engines (the connectors), the per-conversation choice, ProviderProxy, the help text
    downloads.py               download manager (Ollama pulls, Hugging Face files), progress and cancel
    modelinfo.py               model details: context length, memory, what fits
    ollama/                    provider.py (Ollama's HTTP API), catalog.py (curated guide), library.py (the public library)
    llamacpp/                  provider.py (llama-server), finder.py (finds llama-server), gguf.py (GGUF headers), ollama_models.py (reads Ollama's store, read only)
  modelhub/                  which model is best for which job: Arena scores and what fits this machine (arena.py, data/arena_snapshot.json)
  toolchain/                 what the agent can do to a project, and who allows it
    engine.py                  tool schemas, argument validation, the tools (read, create, edit, delete, run, git, remember)
    permissions.py             modes, policies, session grants, path rules, command classification
    runner.py                  controlled subprocess execution (streaming, timeout, cancel)
    toolparse.py               tool calls printed as text by models without native tool calling
  agents/                    running a conversation
    engine.py                  AgentEngine: the loop, history, summaries, repetition handling
    mission.py                 documents run as goal-driven missions
    router.py                  Auto mode: which model for this prompt
    modelprefs.py              per-model request shaping (system prompt folding, text tool protocol, context sizing)
    prompts.py, intent.py, rescue.py
  memory/                    what the agent remembers: project_memory.py (notes), summary.py (rolling summary of long conversations)
  workspace/                 projects, conversations and their persistence
    projects.py, sessions.py, runtime.py (live event bus), zeusdir.py (the .zeus mirror), state.py (state.json: where you were)
  documents/                 attaching documents: conversion to Markdown (service.py, worker.py)
  maintenance/               reset to a fresh installation (reset.py) and uninstall cleanup (cleanup.py)
  mcp/                       MCP, planned (README.md says how it plugs in)
  api/                       HTTP and WebSocket
    main.py                    composition root: create_app() mounts the routers
    context.py                 the shared objects (database, managers, agent, engines) built once
    schemas.py                 request bodies
    routes/                    one router per feature: system, ollama, auto, benchmarks, engines, workspace, sessions, memory, documents, maintenance, live
  cli/                       the terminal app: app.py (the Cli and its commands), client.py (HTTP client)
    ui/                        the terminal toolkit (screen, chooser, render, cards, theme, banner, ...)
    commands/                  one module per slash-command feature (engines, best, llamamodels, memory, reset, models)

backend/tests/               mirrors backend/app: tests/<package>/test_*.py (+ packaging/ for the install scripts and version tooling)
                             shared helpers stay at the top: conftest.py, paths.py, gguf_util.py, fixtures/
frontend/src/
  features/                  one folder per feature: chat, commands, engines, benchmarks, memory, models, maintenance, permissions, projects, file-explorer, terminal
  stores/, services/, lib/, layouts/, components/, __tests__/
src-tauri/                   the desktop shell: window, backend lifecycle
packaging/                   .deb build (build-deb.sh), version.py, publish.py, debian/ maintainer scripts, launchers
scripts/                     e2e/ (the end-to-end suite), manual/ (one-off real-model and browser checks), data/ (data updaters), run-backend.sh
docs/                        ARCHITECTURE.md (how it works), STRUCTURE.md (this file), COMMANDS.md, agentic/
```

## Who may import whom

```
                 api ──────────────────────────────────────────┐
                  │                                             │
               agents ───────────┬───────────┬────────┐         │
                  │              │           │        │         │
              workspace      modelconnectors  system  memory     │      cli  (talks to the backend over HTTP: imports core only)
                  │              │           │        │         │
              toolchain ─────────┤           │        │         │
                  │              │           │        │         │
                  └──────────────┴─────── core ◄───────┴─────────┘
         leaf packages (core only): documents, maintenance, modelhub, system, memory, modelconnectors
```

| Package | may import |
|---|---|
| `core` | nothing of ours |
| `system`, `documents`, `memory`, `maintenance`, `modelhub`, `modelconnectors` | `core` |
| `toolchain` | `core`, `memory` |
| `workspace` | `core`, `toolchain` |
| `agents` | `core`, `memory`, `modelconnectors`, `system`, `toolchain` |
| `mcp` (planned) | `core`, `toolchain` |
| `api` | everything except `cli` |
| `cli` | `core` |

Further rules: no cycles between packages; the connectors `ollama/` and `llamacpp/` never import each other (shared code goes in `modelconnectors/base.py` or `core/`);
every router in `api/routes/` is mounted in `api/main.py`; tests live in the folder of the package they test. Changing a rule is allowed, but it is a decision:
edit `ALLOWED` in `test_architecture.py` and say why in the commit.

## Where does new work go?

| You want to add | Put it in | Also |
|---|---|---|
| a model engine (vLLM, LM Studio, an OpenAI-compatible server) | `modelconnectors/<name>/provider.py` keeping `base.Connector` | one line in `registry.py`, `tests/modelconnectors/`, run `test_connector_contract.py` against it |
| MCP servers and their tools | `mcp/` (client), a tool-source hook in `toolchain/` | see `mcp/README.md`; permissions apply to every call |
| a built-in tool | `toolchain/engine.py` (schema + `t_<name>`), authorise through `permissions.py` | `tests/toolchain/`, a line in the agent prompt |
| multi-agent orchestration | `agents/` (several `AgentSpec`s share one engine, one event bus, one permission manager) | |
| retrieval over a project or documents (RAG) | `memory/` (index, retrieval) fed by `documents/` (conversion, chunking) | a `remember`-style tool in `toolchain/` |
| an HTTP feature | `api/routes/<feature>.py` with `router(ctx)`, mounted in `api/main.py` | request bodies in `schemas.py`, tests in `tests/api/` |
| a terminal command | an entry in `COMMANDS` in `cli/app.py` and a `cmd_<name>`; its views in `cli/commands/<feature>.py` | `docs/COMMANDS.md` |
| a screen in the desktop app | `frontend/src/features/<feature>/` | API calls in `services/api.ts`, state in `stores/app.ts`, an end-to-end case in `scripts/e2e/cases.py` |
| dashboards | `system/` (data) + `api/routes/` + a frontend feature | |
