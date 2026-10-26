"""The environment for child processes that build, test, run or validate generated output.

``dotnet build/test/run`` and the Terraform and Terragrunt validators execute model-written code
and configuration. They must not inherit the API's own secrets (model provider tokens, the app
secret, observability keys, the platform database URL); ASP.NET would even load them into
``IConfiguration``. ``child_env`` therefore starts from an allowlist of the variables the tools need
(paths, locale, proxy and CA settings, the tools' own ``DOTNET_*``, ``NUGET_*``, ``TF_*`` and
Terragrunt variables, the Docker and Testcontainers settings the generated tests use when no test
database is given) and adds only what the caller passes explicitly. Everything else is dropped.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
from collections.abc import Mapping

ALLOWED_NAMES = frozenset({
    "PATH", "HOME", "TMPDIR", "LANG", "TZ",
    "EXIQO_NUGET_CACHE",
    "TF_CLI_CONFIG_FILE",  # the provider network mirror (GM Financial: no registry egress)
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
    # Testcontainers finds the Docker daemon through these when no TEST_DATABASE_URL is passed.
    "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY",
})
ALLOWED_PREFIXES = ("LC_", "DOTNET_", "NUGET_", "TF_", "TERRAGRUNT_", "TG_", "TESTCONTAINERS_")


def allowed(name: str) -> bool:
    """True when ``name`` may be inherited from the parent environment."""
    return name in ALLOWED_NAMES or name.startswith(ALLOWED_PREFIXES)


def child_env(extra: Mapping[str, str] | None = None,
              base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The allowlisted part of ``base`` (default ``os.environ``) plus everything in ``extra``."""
    source = os.environ if base is None else base
    env = {k: v for k, v in source.items() if allowed(k)}
    if extra:
        env.update(extra)
    return env


__all__ = ["ALLOWED_NAMES", "ALLOWED_PREFIXES", "allowed", "child_env"]


_URL_USERINFO = re.compile(r"(\b[A-Za-z][A-Za-z0-9+.-]*://)[^/\s@'\"<>]+@")


def scrub_urls(text: str) -> str:
    """``scheme://user:password@host`` -> ``scheme://***@host`` everywhere in ``text`` (a mirror URL
    with credentials echoed by a tool must not reach a stored log)."""
    return _URL_USERINFO.sub(r"\1***@", text or "")


def run_captured(cmd: list[str], *, cwd: str | os.PathLike[str], env: Mapping[str, str],
                 timeout_s: float) -> tuple[int, str, bool]:
    """Run ``cmd`` in its own session and return ``(exit code, scrubbed stdout+stderr, timed out)``.

    On timeout the whole process group is killed (a build tool's daemons and forks go with it), the
    output read so far is kept (decoded, never dropped as bytes) and the code is 124.
    ``FileNotFoundError`` when the tool does not exist, as ``subprocess.run`` raises it.
    """
    proc = subprocess.Popen(cmd, cwd=cwd, env=dict(env), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        out, err = proc.communicate()
        return 124, scrub_urls(_text(out) + _text(err)), True
    return proc.returncode, scrub_urls(_text(out) + _text(err)), False


def _text(data: bytes | str | None) -> str:
    if data is None:
        return ""
    return data if isinstance(data, str) else data.decode("utf-8", errors="replace")
