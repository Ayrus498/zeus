"""Runs MarkItDown on one file inside a throw-away subprocess: `python -m app.documents.worker <file> <ext>`.

Why a subprocess: converting untrusted PDFs/Office files/ZIPs is the riskiest thing Zeus-Agent does with attachments. Here it gets a hard
timeout (killed by the parent), CPU and memory limits, and NO network (MarkItDown has converters for audio transcription, YouTube,
Bing and URL fetching that must never run). Writes one JSON object to stdout.
"""
from __future__ import annotations

import json
import os
import resource
import socket
import sys


def _limits() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (60, 70))
    resource.setrlimit(resource.RLIMIT_AS, (3 * 2**30, 3 * 2**30))       # zip / decompression bombs end in MemoryError, not an OOM kill
    resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 2**20, 8 * 2**20))    # the converter has no reason to write big files


def _block_network() -> None:
    """Called AFTER imports (replacing socket.socket itself would break ssl's subclasses). Any connect/lookup fails loudly."""
    def deny(*a, **k):
        raise OSError("network access is disabled inside the Zeus-Agent document converter")
    socket.socket.connect = deny           # type: ignore[assignment]
    socket.socket.connect_ex = deny        # type: ignore[assignment]
    socket.getaddrinfo = deny              # type: ignore[assignment]
    socket.gethostbyname = deny            # type: ignore[assignment]
    socket.create_connection = deny        # type: ignore[assignment]


TEXTLIKE = {"txt", "json", "xml", "md", "markdown", "zip"}      # only these may fall back to "treat as plain text"


def build_converter(ext: str):
    """A MarkItDown instance with ONLY the converters Zeus-Agent allows. No audio/image/YouTube/Bing/Wikipedia/RSS/Outlook/Azure
    converters are even loaded, and binary formats get no plain-text catch-all (so a corrupt PDF fails instead of being read as bytes)."""
    from markitdown import PRIORITY_GENERIC_FILE_FORMAT, MarkItDown
    from markitdown.converters import (CsvConverter, DocxConverter, EpubConverter, HtmlConverter, IpynbConverter, PdfConverter,
                                       PlainTextConverter, PptxConverter, XlsConverter, XlsxConverter, ZipConverter)
    md = MarkItDown(enable_builtins=False, enable_plugins=False)
    for cls in (PdfConverter, DocxConverter, PptxConverter, XlsxConverter, XlsConverter, CsvConverter, HtmlConverter, EpubConverter, IpynbConverter):
        md.register_converter(cls())
    md.register_converter(ZipConverter(markitdown=md))
    if ext in TEXTLIKE:
        md.register_converter(PlainTextConverter(), priority=PRIORITY_GENERIC_FILE_FORMAT)
    return md


def main() -> int:
    path, ext = sys.argv[1], sys.argv[2].lower().lstrip(".")
    try:
        _limits()
        from markitdown import StreamInfo                       # heavy imports first, then lock the network down
        md = build_converter(ext)
        _block_network()
        with open(path, "rb") as f:
            res = md.convert_stream(f, stream_info=StreamInfo(extension="." + ext))
        out = {"ok": True, "markdown": res.markdown or "", "title": res.title}
    except MemoryError:
        out = {"ok": False, "error": "the file needs too much memory to convert (is it a compressed bomb or a huge document?)"}
    except BaseException as e:  # noqa: BLE001 - report anything, never crash silently
        out = {"ok": False, "error": f"{type(e).__name__}: {e}"[:400]}
    sys.stdout.write(json.dumps(out))
    sys.stdout.flush()
    os._exit(0)                                                  # skip interpreter teardown of heavy libraries


if __name__ == "__main__":
    sys.exit(main())
