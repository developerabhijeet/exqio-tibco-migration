"""The ``git`` CLI as the GitHub connector runs it: hermetic, credential by environment only.

- Nothing of the host's git setup applies: no system or global config (``GIT_CONFIG_NOSYSTEM``,
  ``GIT_CONFIG_GLOBAL=/dev/null``), a ``HOME`` that does not exist (no ``~/.netrc``, no global
  ignore or attributes file), no credential helper, no askpass, no terminal prompt, no hooks.
- The token travels only as ``http.<remote>/.extraHeader`` in ``GIT_CONFIG_KEY_0/VALUE_0`` for the
  one command, scoped to the configured GitHub URL, so git sends it to that host only (never in a
  URL, never written to ``.git/config`` or a file). Redirects are not followed.
- Only ``https`` (``http`` only with the loopback flag, for the local fakes); TLS is verified
  against the same CA bundle as the HTTP client (``GIT_SSL_CAINFO``).
- The environment starts from ``engine.child_env`` (the API's own secrets are not inherited).
- Every error text is redacted of the credential before it leaves this module.
"""
from __future__ import annotations

import base64
import os
import signal
import subprocess
from pathlib import Path

from api.app.connectors.base import ConnectorError
from api.app.connectors.http import default_ca_bundle, header_secrets, redact
from engine.child_env import child_env

#: ``HOME`` for git: a path that does not exist, so no per-user file is ever read.
NO_HOME = "/nonexistent/exiqo-git-home"
STDERR_TAIL = 800
#: Abort a transfer slower than 1 KB/s for 60 s (the overall timeout is the caller's).
LOW_SPEED_LIMIT, LOW_SPEED_TIME = "1000", "60"
AUTH_PREFIX = "Authorization: "


def auth_header_value(token: str) -> str:
    """What git sends: Basic ``x-access-token:<token>``, which GitHub (Enterprise Server, Cloud)
    accepts for a PAT and for an App installation token alike."""
    return "Basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()


def git_env(token: str, *, allow_http_loopback: bool, remote_root: str,
            ca_bundle: str | None = None) -> dict[str, str]:
    """The child environment of one git command against ``remote_root`` (the GitHub base URL;
    the credential header is scoped to it)."""
    config: list[tuple[str, str]] = [
        (f"http.{remote_root.rstrip('/')}/.extraHeader", AUTH_PREFIX + auth_header_value(token)),
        ("credential.helper", ""),
        ("core.askPass", ""),
        ("core.hooksPath", os.devnull),
        ("core.fsmonitor", "false"),
        ("protocol.allow", "never"),
        ("protocol.https.allow", "always"),
        ("submodule.recurse", "false"),
        ("http.followRedirects", "false"),
        ("advice.detachedHead", "false"),
    ]
    if allow_http_loopback:
        config.append(("protocol.http.allow", "always"))
    env = child_env({
        "HOME": NO_HOME, "XDG_CONFIG_HOME": NO_HOME,
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "SSH_ASKPASS": "", "GCM_INTERACTIVE": "never",
        "GIT_LFS_SKIP_SMUDGE": "1",
        "GIT_SSL_CAINFO": ca_bundle or default_ca_bundle(),
        "GIT_HTTP_LOW_SPEED_LIMIT": LOW_SPEED_LIMIT, "GIT_HTTP_LOW_SPEED_TIME": LOW_SPEED_TIME,
        "LC_ALL": "C",
        "GIT_CONFIG_COUNT": str(len(config)),
    })
    for i, (key, value) in enumerate(config):
        env[f"GIT_CONFIG_KEY_{i}"] = key
        env[f"GIT_CONFIG_VALUE_{i}"] = value
    return env


def env_secrets(env: dict[str, str]) -> list[str]:
    """The credential values ``env`` carries (to redact from anything git says)."""
    out: list[str] = []
    for name, value in env.items():
        if name.startswith("GIT_CONFIG_VALUE_") and value.startswith(AUTH_PREFIX):
            out += header_secrets({"authorization": value[len(AUTH_PREFIX):]})
    return out


def run_git(args: list[str], cwd: Path, env: dict[str, str], timeout_s: int) -> str:
    """``git <args>`` in ``cwd``; its stdout. ``ConnectorError`` (the redacted tail of stderr) when
    it fails, and when it outlives ``timeout_s`` (the whole process group is killed)."""
    secrets = env_secrets(env)
    what = f"git {args[0]}" if args else "git"
    try:
        proc = subprocess.Popen(["git", *args], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    except FileNotFoundError:
        raise ConnectorError("git is not installed on this worker") from None
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)  # git-remote-https runs as a child of git
        except ProcessLookupError:
            pass
        proc.communicate()
        raise ConnectorError(f"{what} timed out after {timeout_s} s") from None
    if proc.returncode:
        tail = redact(err.decode(errors="replace").strip()[-STDERR_TAIL:], secrets)
        raise ConnectorError(f"{what} failed (exit {proc.returncode}): {tail}")
    return redact(out.decode(errors="replace"), secrets)
