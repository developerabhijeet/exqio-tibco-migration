"""``ConnectorClient``: the only way a connector reaches a remote service.

- ``https`` only; ``http`` to 127.0.0.1 only with ``CONNECTORS_ALLOW_INSECURE_LOOPBACK`` (tests and
  local fakes; the chart schema refuses it).
- The host of every request, and of every redirect target, must be in the connector's allowed
  hosts. Redirects are followed by hand, at most 3, each re-checked before anything is sent, so
  the ``Authorization`` header never reaches a host off the list (an SSO page, an attacker).
- Timeouts 10 s connect, 30 s read; at most 3 retries on 429/502/503/504, honouring
  ``Retry-After`` (capped at 30 s); responses over 20 MB refused.
- Every error text is redacted of the credentials the auth header carries.
"""
from __future__ import annotations

import asyncio
import base64
import email.utils
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from api.app.connectors.base import ConnectorError, HostNotAllowed
from api.app.connectors.config import LOOPBACK, host_allowed

TIMEOUT = httpx.Timeout(30.0, connect=10.0)
MAX_REDIRECTS = 3
MAX_RETRIES = 3
RETRY_STATUSES = frozenset({429, 502, 503, 504})
RETRY_AFTER_CAP_S = 30.0
MAX_RESPONSE_BYTES = 20 * 1024 * 1024
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
# Recomputed by httpx for the buffered body (which aiter_bytes has already decoded).
_DROP_HEADERS = frozenset({"content-encoding", "content-length", "transfer-encoding"})


def redact(text: str, secrets: list[str]) -> str:
    """``text`` with every non-empty secret replaced by ``***``, longest first."""
    for value in sorted({s for s in secrets if s}, key=len, reverse=True):
        text = text.replace(value, "***")
    return text


def header_secrets(headers: dict[str, str]) -> list[str]:
    """The credential values an auth header carries: the whole value, the part after the scheme
    and, for ``Basic``, the decoded ``user:password`` and both halves."""
    out: list[str] = []
    for value in headers.values():
        out.append(value)
        scheme, _, rest = value.partition(" ")
        if rest:
            out.append(rest)
            if scheme.lower() == "basic":
                try:
                    decoded = base64.b64decode(rest, validate=True).decode()
                except ValueError:
                    continue
                out += [decoded, *decoded.split(":", 1)]
    return [s for s in out if len(s) >= 3]


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    raw = response.headers.get("retry-after", "").strip()
    delay: float | None = None
    if raw.isdigit():
        delay = float(raw)
    elif raw:
        try:
            when = email.utils.parsedate_to_datetime(raw)
            delay = max(0.0, when.timestamp() - time.time())
        except (TypeError, ValueError):
            delay = None
    if delay is None:
        delay = float(2 ** attempt)  # 1, 2, 4
    return min(delay, RETRY_AFTER_CAP_S)


class ConnectorClient:
    def __init__(self, base_url: str, allowed_hosts: list[str], auth_header: Callable[[], dict[str, str]], *,
                 allow_insecure_loopback: bool = False, transport: httpx.AsyncBaseTransport | None = None,
                 user_agent: str = "ExiQO-Migrate") -> None:
        self.base_url = base_url.rstrip("/")
        self.allowed_hosts = list(allowed_hosts)
        self.auth_header = auth_header
        self.allow_insecure_loopback = allow_insecure_loopback
        self.transport = transport
        self.user_agent = user_agent

    def _resolve(self, path_or_url: str) -> str:
        if urlsplit(path_or_url).scheme:
            return path_or_url
        return f"{self.base_url}/{path_or_url.lstrip('/')}" if path_or_url else self.base_url

    def check(self, url: str) -> None:
        """``HostNotAllowed`` unless ``url`` may be requested."""
        parts = urlsplit(url)
        netloc = parts.netloc
        if parts.scheme == "http":
            if not (self.allow_insecure_loopback and parts.hostname == LOOPBACK):
                raise HostNotAllowed(netloc, f"host {netloc} refused: https required")
        elif parts.scheme != "https":
            raise HostNotAllowed(netloc, f"host {netloc} refused: https required")
        if not host_allowed(netloc, self.allowed_hosts, parts.scheme):
            raise HostNotAllowed(netloc)

    async def request(self, method: str, path_or_url: str, **kw: Any) -> httpx.Response:
        """One logical request: redirects followed (re-checked) and retries done here."""
        url = self._resolve(path_or_url)
        auth = self.auth_header()
        secrets = header_secrets(auth)
        headers = {"User-Agent": self.user_agent, **(kw.pop("headers", None) or {}), **auth}
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False, transport=self.transport,
                                     trust_env=False) as client:
            for _hop in range(MAX_REDIRECTS + 1):
                self.check(url)
                response = await self._with_retries(client, method, url, headers, secrets, kw)
                if response.status_code not in REDIRECT_STATUSES or "location" not in response.headers:
                    return response
                url = urljoin(url, response.headers["location"])
                # as browsers and httpx do: 303, and 301/302 after a write, continue as a bodiless GET
                status = response.status_code
                if method not in ("GET", "HEAD") and (status == 303 or status in (301, 302)):
                    method = "GET"
                    kw = {k: v for k, v in kw.items() if k not in ("json", "content", "data", "files")}
            raise ConnectorError(f"too many redirects (more than {MAX_REDIRECTS}) from {self.base_url}")

    async def _with_retries(self, client: httpx.AsyncClient, method: str, url: str, headers: dict[str, str],
                            secrets: list[str], kw: dict[str, Any]) -> httpx.Response:
        for attempt in range(MAX_RETRIES + 1):
            response = await self._send(client, method, url, headers, secrets, kw)
            if response.status_code not in RETRY_STATUSES or attempt == MAX_RETRIES:
                return response
            await asyncio.sleep(_retry_delay(response, attempt))
        raise AssertionError("unreachable")

    async def _send(self, client: httpx.AsyncClient, method: str, url: str, headers: dict[str, str],
                    secrets: list[str], kw: dict[str, Any]) -> httpx.Response:
        try:
            request = client.build_request(method, url, headers=headers, **kw)
            response = await client.send(request, stream=True)
        except httpx.HTTPError as exc:
            detail = redact(str(exc) or type(exc).__name__, secrets)
            raise ConnectorError(f"{method} {urlsplit(url).netloc}: {detail}") from None
        try:
            declared = response.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
                raise ConnectorError(f"the response from {urlsplit(url).netloc} is over 20 MB: refused")
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise ConnectorError(f"the response from {urlsplit(url).netloc} is over 20 MB: refused")
                chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"{method} {urlsplit(url).netloc}: "
                                 f"{redact(str(exc) or type(exc).__name__, secrets)}") from None
        finally:
            await response.aclose()
        kept = [(k, v) for k, v in response.headers.multi_items() if k.lower() not in _DROP_HEADERS]
        return httpx.Response(response.status_code, headers=kept, content=b"".join(chunks), request=request)

    async def json(self, method: str, path: str, **kw: Any) -> Any:
        """The JSON body of a 2xx answer; ``ConnectorError`` (redacted, with the status) otherwise."""
        secrets = header_secrets(self.auth_header())
        response = await self.request(method, path, **kw)
        where = f"{method} {urlsplit(str(response.request.url)).path}"
        if response.status_code >= 400:
            text = redact(response.text[:500], secrets)
            raise ConnectorError(f"{where}: HTTP {response.status_code}: {text}", response.status_code)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            raise ConnectorError(f"{where}: the answer is not JSON", response.status_code) from None
