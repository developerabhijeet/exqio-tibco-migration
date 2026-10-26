"""Shared by the Confluence and Jira fakes: the flavor's auth (Bearer for Data Center, basic for
Cloud)."""
from __future__ import annotations

import base64

from starlette.requests import Request


def authorized(request: Request, flavor: str, token: str, user: str) -> bool:
    got = request.headers.get("authorization", "")
    if flavor == "cloud":
        return got == "Basic " + base64.b64encode(f"{user}:{token}".encode()).decode()
    return got == f"Bearer {token}"
