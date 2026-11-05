"""``ConnectorClient``: the only way a connector reaches a remote service.

- ``https`` only; ``http`` to 127.0.0.1 only with ``CONNECTORS_ALLOW_INSECURE_LOOPBACK`` (tests and
  local fakes; the chart schema refuses it).
- The host of every request, and of every redirect target, must be in the connector's allowed
  hosts. Redirects are followed by hand, at most 3, each re-checked before anything is sent, so
  the ``Authorization`` header never reaches a host off the list (an SSO page, an attacker).
- Timeouts 10 s connect, 30 s read; at most 3 retries on 429/502/503/504, honouring
  ``Retry-After`` (capped at 30 s); responses over 20 MB refused.
- Retries of a non-idempotent request (POST, PATCH) only when the server said it did not act on it:
  429, or 503 with ``Retry-After``.
- TLS is verified against an explicit CA bundle (``CONNECTORS_CA_BUNDLE``, else ``SSL_CERT_FILE``,
  else certifi), so a GMF private CA is trusted without ``trust_env``; verification is never off.
- Every error text is redacted of the credentials the auth header carries.
"""
from __future__ import annotations

import asyncio
import base64
import email.utils
import os
import ssl
import time
from collections.abc import Callable
from functools import lru_cache
from typing import Any
from urllib.parse import urljoin, urlsplit

import certifi
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
NON_IDEMPOTENT = frozenset({"POST", "PATCH"})
BODY_ARGS = ("json", "content", "data", "files")
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


def default_ca_bundle() -> str:
    return os.environ.get("SSL_CERT_FILE") or certifi.where()


@lru_cache(maxsize=8)
def _context(cafile: str, mtime_ns: int) -> ssl.SSLContext:
    del mtime_ns  # part of the cache key only: a replaced bundle is loaded again
    return ssl.create_default_context(cafile=cafile)


def ssl_context(ca_bundle: str | None = None) -> ssl.SSLContext:
    """A verifying TLS context trusting exactly ``ca_bundle`` (default: ``default_ca_bundle()``)."""
    cafile = ca_bundle or default_ca_bundle()
    try:
        mtime = os.stat(cafile).st_mtime_ns
    except OSError as exc:
        raise ConnectorError(f"the connectors CA bundle {cafile} cannot be read: {exc.strerror}") from None
    try:
        return _context(cafile, mtime)
    except ssl.SSLError as exc:
        raise ConnectorError(f"the connectors CA bundle {cafile} is not a PEM bundle: {exc}") from None


def _endpoint(scheme: str, host: str, port: int | None) -> tuple[str, int]:
    return host.lower(), port or (443 if scheme == "https" else 80)


def _retryable(method: str, response: httpx.Response) -> bool:
    if response.status_code not in RETRY_STATUSES:
        return False
    if method.upper() in NON_IDEMPOTENT:  # the server may have acted: retry only a clear refusal
        return response.status_code == 429 or (response.status_code == 503 and "retry-after" in response.headers)
    return True


