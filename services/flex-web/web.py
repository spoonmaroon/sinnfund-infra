#!/usr/bin/env python3
"""Compatibility proxy from the retired close-only service to the merged API."""

from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response


MERGED_API = "http://127.0.0.1:8002"
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/{path:path}", response_class=Response)
def merged_service(request: Request, path: str):
    target = f"{MERGED_API}/{path.lstrip('/')}"
    if request.url.query:
        target += "?" + request.url.query
    try:
        upstream = urlopen(target, timeout=30)
    except HTTPError as exc:
        upstream = exc
    except URLError:
        return PlainTextResponse("Merged portfolio API unavailable\n", status_code=502)
    return Response(
        content=upstream.read(),
        status_code=upstream.status,
        media_type=upstream.headers.get_content_type(),
        headers={"Cache-Control": "no-store", "X-Portfolio-Service": "merged"},
    )
