# Toolchain (dev container)

Local binaries live in `.tools/` (git-ignored). `scripts/toolchain.py` (`make toolchain`) searches
`<repo>/.tools/bin`, then `<repo>/.tools/dotnet`, then `PATH`.

| Tool | Status | Version | Location | How installed |
|---|---|---|---|---|
| dotnet | installed | SDK 10.0.112 (runtime 10.0.12) | `.tools/dotnet/dotnet`, symlink `.tools/bin/dotnet` | Ubuntu 24.04 `noble-updates` debs (`dotnet-sdk-10.0` and deps) fetched with `apt-get install --download-only` and unpacked with `dpkg -x` into `.tools/dotnet` (no system install) |
| terraform | OpenTofu substitute | OpenTofu v1.13.1 | `.tools/bin/tofu`, symlink `.tools/bin/terraform` | GitHub release `opentofu/opentofu` `tofu_1.13.1_linux_amd64.zip` |
| terragrunt | installed | v1.1.6 | `.tools/bin/terragrunt` | GitHub release `gruntwork-io/terragrunt` `releases/latest/download/terragrunt_linux_amd64` |
| docker | CLI only | 29.4.3 | `/usr/bin/docker` | pre-installed; no daemon running in this container, so `docker compose up` cannot run here |
| node | installed | v22.22.2 | `/opt/node22/bin/node` | pre-installed |
| az | not installed | - | - | not attempted (not required by the brief); `detect()` reports it absent |

## Refused or unavailable (egress proxy returned 403 on CONNECT)

- `dot.net` (dotnet-install.sh), `builds.dotnet.microsoft.com`, `dotnetcli.azureedge.net`,
  `dotnetcli.blob.core.windows.net`, `aka.ms`, `ci.dot.net`: the dotnet-install route failed.
- `mcr.microsoft.com`, `registry-1.docker.io`, `ghcr.io`: image pulls are refused; independently, no Docker
  daemon is running here, so the container-wrapper fallback was not viable.
- `releases.hashicorp.com`: Terraform 1.9.8 zip refused, so OpenTofu was used (the validators accept either).
- `api.github.com` and `github.com/<org>/<repo>/releases` HTML pages are refused; direct
  `releases/download/...` and `releases/latest/download/...` asset URLs and `git ls-remote` work.
  OpenTofu's latest version was found with `git ls-remote --tags`.
- `api.nuget.org` and `packages.microsoft.com`: NuGet restore of any third-party package fails
  (`dotnet add package Newtonsoft.Json` -> "Unable to load the service index"). A bare
  `dotnet new console` + `dotnet build` + `dotnet run` works because the targeting packs ship in the SDK debs.
  Generated .NET projects that need NuGet packages cannot be build-verified here without a NuGet mirror
  (offline `~/.nuget` cache or an allow-listed feed).

## Environment notes

- Export `DOTNET_CLI_TELEMETRY_OPTOUT=1` and `DOTNET_NOLOGO=1`; prepend `.tools/bin` to `PATH` to use the local tools.
- Side effect: `apt-get update` was run and `liblttng-ust*` runtime libs were installed system-wide
  (dependency of the .NET runtime); nothing else was installed outside the repo.

## Database for tests

This container runs a native Postgres 16 with pgvector 0.6.0 (installed from Ubuntu packages) at
`localhost:5432`, user `exiqo` / password `exiqo`, with databases `exiqo` and `exiqo_test`. Tests read
`TEST_DATABASE_URL` (default `postgresql://exiqo:exiqo@localhost:5432/exiqo_test`). Docker Compose is
for developer machines; there is no Docker daemon here.

## NuGet

`api.nuget.org` is refused here and on the device shell, so generated .NET code can only be
restore-verified on a developer machine or with an offline package cache.

## Acceptance run and e2e in this container

- `make demo-check` probes NuGet (or `.tools/nuget-cache`) and the provider registry first. Both
  are refused here, so it runs in offline mode: it asserts the failed restore with the NuGet reason
  and the `make nuget-cache` remedy, build and test skipped, G4 closed, the service not running,
  verify refused, no parity claimed and the registry reported unreachable. Full mode (every test
  green, infrastructure validated, parity 100% except intended fields) needs a machine that
  reaches both; see `docs/DEMO.md`, "What this container could not verify".
- Other sessions may leave servers on 9000 and 3000. `make api` and `make web` refuse a busy port
  and name the process holding it, rather than failing with `[Errno 48]` or `EADDRINUSE`; a web dev
  server left from an earlier session is worth stopping, because it keeps calling the API URL it was
  started with. `make demo-check API_PORT=<port>` starts its own API on that port when nothing
  answers there, and `make e2e` picks two free ports and never reuses a server it did not start.
- A production `next start` from `web/.next` and a `next dev` in the same checkout share the
  build folder. To run `make e2e` beside such a server, set `NEXT_DIST_DIR=.next-e2e`; Next then
  rewrites `web/next-env.d.ts` and adds the folder to `web/tsconfig.json`, so restore those two
  files afterwards (`git restore web/next-env.d.ts web/tsconfig.json`).
