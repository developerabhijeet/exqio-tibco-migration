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
from collections.abc import Mapping

ALLOWED_NAMES = frozenset({
    "PATH", "HOME", "TMPDIR", "LANG", "TZ",
    "EXIQO_NUGET_CACHE",
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
