"""Job kinds for tests that run ``python -m api.app.worker`` as a subprocess.

The entrypoint imports this module only when ``EXIQO_TEST_HANDLERS=1``, so these kinds never exist
in a production worker. Not collected by pytest (no ``test_`` prefix).
"""
from __future__ import annotations

import asyncio

from api.app.jobs.registry import job

SLOW_KIND = "slow-subprocess-test"


@job(SLOW_KIND)
async def slow(ctx, w):
    await ctx.stage("sleeping")
    await asyncio.sleep(600)
    return {}
