"""``SourceLanguage`` for C#: delegates to ``engine/agents/builder.py`` and ``test_engineer.py``.

The agent modules are imported when a check runs, not at import time, so the agents can import
the stack registry without a cycle.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.stacks.base import ForbiddenPattern


class DotnetLanguage:
    comment_prefix = "//"

    @property
    def forbidden(self) -> tuple[ForbiddenPattern, ...]:
        from engine.agents.builder import CSHARP_FORBIDDEN
        return CSHARP_FORBIDDEN

    @property
    def placeholder(self) -> re.Pattern[str]:
        from engine.agents.builder import PLACEHOLDER
        return PLACEHOLDER

    def test_placeholder(self, test_id: str) -> str:
        from engine.dotnet_gen.generator import cs_string
        return f"Assert.Fail({cs_string(f'no test body for {test_id}')})"

    def strip_comments(self, text: str) -> str:
        from engine.agents.builder import strip_comments
        return strip_comments(text)

    def code_only(self, text: str) -> str:
        from engine.agents.builder import code_only
        return code_only(text)

    def declarations(self, text: str) -> list[str]:
        from engine.agents.builder import declarations
        return declarations(text)

    def tibco_comments(self, text: str) -> list[str]:
        from engine.agents.builder import tibco_comments
        return tibco_comments(text)

    def is_test_path(self, path: str) -> bool:
        from engine.agents.builder import is_test_path
        return is_test_path(path)

    def check_test_body(self, body: str, stem: str, kind: str = "rule",
                        json_kinds: dict[str, str] | None = None) -> str | None:
        from engine.agents.test_engineer import check_test_body
        return check_test_body(body, stem, json_kinds, kind=kind)