class ConnectorClient:
    def __init__(self, base_url: str, allowed_hosts: list[str], auth_header: Callable[[], dict[str, str]], *,
                 allow_insecure_loopback: bool = False, transport: httpx.AsyncBaseTransport | None = None,
                 user_agent: str = "ExiQO-Migrate", ca_bundle: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.allowed_hosts = list(allowed_hosts)
        self.auth_header = auth_header
        self.allow_insecure_loopback = allow_insecure_loopback
        self.transport = transport
        self.user_agent = user_agent
        self.ca_bundle = ca_bundle
        self.max_bytes = MAX_RESPONSE_BYTES  # a caller may lower it (Figma thumbnails: 5 MB)

    def _resolve(self, path_or_url: str) -> str:
        if urlsplit(path_or_url).scheme:
            return path_or_url
        return f"{self.base_url}/{path_or_url.lstrip('/')}" if path_or_url else self.base_url

    def check(self, url: str) -> None:
        """``HostNotAllowed`` unless ``url`` may be requested."""
        try:
            parts = urlsplit(url)
            parts.port  # noqa: B018  a malformed port raises here
        except ValueError:
            raise HostNotAllowed(url, "refused: not a valid URL") from None
        netloc = parts.netloc
        if "@" in netloc:
            raise HostNotAllowed(netloc.rpartition("@")[2], "refused: credentials in the URL")
        if parts.scheme == "http":
            if not (self.allow_insecure_loopback and parts.hostname == LOOPBACK):
                raise HostNotAllowed(netloc, f"host {netloc} refused: https required")
        elif parts.scheme != "https":
            raise HostNotAllowed(netloc, f"host {netloc} refused: https required")
        if not host_allowed(netloc, self.allowed_hosts, parts.scheme):
            raise HostNotAllowed(netloc)

    async def request(self, method: str, path_or_url: str, **kw: Any) -> httpx.Response:
        """One logical request: redirects followed (re-checked) and retries done here."""
        response, _ = await self._request(method, path_or_url, kw)
        return response

    async def _request(self, method: str, path_or_url: str,
                       kw: dict[str, Any]) -> tuple[httpx.Response, list[str]]:
        """The response and the credential values to redact from anything said about it (read once
        per request, so ``json()`` redacts what this request actually sent)."""
        url = self._resolve(path_or_url)
        auth = self.auth_header()
        secrets = header_secrets(auth)
        headers = {"User-Agent": self.user_agent, **(kw.pop("headers", None) or {}), **auth}
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False, transport=self.transport,
                                     trust_env=False, verify=ssl_context(self.ca_bundle)) as client:
            for _hop in range(MAX_REDIRECTS + 1):
                self.check(url)
                response = await self._with_retries(client, method, url, headers, secrets, kw)
                if response.status_code not in REDIRECT_STATUSES or "location" not in response.headers:
                    return response, secrets
                url = urljoin(str(response.request.url), response.headers["location"])
                kw = {k: v for k, v in kw.items() if k != "params"}  # the Location carries its own query
                # as browsers and httpx do: 303, and 301/302 after a write, continue as a bodiless GET
                status = response.status_code
                if method not in ("GET", "HEAD") and (status == 303 or status in (301, 302)):
                    method = "GET"
                    kw = {k: v for k, v in kw.items() if k not in BODY_ARGS}
            raise ConnectorError(f"too many redirects (more than {MAX_REDIRECTS}) from {self.base_url}")

    async def _with_retries(self, client: httpx.AsyncClient, method: str, url: str, headers: dict[str, str],
                            secrets: list[str], kw: dict[str, Any]) -> httpx.Response:
        for attempt in range(MAX_RETRIES + 1):
            response = await self._send(client, method, url, headers, secrets, kw)
            if attempt == MAX_RETRIES or not _retryable(method, response):
                return response
            await asyncio.sleep(_retry_delay(response, attempt))
        raise AssertionError("unreachable")

    async def _send(self, client: httpx.AsyncClient, method: str, url: str, headers: dict[str, str],
                    secrets: list[str], kw: dict[str, Any]) -> httpx.Response:
        try:
            request = client.build_request(method, url, headers=headers, **kw)
        except (httpx.InvalidURL, httpx.HTTPError, ValueError) as exc:
            raise ConnectorError(f"{method}: not a valid URL: {redact(str(exc), secrets)}") from None
        # what httpx will actually connect to, after its own parsing of the URL and ``params``
        self.check(str(request.url))
        asked = urlsplit(url)
        sent = request.url
        if (_endpoint(sent.scheme, sent.raw_host.decode("ascii"), sent.port)
                != _endpoint(asked.scheme, asked.hostname or "", asked.port)):
            raise HostNotAllowed(request.url.netloc.decode(), "refused: the URL resolves to another host")
        try:
            response = await client.send(request, stream=True)
        except httpx.HTTPError as exc:
            detail = redact(str(exc) or type(exc).__name__, secrets)
            raise ConnectorError(f"{method} {urlsplit(url).netloc}: {detail}") from None
        try:
            declared = response.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > self.max_bytes:
                raise ConnectorError(f"the response from {urlsplit(url).netloc} is over {self.max_bytes // (1024 * 1024)} MB: refused")
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > self.max_bytes:
                    raise ConnectorError(f"the response from {urlsplit(url).netloc} is over {self.max_bytes // (1024 * 1024)} MB: refused")
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
        response, secrets = await self._request(method, path, kw)
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
