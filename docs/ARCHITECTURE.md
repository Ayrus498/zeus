# Zeus-Agent architecture (Phase 1)

## Components

| Package | Responsibility |
|---|---|
| `backend/app/api` | FastAPI: `main.py` builds the shared context and mounts one router per feature (`routes/`); REST plus two WebSockets (`/ws/sessions/{id}`, `/ws/terminal/{pid}`) |
| `backend/app/agents` | Agent loop (`AgentEngine`), missions, Auto-mode routing, per-model request shaping, prompts, loop/repetition guard |
| `backend/app/toolchain` | Tool schemas and implementations (`engine.py`), the permission engine (`permissions.py`: modes, policy, path/symlink validation, command classification), the subprocess runner, the text tool-call parser |
| `backend/app/modelconnectors` | The model engines behind one contract (`base.py`): `ollama/`, `llamacpp/`, the registry that picks one per conversation, downloads, model details |
| `backend/app/modelhub` | Which model is best for which job (Arena scores) and what fits this machine |
| `backend/app/memory` | Project memory notes and the rolling summary of long conversations |
| `backend/app/workspace` | Project registry, conversations, the live `SessionRuntime`, the `.zeus` mirror and restore, `state.json` |
| `backend/app/documents` | Document-to-Markdown conversion in an isolated worker |
| `backend/app/maintenance` | Reset to a fresh installation; uninstall cleanup |
| `backend/app/system`, `core` | Device information, metrics, hardware fit; configuration and the SQLite schema |
| `backend/app/mcp` | Planned: MCP servers and their tools (see its README) |
| `backend/app/cli` | The terminal app: `ui/` toolkit, `commands/` per feature, a client of the same backend |
| `frontend/src/features/*` | chat, commands, engines, benchmarks, memory, models, maintenance, permissions, projects, terminal, file-explorer |
| `src-tauri` | Window + backend process lifecycle only |

Which package may import which, and where new work goes: [STRUCTURE.md](STRUCTURE.md).

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
- Model engines keep one contract (`modelconnectors/base.py`), and MCP has its place and rules (`mcp/README.md`); where new work goes is in [STRUCTURE.md](STRUCTURE.md).
- Tool engine is stateless; all state is in `ToolContext`, so agents can run concurrently with separate contexts.


## Engines (Ollama and llama.cpp)

Conversation and loops (`agents/intent.py`, `AgentEngine.loop`): a message that is entirely chit-chat (`is_chitchat`: the whole text, ignoring case and punctuation, is greetings/thanks/"who are you"/"what can you do", up to three phrases; "hi, create a todo app" is not) runs with tools off, and a turn without tools uses `CHAT_PROMPT` (short, lists no tool) instead of the tool-centred `SYSTEM_PROMPT`, because small models answer "HI" with a tool name when they are shown tools. With tools off a parsed tool call is never executed. Repetition: the second identical batch adds a one-off nudge that quotes what the tool answered; at the guard's threshold the run does one more turn with tools off and a nudge to answer in words (`forced_text`), and only if that turn still produces nothing but a call does it stop with `loop_detected`.

Ollama's models in llama.cpp mode (`modelconnectors/llamacpp/ollama_models.py`): Ollama keeps each model as a manifest plus content-addressed blobs, and the layer of type `application/vnd.ollama.image.model` is a plain GGUF file, so `llama-server -m <blob>` loads it with the chat template stored inside. `store_dirs` finds the store (`$OLLAMA_MODELS` exclusively when set, otherwise `~/.ollama/models`, `/usr/share/ollama/.ollama/models`, `/var/lib/ollama/models`); `scan` reads manifests as untrusted text (64-hex digest, regular non-link blob of the stated size, no model layer = skipped, two tags of one file = one model) and never writes. `LlamaCppProvider.scan` lists them as `ollama/<name>` (`source: "ollama"`, `removable: null`); the `ollama_models` setting hides them. Every model row carries `source` (managed, hf_cache, llama_cache, folder, ollama) and `removable` (the files a delete would remove and the bytes freed). `delete` only removes managed downloads and models in the Hugging Face / llama.cpp caches (for a Hugging Face entry: the snapshot link, and its blob when no other link uses it, then the empty entry), re-checks every path against the cache root at deletion time, never follows a link out of the cache, and refuses Ollama's models and the user's folders with an explanation. Search and download: `hf_search` (`/api/llamacpp/search`, the typed text only), `/api/llamacpp/hf` adds `arena.gguf_fit` per file and `arena.suggest_file`; `ZEUS_HF_BASE` redirects the Hugging Face address (tests, mirrors).

