"""Attaching documents: conversion to Markdown."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ...documents import service as convert_service


def router(ctx) -> APIRouter:
    r = APIRouter()

    @r.get("/api/convert/formats")
    async def convert_formats():
        """Which attachment types Zeus-Agent can turn into Markdown, and whether MarkItDown is installed."""
        return convert_service.info()

    @r.post("/api/convert")
    async def convert_file(request: Request, filename: str):
        """Convert an uploaded document (raw request body) to Markdown with MarkItDown, in an isolated subprocess.
        The result is only returned to the caller; nothing is stored or written into any project."""
        limit = convert_service.MAX_INPUT
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise HTTPException(413, f"The file is larger than {limit // 2**20} MB.")
        chunks, size = [], 0
        async for c in request.stream():
            size += len(c)
            if size > limit:
                raise HTTPException(413, f"The file is larger than {limit // 2**20} MB.")
            chunks.append(c)
        try:
            return await convert_service.convert_bytes(b"".join(chunks), filename)
        except convert_service.ConvertError as e:
            raise HTTPException(e.status, e.message)

    return r
