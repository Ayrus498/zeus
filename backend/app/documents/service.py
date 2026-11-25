"""Document -> Markdown conversion (Microsoft MarkItDown) for attachments.

Only an explicit allow-list of file types is accepted. Audio, images, URLs and plugins are deliberately NOT supported: MarkItDown's
converters for those use online services or external tools. Conversion runs in `app.documents.worker` (isolated subprocess).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
from importlib import metadata
from pathlib import Path

from ..core.config import MAX_FILE_BYTES

MAX_INPUT = 25 * 1024 * 1024
MAX_MD_BYTES = 200_000                    # same cap as mission documents (see agent.mission.MAX_DOC_BYTES)
TIMEOUT_S = 90
BACKEND_DIR = Path(__file__).resolve().parents[2]

FORMATS: dict[str, str] = {
    "md": "Markdown", "markdown": "Markdown", "txt": "Plain text",
    "pdf": "PDF document", "docx": "Word document", "pptx": "PowerPoint presentation",
    "xlsx": "Excel workbook", "xls": "Excel 97-2003 workbook", "csv": "CSV table",
    "html": "HTML page", "htm": "HTML page", "json": "JSON", "xml": "XML", "epub": "EPUB book",
    "ipynb": "Jupyter notebook", "zip": "ZIP archive (supported documents inside are converted)",
}
_PASSTHROUGH = {"md", "markdown", "txt"}
# MarkItDown silently falls back to "plain text" when a converter rejects a file, which would hand raw bytes to the model.
# Binary formats therefore must carry the right file signature, and their output must not look like the raw input.
_MAGIC = {"pdf": [b"%PDF-"], "docx": [b"PK\x03\x04"], "pptx": [b"PK\x03\x04"], "xlsx": [b"PK\x03\x04"], "epub": [b"PK\x03\x04"],
          "zip": [b"PK\x03\x04", b"PK\x05\x06"], "xls": [b"\xd0\xcf\x11\xe0"]}


class ConvertError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def markitdown_version() -> str | None:
    try:
        return metadata.version("markitdown")
    except metadata.PackageNotFoundError:
        return None


def info() -> dict:
    v = markitdown_version()
    return {"installed": v is not None, "version": v, "max_bytes": MAX_INPUT,
            "formats": [{"ext": e, "label": l, "needs_markitdown": e not in _PASSTHROUGH} for e, l in FORMATS.items()]}


def _safe_name(filename: str) -> str:
    base = os.path.basename(filename or "").strip().replace("\x00", "")
    base = re.sub(r"[^\w .()+-]", "_", base)[:120]
    return base or "document"


def _truncate(md: str) -> tuple[str, int]:
    raw = md.encode("utf-8")
    if len(raw) <= MAX_MD_BYTES:
        return md, 0
    cut = raw[:MAX_MD_BYTES].decode("utf-8", "ignore")
    k = cut.rfind("\n\n")
    cut = cut[:k] if k > MAX_MD_BYTES * 0.6 else cut
    omitted = len(md) - len(cut)
    return cut.rstrip() + f"\n\n> [Zeus-Agent: the converted document was cut at {MAX_MD_BYTES // 1000} KB; {omitted:,} characters were omitted]\n", omitted


def _run_worker(path: str, ext: str, timeout: int) -> dict:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(BACKEND_DIR), "LANG": "C.UTF-8",
           "HOME": tempfile.gettempdir(), "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": tempfile.gettempdir()}
    try:
        p = subprocess.run([sys.executable, "-m", "app.documents.worker", path, ext], cwd=BACKEND_DIR, env=env,
                           capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL, start_new_session=True)
    except subprocess.TimeoutExpired:
        raise ConvertError(408, f"Converting this file took longer than {timeout} seconds and was stopped.")
    if p.returncode != 0 and not p.stdout:
        raise ConvertError(422, "The converter crashed on this file (it may be corrupt or too large to process).")
    try:
        return json.loads(p.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        raise ConvertError(422, "The converter returned no usable output for this file.")


async def convert_bytes(data: bytes, filename: str, timeout: int = TIMEOUT_S) -> dict:
    name = _safe_name(filename)
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in FORMATS:
        raise ConvertError(415, f"'.{ext or '?'}' files are not supported. Supported: " + ", ".join(sorted(f".{e}" for e in FORMATS))
                           + ". (Images and audio are not supported: they would need online services.)")
    if not data:
        raise ConvertError(422, f"{name} is empty.")
    if len(data) > MAX_INPUT:
        raise ConvertError(413, f"{name} is larger than {MAX_INPUT // 2**20} MB.")
    sig = _MAGIC.get(ext)
    if sig and not any(data[:1024].lstrip(b"\x00 \r\n").startswith(m) or (m == b"%PDF-" and m in data[:1024]) for m in sig):
        raise ConvertError(422, f"{name} is not a valid .{ext} file (its content does not look like one). Check the file or convert it to PDF/DOCX/Markdown first.")
    title = None
    if ext in _PASSTHROUGH:
        try:
            md = data.decode("utf-8")
        except UnicodeDecodeError:
            md = data.decode("latin-1")
    else:
        if markitdown_version() is None:
            raise ConvertError(503, "MarkItDown is not installed. Install it with:  pip install 'markitdown[pdf,docx,pptx,xlsx,xls]'")
        fd, tmp = tempfile.mkstemp(prefix="zeus-convert-", suffix="." + ext)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            res = await asyncio.to_thread(_run_worker, tmp, ext, timeout)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        if not res.get("ok"):
            raise ConvertError(422, f"Could not convert {name}: {res.get('error') or 'unknown error'}")
        md, title = res["markdown"], res.get("title")
    md = md.replace("\r\n", "\n").replace("\x00", "")
    if not md.strip():
        raise ConvertError(422, f"No text could be extracted from {name}. If it is a scanned or image-only document, run OCR on it first.")
    md, omitted = _truncate(md)
    out_name = name if ext in ("md", "markdown") else f"{name}.md"
    return {"name": out_name, "source": name, "markdown": md, "title": title, "chars": len(md), "truncated": omitted > 0,
            "omitted_chars": omitted, "converter": "markitdown" if ext not in _PASSTHROUGH else "none"}
