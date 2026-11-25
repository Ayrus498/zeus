"""MCP (Model Context Protocol): tools, resources and prompts from MCP servers, for the next phase. NOT IMPLEMENTED YET: this package marks where it goes.

What belongs here: a client that starts or connects to the MCP servers the user configured, lists what they offer, and hands their tools to the toolchain; nothing about
models (that is `modelconnectors/`), nothing about running a conversation (that is `agents/`).

The rules it must follow (see README.md in this folder for the design notes):
  * every MCP tool call goes through `toolchain.permissions` like a built-in tool: nothing a server offers runs without the project's permission policy;
  * tool names are namespaced `mcp__<server>__<tool>` so a server can never replace a built-in tool;
  * server definitions are the user's configuration (settings), never read from a project folder;
  * it may import only `core` and `toolchain` (enforced by tests/test_architecture.py).
"""
