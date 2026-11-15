# ZEUS architecture (Phase 1)

## Components

| Package | Responsibility |
|---|---|
| `backend/app/api` | FastAPI wiring only: REST + two WebSocket endpoints (`/ws/sessions/{id}`, `/ws/terminal/{pid}`) |
| `backend/app/providers` | Ollama client: tags, show (context length, capabilities, KV geometry), `/api/chat` streaming, memory estimate |
| `backend/app/agent` | Agent loop (`AgentEngine`), prompts, text-JSON tool-call fallback parser, loop/oscillation guard |
| `backend/app/tools` | Tool schemas, argument validation, implementations. Mutating tools call the permission engine first |
| `backend/app/permissions` | `PermissionManager` (modes, per-operation policy, session grants), path/symlink validation, command classification |
| `backend/app/terminal` | Controlled subprocess runner: streaming, timeout, cancellation (process-group kill) |
| `backend/app/projects`, `sessions` | Project registry (references to real folders), conversation persistence, live `SessionRuntime` |
| `backend/app/database` | SQLite schema: projects, sessions, messages, executions, file_changes, settings |
| `frontend/src/features/*` | chat, projects, models, permissions, terminal, file-explorer |
| `src-tauri` | Window + backend process lifecycle only |

## Agent turn

1. UI sends `{"type":"prompt"}` over the session WebSocket.
2. `AgentEngine.run` rebuilds history from SQLite and streams `/api/chat` with the tool schemas and `options.num_ctx`.
3. Tool calls come from native `tool_calls`, or — for models that print JSON instead — from the fallback parser
   (only calls naming a known tool with object arguments are accepted).
4. `ToolEngine.execute` validates arguments against the schema, resolves paths inside the workspace, builds an
   `Operation` (with unified diff for edits) and calls `PermissionManager.authorize`.
5. If policy says *ask*, the runtime emits `permission_request` and the tool coroutine **awaits** the user's answer.
   Reject / stop / disconnect-without-answer ⇒ the operation never runs; the model is told it was denied.
6. After approval the path is re-resolved and the file re-checked (TOCTOU guard), then the write happens and the
   before/after text is stored in `file_changes` (drives the change viewer and the A/M/D badges).
7. Results are saved as `tool` messages and fed back to the model until it answers without a tool call.

## Security properties (each covered by tests in `backend/tests`)

- Backend-enforced approval; no code path to `write_text`/`unlink`/subprocess bypasses `authorize` except the
  user's own typed terminal command (explicit user action; still denied in Read-only mode and for blocked commands).
- Path traversal, absolute paths outside the root, and symlink escapes (file or directory) are rejected.
- `create_file` refuses to overwrite; `edit_file` requires a unique exact match; delete always asks (even Trusted).
- Session grants are keyed by `(action, target)`.

## Designed for Phase 2 (multi-agent)

- Every message, event and permission request carries an `agent` id; `messages.agent` is persisted.
- `AgentSpec(name, system_prompt, tools)` is the unit of an agent; `AgentEngine.run` takes a spec, so several
  specs (different roles/models) can share one engine, one event bus (`SessionRuntime.emit`) and one permission manager.
- Providers are behind a small interface (`chat_stream`, `show`, `list_models`) — other backends can be added.
- Tool engine is stateless; all state is in `ToolContext`, so agents can run concurrently with separate contexts.
