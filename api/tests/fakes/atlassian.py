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


class AtlassianFakes:
    """One flavor's Confluence and Jira fakes, as ``client_atlassian`` hands them out. Attributes it
    does not have (``page_count``, ``only_page``, ``bump_version``, ``conflict_always``...) are the
    Confluence fake's, so a Confluence test reads ``fake.only_page()``; Jira is ``fake.jira``."""

    _OWN = frozenset({"confluence", "jira"})

    def __init__(self, confluence, jira) -> None:
        object.__setattr__(self, "confluence", confluence)
        object.__setattr__(self, "jira", jira)

    def __getattr__(self, name: str):
        return getattr(self.confluence, name)

    def __setattr__(self, name: str, value) -> None:
        if name in self._OWN:
            object.__setattr__(self, name, value)
        else:
            setattr(self.confluence, name, value)
