"""SaxonC-HE is a GraalVM native image: its isolate must be created on the main thread.

A PySaxonProcessor first created on a worker thread (the verify job runs the evaluator under
asyncio.to_thread) makes the interpreter die at exit with a GraalVM "StackOverflowError:
Enabling the yellow zone..." fatal error and exit code 99, after every test has passed.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytest.importorskip("saxonche")

REPO = Path(__file__).resolve().parents[2]

_SCRIPT = textwrap.dedent(
    """
    import threading
    from engine.parity import xslt
    from engine.parity.xpath import XPathContext
    {warm}
    SHEET = ('<xsl:stylesheet version="3.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
             '<xsl:template match="/"><r><xsl:value-of select="1+1"/></r></xsl:template>'
             '</xsl:stylesheet>')
    out = {{}}
    t = threading.Thread(target=lambda: out.setdefault("r", xslt.transform(SHEET, XPathContext({{}}, {{}}))))
    t.start(); t.join()
    assert out["r"].text == "2", out
    print("ok")
    """
)


def _run(warm: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-c", _SCRIPT.format(warm=warm)], cwd=REPO,
                          capture_output=True, text=True, timeout=120, check=False)


def test_transform_on_a_worker_thread_exits_cleanly_after_warm_up():
    proc = _run("xslt.warm_up()")
    assert proc.returncode == 0, proc.stderr[:600]
    assert "ok" in proc.stdout


def test_warm_up_off_the_main_thread_does_not_create_the_isolate():
    # A fresh interpreter: earlier tests in this process (any parity evaluation, or warm_up itself)
    # have already created the shared processor, after which warm_up rightly returns True anywhere.
    script = textwrap.dedent(
        """
        import threading
        from engine.parity import xslt
        out = {}
        t = threading.Thread(target=lambda: out.setdefault("r", xslt.warm_up()))
        t.start(); t.join()
        print(out["r"], xslt._saxon_processor is None)
        """
    )
    proc = subprocess.run([sys.executable, "-c", script], cwd=REPO, capture_output=True, text=True,
                          timeout=120, check=False)
    assert proc.returncode == 0, proc.stderr[:600]
    assert proc.stdout.split() == ["False", "True"], proc.stdout