Chat templates (`modelconnectors/llamacpp/provider.py`): with native tool calling (tools sent) the history keeps OpenAI's shape; without tools (the text tool protocol) `flatten_messages` sends plain strictly alternating user/assistant turns (a stored tool call becomes the JSON text the model was taught, a tool result a user turn, neighbours of the same role are merged), and a 400 that complains about the template/roles/system prompt is retried once with the system prompt folded into the first user turn (`_stream` raises `_TemplateRejected` before yielding anything, so nothing is duplicated).

Finding llama-server (`modelconnectors/llamacpp/finder.py`, `LlamaCppProvider.candidates/binary`): the path set in the settings wins when it works; otherwise the standard places (`LLAMA_SERVER`, PATH, `~/.local/bin`, `/usr/local/bin`, `~/llama.cpp/build/bin`) and a breadth-first search of the home folder, `/opt`, `/usr/local` and the data folder (6 levels, 80,000 entries, 4 s, skipping caches, `node_modules`, venvs, hidden folders, Ollama and symlinked folders). A found file is accepted only if it is an executable ELF program with llama.cpp's libraries or `llama-cli` beside it and no `ollama` in its path. Each candidate is probed once (version, build, GPUs) and cached (5 min; a failed search is retried after 20 s; a vanished file triggers a new search). Automatic mode ranks GPU-capable builds first, then the newest build. `/api/llamacpp` returns `found`, `binary_auto`, `binary_problem`; `POST /api/llamacpp/rescan` searches again.

`modelconnectors/registry.py` holds the `Engines` registry and a `ProviderProxy`; the agent loop talks to the proxy, which forwards to the engine of the
conversation that is running (a ContextVar set per run), so two conversations may use different engines. `modelconnectors/llamacpp/provider.py` starts one `llama-server`
subprocess per loaded model (`-np 1`, `--jinja`, OpenAI-compatible SSE) and translates its stream to the Ollama chunk shape; `modelconnectors/llamacpp/gguf.py` reads GGUF
headers without loading tensors. Each session stores its `engine`; the default engine and the last model per engine are settings.
`workspace/state.py` writes `~/.zeus/state.json` (a sanitised JSON pointer, mode 0600) on session create/open/run and engine switch, from either client;
`/api/state` resolves it and `zeus --cli --continue` uses it.

## Removing the package (`packaging/debian/prerm`, `postrm`, `app/cleanup.py`)

