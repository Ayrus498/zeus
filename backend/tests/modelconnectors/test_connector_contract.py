"""Every connector keeps the contract of modelconnectors/base.py, so a new engine can be added without touching the agent."""
import inspect

import pytest

from app.core.database import Database
from app.modelconnectors.base import Connector, ConnectorError
from app.modelconnectors.llamacpp.provider import LlamaCppError, LlamaCppProvider
from app.modelconnectors.ollama.provider import OllamaError, OllamaProvider


@pytest.fixture
def connectors():
    return {"ollama": OllamaProvider("http://127.0.0.1:9"), "llamacpp": LlamaCppProvider(Database(":memory:"))}


def test_every_connector_offers_the_methods_of_the_contract(connectors):
    for name, c in connectors.items():
        assert isinstance(c, Connector), name
    required = [n for n, _ in inspect.getmembers(Connector, inspect.isfunction) if not n.startswith("_")]
    assert sorted(required) == ["chat_once", "chat_stream", "delete", "list_models", "pull_stream", "running", "show", "status", "unload"]


def test_the_streaming_and_async_methods_are_the_right_kind_of_callable(connectors):
    for name, c in connectors.items():
        for m in ("status", "list_models", "show", "chat_once", "running", "unload", "delete"):
            assert inspect.iscoroutinefunction(getattr(c, m)), f"{name}.{m} must be async"
        for m in ("chat_stream", "pull_stream"):
            assert inspect.isasyncgenfunction(getattr(c, m)), f"{name}.{m} must be an async generator"
        sig = inspect.signature(c.chat_stream)
        assert list(sig.parameters)[:4] == ["model", "messages", "tools", "num_ctx"], name          # the agent calls it positionally


def test_errors_share_one_neutral_base_and_do_not_depend_on_each_other():
    assert issubclass(OllamaError, ConnectorError) and issubclass(LlamaCppError, ConnectorError)
    assert not issubclass(LlamaCppError, OllamaError) and not issubclass(OllamaError, LlamaCppError)


async def test_status_never_raises_and_says_whether_the_engine_is_usable(connectors):
    for name, c in connectors.items():
        st = await c.status()                                        # Ollama: nothing listens on port 9. llama.cpp: no llama-server is installed in the test setup
        assert isinstance(st, dict) and st["connected"] is False and isinstance(st.get("error"), str) and st["error"], name


async def test_the_agent_depends_on_the_contract_not_on_a_particular_engine(db, harness, ws_dir):
    """A connector that is neither Ollama nor llama.cpp drives the agent loop, and an error it raises is reported to the user like any engine's."""
    from app.agents.engine import AgentEngine
    from app.toolchain.engine import ToolEngine
    from app.workspace.sessions import SessionManager

    class Other:                                                     # a third engine: only the contract
        async def chat_stream(self, model, messages, tools, num_ctx, options=None, think=None):
            yield {"message": {"role": "assistant", "content": "hello from another engine"}}
            yield {"done": True, "prompt_eval_count": 3, "eval_count": 2, "eval_duration": 1}

        async def show(self, name):
            return {}
    h = harness("trusted")
    s = SessionManager(db).create(h.ctx.project["id"], model="m")
    await AgentEngine(db, Other(), ToolEngine()).run(h.ctx, s, "create hello.py")
    assert h.events[-1] == {"type": "run_end", "status": "completed"} and any(e.get("text") == "hello from another engine" for e in h.events if e["type"] == "assistant_end")

    class Broken(Other):
        async def chat_stream(self, *a, **k):
            raise ConnectorError("the engine is down")
            yield                                                    # (makes this an async generator)
    h2 = harness  # a fresh harness cannot reuse the project path: report through the same one
    h.events.clear()
    await AgentEngine(db, Broken(), ToolEngine()).run(h.ctx, s, "create more")
    assert any(e["type"] == "error" and "the engine is down" in e["message"] for e in h.events) and h.events[-1]["status"] == "error"
