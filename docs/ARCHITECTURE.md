# Zeus-Agent architecture (Phase 1)

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


## Persistence: SQLite + `.zeus/` mirror

`backend/app/persistence/zeusdir.py` (`ZeusFiles`) keeps a durable, human-readable copy of state: `<project>/.zeus/sessions/<uid>.json`
(conversation context, written debounced after each message/usage update and flushed at shutdown) and `~/.zeus/settings.json`
(Ollama endpoint, default model/context, project list). `restore_home()` runs at startup and whenever the database is recreated;
`restore_project()` runs when a project is added or opened. Sessions have a stable `uid` so restore is idempotent across databases.
Trust boundary: files under `.zeus/` are untrusted input - permission modes are never read from them, session JSON is validated
(roles, types, size), and `ToolEngine` refuses agent writes to `.zeus/` and hides it from list/search.


## Auto model routing

`backend/app/agent/router.py`. When a session's model is `auto`, `AgentEngine.run` asks `AutoRouter.route(prompt, last_model)` once per prompt:
`classify()` (rule-based; explainable reasons) -> `decide()` scores installed chat models per role (`code` needs tool calling; `think` prefers reasoning/thinking
models) from catalog knowledge + `/api/show` capabilities + GPU/RAM placement + loaded state, with hysteresis to avoid reloads. The chosen model is used for every step
of that run; a reasoning-only model runs with tools disabled and a system note. Pins (`auto_code_model`, `auto_think_model`) override scoring. Nothing in routing loads or
changes models by itself: it only chooses the name passed to Ollama's chat call.


## Getting files onto disk when the model does not call tools

Small local models sometimes print a whole file in chat instead of calling `create_file`, or emit a tool call as text with slightly invalid JSON.
Three layers handle this (all in `backend/app/agent/`):
1. `toolparse.lenient_file_call` recovers a `create_file` call whose JSON is almost valid (e.g. one unescaped quote in a 16 KB string).
2. `engine.py` retries up to twice when a reply *looks like* a tool call but cannot be parsed, telling the model what was wrong (the garbage is not kept in history).
3. `rescue.find_printed_file` - if the user clearly asked for a file and the reply is exactly one large code block with no file written, Zeus-Agent issues a normal
   `create_file` call for it. It goes through the permission engine like any write (approval card with diff, never overwrites).
In the UI every finished code block also has a "Save as file" button (`POST /api/projects/{id}/files`): the click is the user's explicit action; the same path
rules apply (inside the project, not `.zeus`, blocked in read-only mode, no silent overwrite).


## Missions and model-aware prompting

`backend/app/agent/mission.py` (`MissionRunner`) turns an attached Markdown document into a verified objective. The WebSocket message
`{"type":"mission","name","content","note","max_rounds","verifier"}` starts it (`start_mission` in `api/main.py`; `.md` only, <= 200 KB).
Per round: `AgentEngine.loop()` (the same tool loop used for normal prompts, now reusable and returning a status) -> `build_evidence()` -> a reviewer call
(`provider.chat_once`, model chosen like the worker unless the user asks for the reasoning model) returning per-criterion JSON -> `read_verdict()` (Zeus-Agent computes
`accepted` itself) -> `hard_checks()` (exit codes and files on disk overrule the reviewer) -> feedback appended as a user message and the next round.
Stop conditions: accepted, user stop, stalled (same unmet criteria and no new file/command activity), round limit. State lives in the `missions` table
(mirrored into the session's `.zeus` file); events: `mission_start/round/verifying/check/end`.
Loop guards for weak models (all in `engine.loop`): plan-only replies are sent back to act (missions), printed `# file.py` blocks are saved through `create_file`
(approval flow), fabricated `<tool_response>` text is rejected, malformed tool JSON is retried.

`backend/app/agent/modelprefs.py` builds a `Prefs` per model and role (`agent | plan | verify | think`) from `/api/show` (capabilities, `parameters`) and family rules,
and `prepare_messages()` shapes the conversation (text tool protocol for models without native tools, system folded into the user turn where recommended, stale
`<think>` blocks removed); `plan_context()` raises `num_ctx` when a large document needs it.


## Document conversion (MarkItDown)

`backend/app/convert/service.py` validates (extension allow-list, size, file signature) and runs `app.convert.worker` as a subprocess; `worker.py` builds a MarkItDown
instance with `enable_builtins=False` and registers only the allowed converters (plain text only for txt/json/xml/zip/md), sets rlimits, imports the heavy libraries,
THEN blocks sockets (replacing `socket.socket` before importing would break `ssl`), and prints one JSON object. The API returns Markdown to the caller and stores nothing.
The frontend treats the result as an ordinary attachment (`name = <file>.md`), so missions, previews and the `.md`-only WebSocket check need no special cases.
