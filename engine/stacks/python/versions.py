"""The Python dependency table (target-stacks spec section 4): every distribution the generated
service depends on, pinned here once. No agent adds a dependency. ``requirements.lock`` is these
plus their dependencies, hash-pinned for every platform (``make stack-locks`` writes it with
``uv pip compile --generate-hashes --universal`` against ``$UV_INDEX_URL``, GMF's mirror); a build
installs it with ``uv pip install --require-hashes`` and never resolves anything itself.

``python -m engine.stacks.python.versions`` prints the requirements the lock is compiled from.
"""
from __future__ import annotations

PYTHON = "3.12"  # the interpreter line (requires-python, the lock's target, the Dockerfile base)

#: (distribution with extras, exact version) the service imports, in pyproject order.
DEPENDENCIES: tuple[tuple[str, str], ...] = (
    ("fastapi", "0.162.0"),
    ("uvicorn", "0.54.0"),
    ("pydantic", "2.14.1"),
    ("psycopg[binary]", "3.3.6"),
    ("azure-identity", "1.26.0"),
    ("azure-servicebus", "7.15.0"),
    ("azure-keyvault-secrets", "4.11.3"),
    ("pyjwt[crypto]", "2.15.1"),
    ("httpx", "0.28.1"),
)
#: The test and format tools (the dev group).
DEV_DEPENDENCIES: tuple[tuple[str, str], ...] = (
    ("pytest", "9.1.1"),
    ("pytest-cov", "7.1.0"),
    ("ruff", "0.17.2"),
)

# The upstream image the generated Dockerfile names for GMF to mirror; the Dockerfile has no default
# (GMF passes its own registry's images as BASE_IMAGE_BUILD and BASE_IMAGE_RUNTIME).
BASE_IMAGE = f"python:{PYTHON}-slim"


#: The uv the pipelines install (by hash, from ``uv-tool.lock``); the platform image's uv is the same.
UV = "0.11.7"


def requirements(group: str = "all") -> str:
    """The requirements a lock is compiled from (``name==version`` lines): ``all`` for
    ``requirements.lock`` (the build and its tests), ``runtime`` for ``requirements.runtime.lock``
    (what the service image installs), ``uv`` for ``uv-tool.lock`` (the pipelines' uv)."""
    if group == "uv":
        return f"uv=={UV}\n"
    deps = DEPENDENCIES if group == "runtime" else DEPENDENCIES + DEV_DEPENDENCIES
    return "".join(f"{name}=={version}\n" for name, version in deps)


if __name__ == "__main__":
    import sys

    print(requirements(sys.argv[1] if len(sys.argv) > 1 else "all"), end="")