`prerm remove` (dpkg also runs it for `apt purge`) first stops the program: processes whose command line starts with `/usr/lib/zeus/venv/bin/python -m app` or `/usr/bin/zeus`, plus the `llama-server` children of those (TERM, then KILL after 5 s), because a running
backend would write its data again after the cleanup. Then for every account with uid 0 or 1000 to 65533 that has Zeus-Agent folders it runs `runuser -u <user> -- env -i HOME=... python -m app.maintenance.cleanup` (the venv's python, else `python3`; standard library only), so deletion
happens with the user's own permissions. `app.maintenance.cleanup` removes `~/.zeus`, `~/.local/share/zeus` (database, logs, history, shim) and the `ai.zeus.desktop` folders of the desktop window under `.local/share`, `.cache`, `.config`, with the same rules as the Reset dialog (`maintenance/reset.py`: known names only, no symlink followed). It deliberately does
not read the project list and never touches a project's `.zeus` folder (project data travels with the project, and paths read from user-writable files are never deleted). It never fails the removal. Models are kept (`--models` removes them); `postrm purge` removes `*.gguf`/`*.part` in each user's
`~/.local/share/zeus/models` the same way. `~/.zeus/keep-data` (or `ZEUS_KEEP_USER_DATA=1`) opts a user out. `prerm upgrade` and the other actions (a reinstall is an upgrade to dpkg) delete nothing. Tests: `tests/test_cleanup.py` and `tests/test_maintainer_scripts.py` (the real scripts under `sh` with stand-ins for getent/runuser and real throw-away processes).

## Reset to a fresh installation

`maintenance/reset.py` plans and performs a reset in five independently chosen items: `conversations` (every table emptied, ids restart, file compacted, so a running app keeps working), `project_data`
(the known files of each project's `.zeus`: sessions, memory.md, project.json, README.md, a `.gitignore` that is exactly `*`; empty folders are then removed, unknown files stay), `home` (`~/.zeus/settings.json`,
`state.json`), `logs` (log files, terminal history, the python shim link, never its target) and `models` (`*.gguf`/`*.part` in Zeus-Agent's own download folder; not chosen by default). Nothing follows a symlink; a
`.zeus` that is a link or not directly inside its project is ignored. `GET /api/reset` previews (sizes, counts, paths) and changes nothing; `POST /api/reset` needs the word `RESET` and known item ids, stops running
conversations, downloads and the llama-server, drops queued session writes, runs the items and clears in-memory caches. Both endpoints accept only local callers: a browser `Origin` must be the app's own (tauri, localhost, 127.0.0.1) and
`Host` must be local (a web page on the internet, or DNS rebinding, gets 403). The dialog is `ResetDialog.tsx`; it reloads the interface afterwards. `/reset` exists in the app and the terminal.

## Memory: project notes and rolling summary

`memory/project_memory.py` keeps `<project>/.zeus/memory.md` (16 KB on disk, 6,000 characters in the prompt, control characters stripped, mode 0600, `.zeus/.gitignore` is `*`).
`AgentEngine._history` appends it to the system prompt as background notes, then, when the session has a summary, the summary block, and sends only the messages after
`sessions.summary_msgs`. The `remember` tool appends one line via `memory.append` after a normal permission request (action `edit_file`, target `.zeus/memory.md`); the file tools
still cannot write inside `.zeus/`. `memory/summary.py` holds the pure parts: the boundary (always a user message, so a tool call is never split from its result, keeping the last
two user turns, one for `/compact`), the 75% trigger (`est_tokens` plus a 2,048 reserve against the session's window), transcript rendering with tool output clipped, chunking and
the prompt. `AgentEngine.compact` summarises in pieces with the conversation's own model (each piece folds into the running summary), stores `summary`, `summary_msgs`,
`summary_updated` and mirrors them to the session file; failures never break a run (one attempt per run, the old fallbacks still apply). The transcript in the database is never
modified. API: `/api/projects/{id}/memory` (GET, PUT, POST `/note`), `/api/sessions/{id}/context`, `/api/sessions/{id}/compact`, DELETE `/api/sessions/{id}/summary`.
The desktop prompt box's command menu is `frontend/src/features/chat/commands.ts`; results are `local` timeline items.

## Model rankings and hardware fit

`modelhub/arena.py` holds Arena's human-preference leaderboard for open models (bundled snapshot `modelhub/data/arena_snapshot.json`, rebuilt by
`scripts/data/update_arena_snapshot.py`, refreshed on request from freellmarena.com/data.json and cached in the settings table) and ports the site's published
memory and speed model: weights at 0.606 bytes per parameter (Q4_K_M), KV cache from each model's architecture, 0.6 GB runtime, speed = memory bandwidth x 0.78 / bytes read
per token (mixture-of-experts read only their active parameters). `hardware_profile` maps the detected GPU to the card catalogue (laptop parts discounted) and uses GiB.
`overview` returns, per activity, models sorted by score with a verdict, the matching Ollama tag (family and size must both match; entries without sizes are cloud-only) and
`best_here`: the highest score at 8 tokens/s or more, plus `best_quality` when a stronger model runs but slowly. Served by `/api/arena`, `/api/arena/family/{family}` and
`/api/arena/detail`; shown in the Models window and by `/best`. Attribution (CC BY 4.0, Arena Intelligence, via freellmarena.com) is shown wherever scores appear.

## Persistence: SQLite + `.zeus/` mirror

`backend/app/workspace/zeusdir.py` (`ZeusFiles`) keeps a durable, human-readable copy of state: `<project>/.zeus/sessions/<uid>.json`
(conversation context, written debounced after each message/usage update and flushed at shutdown) and `~/.zeus/settings.json`
(Ollama endpoint, default model/context, project list). `restore_home()` runs at startup and whenever the database is recreated;
`restore_project()` runs when a project is added or opened. Sessions have a stable `uid` so restore is idempotent across databases.
Trust boundary: files under `.zeus/` are untrusted input - permission modes are never read from them, session JSON is validated
(roles, types, size), and `ToolEngine` refuses agent writes to `.zeus/` and hides it from list/search.


## Auto model routing

`backend/app/agents/router.py`. When a session's model is `auto`, `AgentEngine.run` asks `AutoRouter.route(prompt, last_model)` once per prompt:
`classify()` (rule-based; explainable reasons) -> `decide()` scores installed chat models per role (`code` needs tool calling; `think` prefers reasoning/thinking
models) from catalog knowledge + `/api/show` capabilities + GPU/RAM placement + loaded state, with hysteresis to avoid reloads. The chosen model is used for every step
of that run; a reasoning-only model runs with tools disabled and a system note. Pins (`auto_code_model`, `auto_think_model`) override scoring. Nothing in routing loads or
changes models by itself: it only chooses the name passed to Ollama's chat call.


## Getting files onto disk when the model does not call tools

Small local models sometimes print a whole file in chat instead of calling `create_file`, or emit a tool call as text with slightly invalid JSON.
Three layers handle this (in `backend/app/agents/` and `toolchain/`):
1. `toolparse.lenient_file_call` recovers a `create_file` call whose JSON is almost valid (e.g. one unescaped quote in a 16 KB string).
2. `engine.py` retries up to twice when a reply *looks like* a tool call but cannot be parsed, telling the model what was wrong (the garbage is not kept in history).
3. `rescue.find_printed_file` - if the user clearly asked for a file and the reply is exactly one large code block with no file written, Zeus-Agent issues a normal
   `create_file` call for it. It goes through the permission engine like any write (approval card with diff, never overwrites).
In the UI every finished code block also has a "Save as file" button (`POST /api/projects/{id}/files`): the click is the user's explicit action; the same path
rules apply (inside the project, not `.zeus`, blocked in read-only mode, no silent overwrite).


## Missions and model-aware prompting

`backend/app/agents/mission.py` (`MissionRunner`) turns an attached Markdown document into a verified objective. The WebSocket message
`{"type":"mission","name","content","note","max_rounds","verifier"}` starts it (`start_mission` in `api/routes/live.py`; `.md` only, <= 200 KB).
Per round: `AgentEngine.loop()` (the same tool loop used for normal prompts, now reusable and returning a status) -> `build_evidence()` -> a reviewer call
(`provider.chat_once`, model chosen like the worker unless the user asks for the reasoning model) returning per-criterion JSON -> `read_verdict()` (Zeus-Agent computes
`accepted` itself) -> `hard_checks()` (exit codes and files on disk overrule the reviewer) -> feedback appended as a user message and the next round.
Stop conditions: accepted, user stop, stalled (same unmet criteria and no new file/command activity), round limit. State lives in the `missions` table
(mirrored into the session's `.zeus` file); events: `mission_start/round/verifying/check/end`.
Loop guards for weak models (all in `engine.loop`): plan-only replies are sent back to act (missions), printed `# file.py` blocks are saved through `create_file`
(approval flow), fabricated `<tool_response>` text is rejected, malformed tool JSON is retried.

`backend/app/agents/modelprefs.py` builds a `Prefs` per model and role (`agent | plan | verify | think`) from `/api/show` (capabilities, `parameters`) and family rules,
and `prepare_messages()` shapes the conversation (text tool protocol for models without native tools, system folded into the user turn where recommended, stale
`<think>` blocks removed); `plan_context()` raises `num_ctx` when a large document needs it.


## Document conversion (MarkItDown)

`backend/app/documents/service.py` validates (extension allow-list, size, file signature) and runs `app.documents.worker` as a subprocess; `worker.py` builds a MarkItDown
instance with `enable_builtins=False` and registers only the allowed converters (plain text only for txt/json/xml/zip/md), sets rlimits, imports the heavy libraries,
THEN blocks sockets (replacing `socket.socket` before importing would break `ssl`), and prints one JSON object. The API returns Markdown to the caller and stores nothing.
The frontend treats the result as an ordinary attachment (`name = <file>.md`), so missions, previews and the `.md`-only WebSocket check need no special cases.


## Model library (every model on ollama.com)

`backend/app/modelconnectors/ollama/library.py`. The curated catalogue (`catalog.py`) describes about 30 families in depth; everything else in the public Ollama library is listed
from ollama.com itself so new models appear without a Zeus-Agent update. `fetch_library()` parses https://ollama.com/library (name, description, capability badges, sizes,
pulls, tags, updated), caches it for 6 hours and keeps the last good copy in the settings table for offline use; a page that yields fewer than 20 entries is treated as
unreadable. `search()` parses https://ollama.com/search?q= (official models and community `user/model` entries) with a 10 minute memory cache and sends only the typed text
(no cookies, project or path data; the request has a test). `modelinfo.fetch()` serves details for a family (`/library/<name>`) or a community model (`/<user>/<name>`) and
parses its version list. The UI builds one item list from the catalogue, the library and the search results (`frontend/src/lib/modelGroups.ts`), attaches installed models
to their family and orders the library by popularity (by relevance while searching). Download uses the existing pull endpoint with the exact tag, so the same progress and
fit logic applies. Community models are marked and carry a warning because neither Ollama nor Zeus-Agent reviews them.

