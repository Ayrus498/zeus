"""The contract between Zeus-Agent and a model engine ("connector"): what the agent loop, the download manager and the API rely on.

Today there are two connectors, `ollama/` and `llamacpp/`; the registry (`registry.py`) holds them and picks one per conversation. A new engine (vLLM, LM Studio, an
OpenAI-compatible server, ...) is a new subpackage `modelconnectors/<name>/` whose provider class offers the methods below, and a line in the registry. Nothing else
in the project needs to know which engine runs a conversation.

The rules of the contract
  * Messages go in as Ollama-style dicts ({"role", "content", "tool_calls"?, "tool_name"?}); a connector translates them for its engine.
  * `chat_stream` yields chunks in the Ollama shape: {"message": {"role", "content", "thinking"?, "tool_calls"?: [{"function": {"name", "arguments": {...}}}]},
    "done": bool} and, on the last chunk, "prompt_eval_count", "eval_count", "eval_duration" (nanoseconds). Tool calls arrive complete (arguments already a dict).
  * `tools` is None for a model that has no native tool calling: the agent then teaches the model a text protocol, and the connector sends a plain conversation.
  * `status()` never raises: {"connected": bool, "endpoint"?, "version"?, "error"?}. Other methods raise a subclass of `ConnectorError` with a message fit for the user.
  * `list_models()` rows have at least "name" and "size"; `show(name)` returns {"name", "context_length", "capabilities": [...], "template", "parameters", "details"...}.
  * Arguments are passed positionally (the two connectors name some of them differently).
A connector must not import another connector: shared code lives here or in `core/` (checked by tests/test_architecture.py).
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Protocol, runtime_checkable


class ConnectorError(Exception):
    """An engine could not do what was asked (not reachable, model missing, bad request ...). The message is shown to the user."""


@runtime_checkable
class Connector(Protocol):
    async def status(self) -> dict: ...

    async def list_models(self) -> list[dict]: ...

    async def show(self, name: str) -> dict: ...

    def chat_stream(self, model: str, messages: list[dict], tools: list[dict] | None, num_ctx: int | None, options: dict | None = None, think: Any = None) -> AsyncIterator[dict]: ...

    async def chat_once(self, model: str, messages: list[dict], num_ctx: int | None = None, options: dict | None = None, think: Any = None) -> str: ...

    async def running(self) -> list[dict]: ...

    async def unload(self, name: str | None = None) -> None: ...

    async def delete(self, name: str) -> Any: ...

    def pull_stream(self, model: str) -> AsyncIterator[dict]: ...
