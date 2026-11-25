import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.core.database import Database
from app.toolchain.permissions import PermissionManager
from app.toolchain.runner import Runner
from app.toolchain.engine import ToolContext, ToolEngine


@pytest.fixture(autouse=True)
def _isolated_zeus_home(tmp_path_factory, monkeypatch):
    """Never let tests touch the real ~/.zeus."""
    monkeypatch.setenv("ZEUS_HOME", str(tmp_path_factory.mktemp("zeushome")))


@pytest.fixture(autouse=True)
def _no_real_llama_cpp(monkeypatch):
    """Tests must not find (or run) a llama-server, or list models, that happen to be installed on the developer's machine."""
    from app.modelconnectors.llamacpp import finder as llamafind
    from app.modelconnectors.llamacpp.provider import LlamaCppProvider
    from app.modelconnectors.llamacpp import ollama_models as ollamastore
    monkeypatch.setattr(llamafind, "discover", lambda *a, **k: [])
    monkeypatch.setattr(LlamaCppProvider, "_standard", lambda self: [])
    monkeypatch.setattr(ollamastore, "store_dirs", lambda *a, **k: [])                 # nor the Ollama models stored on the developer's machine


@pytest.fixture
def ws_dir(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    return d


@pytest.fixture
def db():
    return Database(":memory:")


class Harness:
    """Builds a ToolContext whose approver answers from a script and records every request."""
    def __init__(self, db, root, mode="approval", answers=None):
        self.requests, self.events = [], []
        self.answers = list(answers or [])
        pid = db.x("INSERT INTO projects(name,path,last_opened) VALUES(?,?,0)", ("p", str(root)))

        async def request(req):
            self.requests.append(req)
            return self.answers.pop(0) if self.answers else "reject"

        async def emit(ev):
            self.events.append(ev)

        self.perms = PermissionManager(mode)
        self.ctx = ToolContext({"id": pid, "name": "p", "path": str(root)}, None, "coder", self.perms, db, Runner(), emit, request)
        self.tools = ToolEngine()

    async def call(self, name, **args):
        return await self.tools.execute(self.ctx, name, args)


@pytest.fixture
def harness(db, ws_dir):
    def make(mode="approval", answers=None):
        return Harness(db, ws_dir, mode, answers)
    return make


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "z.db")) as c:
        yield c
