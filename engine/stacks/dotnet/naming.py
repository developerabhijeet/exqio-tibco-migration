"""``DotnetNaming``: the .NET identifiers and paths of a design (target-stacks spec section 2).

Exactly today's names: the pattern templates are ``PatternSpec``'s ``.cs`` templates (this is the
only reader of those fields), projects are ``<App>.Api``, ``<App>.Core``, ``<App>.Infrastructure``
and ``<App>.Tests``, methods keep the activity name with an ``Async`` suffix (``LookupDealerAsync``),
contracts go to ``Contracts/<Module>/<Stem>.cs`` in namespace ``<App>.Core.Contracts.<Module>``,
configuration keys are ``Gmf:<Module>:<Name>`` and connection strings ``ConnectionStrings:<Name>``.
Every member takes raw words (``StackNaming``) and cases them here.
"""
from __future__ import annotations

from engine.dotnet_gen.patterns import JDBC_PATTERNS, JMS_PATTERNS, PATTERNS
from engine.stacks.contracts import ident, pascal

CONFIG_ROOT = "Gmf"
_PROJECT_SUFFIX = {"api": "Api", "core": "Core", "infrastructure": "Infrastructure", "tests": "Tests"}
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
        if len(names) == 1:
            parts = [ident(names[0], "Var")]
        elif len(names) == 2:
            parts = [ident(names[0], "Resource"), ident(names[1], "Setting")]
        else:
            raise ValueError(f"config_key takes a setting or a resource and its setting, not {names!r}")
        return ":".join([CONFIG_ROOT, ident(module, "Module"), *parts])

    def connection_key(self, resource: str) -> str:
        """``ConnectionStrings:<Resource>``, the section ``IConfiguration.GetConnectionString`` reads."""
        return f"ConnectionStrings:{ident(resource, 'Resource')}"

    def row_names(self, pattern: str, words: dict[str, str]) -> tuple[str, str, str]:
        """(file, class, method) of a row: the ``PatternSpec`` templates over the cased words.

        Each ``TEMPLATE_KEYS`` value is PascalCase (``Step`` prefixed to one that starts with a digit),
        ``resource`` loses a trailing ``Client``, ``exception`` is ``exception_name(fault)`` and
        ``destination`` stays verbatim."""
        resource = ident(words["resource"], "Step")
        if resource.endswith("Client") and len(resource) > len("Client"):
            resource = resource[: -len("Client")]
        values = {"app": ident(words["app"], "App"), "process": ident(words["process"], "Process"),
                  "activity": ident(words["activity"], "Step"), "starter": ident(words["starter"], "Step"),
                  "resource": resource, "fault": ident(words["fault"], "Step"),
                  "exception": self.exception_name(words["fault"]),
                  "target": ident(words["target"], "Step") if words["target"] else "",
                  "destination": words["destination"]}
        file_t, class_t, method_t = self.pattern_paths(pattern)
        return file_t.format(**values), class_t.format(**values), method_t.format(**values)

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
        if patterns & JMS_PATTERNS:
            files["core"].add("Messaging/IMessageBus.cs")
            files["infrastructure"] |= {"Messaging/ServiceBusMessageBus.cs", "Messaging/InMemoryMessageBus.cs"}
        if "throw" in patterns:
            files["api"].add("Errors/FaultExceptionHandler.cs")
        if patterns & JDBC_PATTERNS:
            files["tests"].add("TestDatabase.cs")
        return files
