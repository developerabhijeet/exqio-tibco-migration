"""Test helper: take a stack out of the registry, as it was before it landed."""
from __future__ import annotations

from engine import stacks


def unimplement(monkeypatch, key: str) -> None:
    """``key`` becomes a known stack that is not implemented yet (listed with that reason)."""
    stack = stacks.get_stack(key)
    monkeypatch.delitem(stacks._FACTORIES, key)
    monkeypatch.setitem(stacks._PENDING, key, (stack.label, tuple(stack.required_tools)))
