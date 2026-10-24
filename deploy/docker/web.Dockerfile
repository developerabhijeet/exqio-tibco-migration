# syntax=docker/dockerfile:1.10
# exiqo-web: the Next.js UI, built with output "standalone" and served by `node server.js` on 3000.
#
# Build (context: the repo root; .dockerignore is an allowlist):
#   docker build -f deploy/docker/web.Dockerfile -t exiqo-web:<tag> .
#
# The bundle calls the API on its own origin: NEXT_PUBLIC_SAME_ORIGIN=1 makes the API base "/api"
# (web/scripts/root-env.ts), and the ingress routes /api to the API without rewriting the path.
# Fonts (@fontsource) and the Monaco editor (copied into public/ by scripts/copy-monaco.mjs) are
# bundled, so the browser loads nothing from a CDN.
#
# Base images are build arguments pinned by digest (multi-arch indexes). The defaults are the
# public upstream images; GM Financial's pipeline overrides them with its ACR mirror and sets
# NPM_CONFIG_REGISTRY to its npm mirror. The runtime is distroless: no shell, no package manager.

# node:22-bookworm-slim, resolved 2026-10-24
ARG BASE_IMAGE_NODE_BUILD=node:22-bookworm-slim@sha256:de170b39439eb0e5a3444ad7d60b015614a381e10fecae2e30ec9e735122f38d
# gcr.io/distroless/nodejs22-debian12:nonroot, resolved 2026-10-24
ARG BASE_IMAGE_NODE_RUNTIME=gcr.io/distroless/nodejs22-debian12:nonroot@sha256:13593b7570658e8477de39e2f4a1dd25db2f836d68a0ba771251572d23bb4f8e

# ----------------------------------------------------------------------------------------- build
# The tag and digest come from the build argument (DL3006 cannot see through it).
# hadolint ignore=DL3006
FROM ${BASE_IMAGE_NODE_BUILD} AS build

ARG NPM_CONFIG_REGISTRY=https://registry.npmjs.org/
ENV NPM_CONFIG_REGISTRY=${NPM_CONFIG_REGISTRY} \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false \
    NPM_CONFIG_AUDIT=false \
    NEXT_TELEMETRY_DISABLED=1 \
    NEXT_PUBLIC_SAME_ORIGIN=1

WORKDIR /app
# The lockfile and the postinstall script first, so the dependency layer survives source edits.
COPY web/package.json web/package-lock.json ./
COPY web/scripts/copy-monaco.mjs ./scripts/copy-monaco.mjs
RUN npm ci

COPY web/ ./
# The Next cache directory is pointed at /tmp: the runtime root filesystem is read-only, and
# /tmp is the only writable path the pod has.
RUN npm run build \
 && rm -rf .next/standalone/.next/cache \
 && ln -s /tmp/next-cache .next/standalone/.next/cache

# --------------------------------------------------------------------------------------- runtime
# hadolint ignore=DL3006
FROM ${BASE_IMAGE_NODE_RUNTIME} AS final

ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="exiqo-web" \
      org.opencontainers.image.description="ExiQO Migrate web UI" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000

WORKDIR /app
# server.js and the traced node_modules, then the static assets the standalone output leaves out.
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public

USER 10001:10001
EXPOSE 3000
# The distroless entrypoint is node itself.
CMD ["server.js"]
