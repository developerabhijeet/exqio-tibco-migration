"""``DotnetNaming``: the .NET identifiers and paths of a design (target-stacks spec section 2).

Exactly today's names: the pattern templates are ``PatternSpec``'s ``.cs`` templates (this is the
only reader of those fields), projects are ``<App>.Api``, ``<App>.Core``, ``<App>.Infrastructure``
and ``<App>.Tests``, methods keep the activity name with an ``Async`` suffix (``LookupDealerAsync``),
contracts go to ``Contracts/<Module>/<Stem>.cs`` in namespace ``<App>.Core.Contracts.<Module>``.
"""
from __future__ import annotations

from engine.dotnet_gen.patterns import PATTERNS
from engine.stacks.contracts import ident, pascal

CONFIG_ROOT = "Gmf"
_PROJECT_SUFFIX = {"api": "Api", "core": "Core", "infrastructure": "Infrastructure", "tests": "Tests"}


class DotnetNaming:
    def type_name(self, text: str) -> str:
        """PascalCase type name (``dealer funding-request`` -> ``DealerFundingRequest``)."""
        return ident(text)

    def method_name(self, prefix: str, text: str) -> str:
        """``prefix`` and ``text`` as one PascalCase method with the async suffix
        (``("", "Lookup Dealer")`` -> ``LookupDealerAsync``, ``("Execute", "")`` -> ``ExecuteAsync``)."""
        core = pascal(prefix) + pascal(text) or "Execute"
        return f"{'Step' if core[0].isdigit() else ''}{core}Async"

    def pattern_paths(self, pattern: str) -> tuple[str, str, str]:
        """(file, class, method) templates of ``pattern`` over ``patterns.TEMPLATE_KEYS``; the file
        is relative to the project folder. ``KeyError`` for an unknown pattern."""
        spec = PATTERNS[pattern]
        return spec.file_template, spec.class_template, spec.method_template

    def layer_folder(self, kind: str, app: str) -> str:
        """The project of a layer kind (``api`` -> ``GmfCredit.Api``)."""
        return f"{app}.{_PROJECT_SUFFIX[kind]}"

    def contract_target(self, app: str, module: str, stem: str) -> tuple[str, str]:
        """(namespace, file in the Core project) of a contract of ``module`` ("" for none)."""
        if not module:
            return f"{app}.Core.Contracts", f"Contracts/{ident(stem)}.cs"
        mod = ident(module, "Module")
        return f"{app}.Core.Contracts.{mod}", f"Contracts/{mod}/{ident(stem)}.cs"

    def config_key(self, module: str, name: str) -> str:
        """``Gmf:<Module>:<Name>``; ``module`` and ``name`` are already identifiers."""
        return f"{CONFIG_ROOT}:{module}:{name}"
