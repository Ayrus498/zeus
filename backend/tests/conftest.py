import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.database.db import Database
from app.permissions.manager import PermissionManager
from app.terminal.runner import Runner
from app.tools.engine import ToolContext, ToolEngine


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
