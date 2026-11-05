"""The deployment's configured sensitive fields (Settings.sensitive_fields) reach every process
that masks: the API (create_app) and the worker (worker.run), whose jobs project memory nodes."""
from __future__ import annotations

import asyncio

import pytest

from api.app import worker
from api.app.config import Settings
from engine import masking
from engine.memory.model import MemNode, mask_node


@pytest.fixture(autouse=True)
def _restore_masking(monkeypatch):
    monkeypatch.setattr(masking, "_EXTRA", ())


def test_the_worker_masks_with_the_configured_fields():
    node = MemNode(label="Chunk", uid="chunk:x", name="note", text="Pay out when dealer_pin=4242 matches.")
    assert "4242" in mask_node(node)[0].text  # not a default sensitive field
    settings = Settings(sensitive_fields=["dealer_pin"], internal_token="", _env_file=None)
    # Startup configures masking first; the missing token then stops it before any connection.
    with pytest.raises(RuntimeError, match="INTERNAL_TOKEN"):
        asyncio.run(worker.run(settings))
    masked = mask_node(node)[0]
    assert "4242" not in masked.text and "dealer_pin=***" in masked.text


def test_configured_fields_and_credential_patterns_apply_together():
    from api.app.services.citations import _cite_prose

    masking.configure(["dealer_pin"])
    raw = "Call with dealer_pin=4242 and password=hunter2 under Bearer eyJhbGciOi.payload.sig; ssn 123-45-6789."
    out = masking.mask(raw)
    for secret in ("4242", "hunter2", "eyJhbGciOi.payload.sig", "123-45-6789"):
        assert secret not in out
    cited = _cite_prose(raw, {})
    assert cited.text == out  # cited.text (and so every span offset) is the masked text
