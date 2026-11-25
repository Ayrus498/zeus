# mcp: the Model Context Protocol (planned, not built)

This folder is reserved for the next phase. Today the agent has the built-in tools in `toolchain/engine.py` (`TOOL_SCHEMAS` plus one `t_<name>` method each). MCP adds tools
that live in other processes.

## Where it plugs in

```
agents/engine.py  ──asks for tool schemas / executes a call──▶  toolchain/engine.py  ──▶  built-in tools
                                                                      │
                                                                      └──▶  mcp/  (a client per configured server)
```

* **Tool source.** `ToolEngine` has a fixed list of schemas today. The change this phase needs is small and lives in the toolchain: let it ask a set of *tool sources* for
  `schemas()` and `call(name, args, ctx)`; the built-in tools become the first source and `mcp/` provides another.
* **Permissions.** Calls pass through `toolchain/permissions.py` with a new action (default: ask), shown in the approval card with the server's name. A project's permission
  mode applies unchanged (read-only denies, trusted may allow).
* **Configuration.** Servers are defined in the user's settings (`~/.zeus/`), never in a project folder: a cloned repository must not be able to start a program on the
  user's machine (the same reason permissions are never read from `<project>/.zeus`).
* **Isolation.** A server is a subprocess started with a clean environment and no secrets unless the user adds them; its output is untrusted text.
* **Naming.** `mcp__<server>__<tool>`: unique, and unable to shadow `create_file`, `run_command` and the rest.

## Where its pieces will go

| Piece | Folder |
|---|---|
| client (stdio first, then HTTP), server definitions, tool/resource listing | `mcp/` |
| tool-source hook, the new permission action | `toolchain/` |
| REST endpoints to list servers and their tools, a settings screen | `api/routes/mcp.py`, `frontend/src/features/mcp/` |
| tests | `backend/tests/mcp/` |

Dependencies: `mcp` may import `core` and `toolchain` only (see `docs/STRUCTURE.md`).
