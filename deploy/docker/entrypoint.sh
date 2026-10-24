#!/bin/sh
# exiqo-platform entrypoint: one image, three processes, chosen by EXIQO_ROLE.
#
#   api      the HTTP API on 8000 (the ingress sends /api here; API_BASE_PATH=/api in the cluster)
#   worker   the job worker; its cluster-internal listener is on INTERNAL_PORT (9100)
#   migrate  Alembic to head, then exit (the chart's pre-install/pre-upgrade hook Job)
#
# Anything else exits 64 (EX_USAGE) so a typo in a values file fails the pod at once instead of
# starting the wrong process. EXIQO_ROLE=all is local development only and is not offered here.
#
# The process is exec'd under tini: tini is PID 1, forwards SIGTERM to the process (which drains:
# the worker gives running jobs JOB_DRAIN_SECONDS) and reaps the orphans that dotnet, MSBuild and
# terraform leave behind, which a Python PID 1 would let pile up as zombies. tini signals the
# direct child only (no -g), so a drain is never cut short by killing the build it is waiting for.
set -eu

cd /app

case "${EXIQO_ROLE:-api}" in
  api)
    # TLS ends at the ingress; X-Forwarded-* is trusted from any peer because the NetworkPolicy
    # admits only the ingress controller (and the web pods) to port 8000.
    exec /usr/bin/tini -- uvicorn api.app.main:app --host 0.0.0.0 --port 8000 \
      --proxy-headers --forwarded-allow-ips='*' --no-server-header
    ;;
  worker)
    exec /usr/bin/tini -- python -m api.app.worker
    ;;
  migrate)
    # Settings knows the process roles only (api, worker, all); Alembic reads DATABASE_URL through
    # them, so it runs as role api, which starts nothing by itself.
    EXIQO_ROLE=api
    export EXIQO_ROLE
    exec /usr/bin/tini -- alembic -c api/alembic.ini upgrade head
    ;;
  *)
    echo "entrypoint: EXIQO_ROLE must be api, worker or migrate (got '${EXIQO_ROLE:-}')" >&2
    exit 64
    ;;
esac
