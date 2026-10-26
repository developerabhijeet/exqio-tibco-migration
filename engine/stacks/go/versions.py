"""The Go dependency table (target-stacks spec section 4): every module the generated ``go.mod``
requires, pinned here once, with ``go.sum.lock`` (next to this file) holding their checksums.

No agent adds a dependency. A generated project builds with ``GOFLAGS=-mod=readonly``,
``GOSUMDB=off``, ``GOTOOLCHAIN=local`` and ``GOTELEMETRY=off`` against GMF's ``GOPROXY``, so the
checksums are ExiQO's, never resolved per build: refresh both with ``make stack-locks`` (a scratch
module, ``GOFLAGS=-mod=mod go mod tidy`` against ``$GOPROXY``) and copy the require blocks here.
"""
from __future__ import annotations

from pathlib import Path

#: The Go release the platform image and the generated Dockerfile's build image carry: the newest
#: patch when this table was refreshed (global constraint: Go 1.25 or later).
GO_VERSION = "1.27.3"
#: The ``go`` line of the generated ``go.mod`` (no ``toolchain`` line: the image's Go builds it).
GO_MIN = "1.25.0"

LOCK = Path(__file__).resolve().parent / "go.sum.lock"

#: (module, version) of every direct requirement, in go.mod order.
DIRECT: tuple[tuple[str, str], ...] = (
    ("github.com/Azure/azure-sdk-for-go/sdk/azidentity", "v1.15.0"),
    ("github.com/Azure/azure-sdk-for-go/sdk/messaging/azservicebus", "v1.11.0"),
    ("github.com/Azure/azure-sdk-for-go/sdk/security/keyvault/azsecrets", "v1.5.0"),
    ("github.com/MicahParks/keyfunc/v3", "v3.8.2"),
    ("github.com/golang-jwt/jwt/v5", "v5.3.1"),
    ("github.com/jackc/pgx/v5", "v5.11.0"),
)

#: (module, version) of every indirect requirement ``go mod tidy`` lists, in go.mod order.
INDIRECT: tuple[tuple[str, str], ...] = (
    ("github.com/Azure/azure-sdk-for-go/sdk/azcore", "v1.23.3"),
    ("github.com/Azure/azure-sdk-for-go/sdk/internal", "v1.13.0"),
    ("github.com/Azure/azure-sdk-for-go/sdk/security/keyvault/internal", "v1.2.0"),
    ("github.com/Azure/go-amqp", "v1.7.0"),
    ("github.com/AzureAD/microsoft-authentication-library-for-go", "v1.10.1"),
    ("github.com/MicahParks/jwkset", "v0.11.3"),
    ("github.com/google/uuid", "v1.6.0"),
    ("github.com/jackc/pgpassfile", "v1.0.0"),
    ("github.com/jackc/pgservicefile", "v0.0.0-20240606120523-5a60cdf6a761"),
    ("github.com/jackc/puddle/v2", "v2.2.2"),
    ("github.com/kylelemons/godebug", "v1.1.0"),
    ("github.com/pkg/browser", "v0.0.0-20240102092130-5ac0b6a4141c"),
    ("golang.org/x/crypto", "v0.55.0"),
    ("golang.org/x/net", "v0.58.0"),
    ("golang.org/x/sync", "v0.22.0"),
    ("golang.org/x/sys", "v0.47.0"),
    ("golang.org/x/text", "v0.41.0"),
    ("golang.org/x/time", "v0.15.0"),
)

# Default base images of the generated Dockerfile (documentation only: the Dockerfile has no
# default, GMF passes its own registry's images as BASE_IMAGE_BUILD / BASE_IMAGE_RUNTIME).
BASE_IMAGE_BUILD = f"golang:{GO_VERSION}"
BASE_IMAGE_RUNTIME = "gcr.io/distroless/static-debian12:nonroot"


def go_sum(required: set[str] | None = None) -> str:
    """``go.sum`` for a ``go.mod`` requiring ``required`` modules (default: every module above):
    the lock's lines whose module is required, in the lock's order."""
    want = required if required is not None else {m for m, _ in DIRECT + INDIRECT}
    lines = [ln for ln in LOCK.read_text(encoding="utf-8").splitlines() if ln.split(" ", 1)[0] in want]
    return "\n".join(lines) + "\n"
