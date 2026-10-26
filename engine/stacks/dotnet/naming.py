"""``DotnetNaming``: the .NET identifiers and paths of a design (target-stacks spec section 2).

Exactly today's names: the pattern templates are ``PatternSpec``'s ``.cs`` templates (this is the
only reader of those fields), projects are ``<App>.Api``, ``<App>.Core``, ``<App>.Infrastructure``
and ``<App>.Tests``, methods keep the activity name with an ``Async`` suffix (``LookupDealerAsync``),
contracts go to ``Contracts/<Module>/<Stem>.cs`` in namespace ``<App>.Core.Contracts.<Module>``,
configuration keys are ``Gmf:<Module>:<Name>`` and connection strings ``ConnectionStrings:<Name>``.
Every member takes raw words (``StackNaming``) and cases them here.
"""
from __future__ import annotations

from engine.dotnet_gen.patterns import PATTERNS
from engine.stacks.contracts import ident, pascal

CONFIG_ROOT = "Gmf"
_PROJECT_SUFFIX = {"api": "Api", "core": "Core", "infrastructure": "Infrastructure", "tests": "Tests"}
_JMS_TYPES = frozenset({"jms.receive", "jms.send", "jms.publish", "jms.reply", "jms.request_reply"})
_JDBC_TYPES = frozenset({"jdbc.query", "jdbc.update", "jdbc.call"})
# C# keywords (all lower case, so a PascalCase identifier never meets one).
CSHARP_KEYWORDS = frozenset({
    "abstract", "as", "base", "bool", "break", "byte", "case", "catch", "char", "checked", "class",
    "const", "continue", "decimal", "default", "delegate", "do", "double", "else", "enum", "event",
    "explicit", "extern", "false", "finally", "fixed", "float", "for", "foreach", "goto", "if",
    "implicit", "in", "int", "interface", "internal", "is", "lock", "long", "namespace", "new", "null",
    "object", "operator", "out", "override", "params", "private", "protected", "public", "readonly",
    "ref", "return", "sbyte", "sealed", "short", "sizeof", "stackalloc", "static", "string", "struct",
    "switch", "this", "throw", "true", "try", "typeof", "uint", "ulong", "unchecked", "unsafe",
    "ushort", "using", "virtual", "void", "volatile", "while",
})


class DotnetNaming:
    platform = ".NET"
    config_file = "appsettings.json"
    messaging_actor = "ServiceBus"
    reserved = CSHARP_KEYWORDS

    def escape(self, name: str) -> str:
        """A C# keyword as a verbatim identifier (``class`` -> ``@class``); anything else unchanged."""
        return f"@{name}" if name in self.reserved else name

    def type_name(self, words: str, fallback: str = "Value") -> str:
        """PascalCase type name (``dealer funding-request`` -> ``DealerFundingRequest``)."""
        return self.escape(ident(words, fallback))

    def method_name(self, prefix: str, words: str) -> str:
        """``prefix`` and ``words`` as one PascalCase method with the async suffix
        (``("", "Lookup Dealer")`` -> ``LookupDealerAsync``, ``("Execute", "")`` -> ``ExecuteAsync``)."""
        core = pascal(prefix) + pascal(words) or "Execute"
        return f"{'Step' if core[0].isdigit() else ''}{core}Async"

    def file_name(self, words: str) -> str:
        """``LookupDealer.cs``: one file per type, named for it."""
        return f"{ident(words)}.cs"

    def exception_name(self, words: str) -> str:
        """The exception of a fault: ``DealerInactiveFault`` -> ``DealerInactiveFaultException``, so it
        never clashes with the fault's contract record; a name already ending so is kept."""
        fault = ident(words, "Step")
        return fault if fault.endswith("Exception") else f"{fault}Exception"

    def config_key(self, module: str, *names: str) -> str:
        """``Gmf:<Module>:<Name>`` (``("dealerfunding", "DB_URL")`` -> ``Gmf:Dealerfunding:DbUrl``).

        One name is a substitution variable; two are a resource and one of its settings
        (``Gmf:<Module>:<Resource>:<Setting>``)."""
        fallbacks = ("Var",) if len(names) == 1 else ("Resource", "Setting", *(("Value",) * len(names)))
        parts = [ident(n, f) for n, f in zip(names, fallbacks, strict=False)]
        return ":".join([CONFIG_ROOT, ident(module, "Module"), *parts])

    def connection_key(self, resource: str) -> str:
        """``ConnectionStrings:<Resource>``, the section ``IConfiguration.GetConnectionString`` reads."""
        return f"ConnectionStrings:{ident(resource, 'Resource')}"

    def pattern_paths(self, pattern: str) -> tuple[str, str, str]:
        """(file, class, method) templates of ``pattern`` over ``patterns.TEMPLATE_KEYS``; the file
        is relative to the project folder. ``KeyError`` for an unknown pattern."""
        spec = PATTERNS[pattern]
        return spec.file_template, spec.class_template, spec.method_template

    def layer_folder(self, kind: str, app: str) -> str:
        """The project of a layer kind (``api`` -> ``GmfCredit.Api``); ``app`` is the type name."""
        return f"{app}.{_PROJECT_SUFFIX[kind]}"

    def contract_target(self, app: str, module: str, stem: str) -> tuple[str, str]:
        """(namespace, file in the Core project) of a contract of ``module`` ("" for none)."""
        if not module:
            return f"{app}.Core.Contracts", f"Contracts/{ident(stem)}.cs"
        mod = ident(module, "Module")
        return f"{app}.Core.Contracts.{mod}", f"Contracts/{mod}/{ident(stem)}.cs"

    def layout_scaffold(self, projects: dict[str, str], process: str,
                        patterns: set[str]) -> dict[str, set[str]]:
        """The files the .NET generator writes next to the mapped ones (project files, host, tests),
        by project kind. ``projects`` maps kind to project name, ``process`` is the type name."""
        files: dict[str, set[str]] = {kind: {f"{name}.csproj"} for kind, name in projects.items()}
        files["api"] |= {"Program.cs", "appsettings.json"}
        files["core"] |= {"Contracts/TibcoPathAttribute.cs", f"Services/{process}Service.cs"}
        files["tests"] |= {f"{process}RulesTests.cs", f"{process}BranchTests.cs", f"{process}ScenarioTests.cs"}
        if patterns & _JMS_TYPES:
            files["core"].add("Messaging/IMessageBus.cs")
            files["infrastructure"] |= {"Messaging/ServiceBusMessageBus.cs", "Messaging/InMemoryMessageBus.cs"}
        if "throw" in patterns:
            files["api"].add("Errors/FaultExceptionHandler.cs")
        if patterns & _JDBC_TYPES:
            files["tests"].add("TestDatabase.cs")
        return files
