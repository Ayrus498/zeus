"""Request bodies of the HTTP API (pydantic)."""
from __future__ import annotations

from pydantic import BaseModel


class ResetBody(BaseModel):
    confirm: str = ""
    items: list[str] = []

class MemoryBody(BaseModel):
    text: str

class NoteBody(BaseModel):
    note: str

class AddProject(BaseModel):
    path: str

class CreateProject(BaseModel):
    parent: str
    name: str

class PermUpdate(BaseModel):
    mode: str
    custom: dict | None = None

class SessionCreate(BaseModel):
    project_id: int
    title: str | None = None
    model: str | None = None
    num_ctx: int | None = None
    engine: str | None = None

class SessionPatch(BaseModel):
    title: str | None = None
    model: str | None = None
    num_ctx: int | None = None
    engine: str | None = None

class EngineReq(BaseModel):
    engine: str
    session_id: int | None = None

class LlamaModelReq(BaseModel):
    model: str

class LlamaDownload(BaseModel):
    repo: str
    file: str

class ModelReq(BaseModel):
    model: str

class UrlReq(BaseModel):
    url: str

class SaveFile(BaseModel):
    path: str
    content: str
    session_id: int | None = None
    overwrite: bool = False

class PinsUpdate(BaseModel):
    code: str | None = None
    think: str | None = None
    clear: list[str] = []

class PreviewReq(BaseModel):
    prompt: str
    last_model: str | None = None

class SettingsUpdate(BaseModel):
    ollama_url: str | None = None
    default_model: str | None = None
    default_num_ctx: int | None = None
