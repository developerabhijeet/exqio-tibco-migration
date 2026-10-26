"""``JavaNaming``: the Java identifiers and paths of a design (target-stacks spec section 2, ruling X16).

One Maven module, package ``com.gmf.<app lower alnum>`` (``gmf.credit`` -> ``com.gmf.gmfcredit``).
Layer folders are the spec's: ``api``, ``core`` and ``infrastructure`` are sub-packages under
``src/main/java/com/gmf/<app>/``, ``tests`` is ``src/test/java/com/gmf/<app>``. Types are PascalCase,
methods lowerCamel (``Lookup Dealer`` -> ``lookupDealer``), Java keywords are escaped with a
trailing underscore (``new`` -> ``new_``).

``pattern_paths`` gives every pattern's (file, class, method) template relative to the package root,
the file always under the folder of the pattern's project kind (``core/{process}Service.java``);
``row_names`` fills it and returns the file relative to that layer folder, which is what a
``MappingRow.file`` is. The JMS bus port sits in ``core/messaging`` (``PatternSpec`` puts the
``jms.*`` rows in Core, and Core must not depend on Infrastructure); its implementations are in
``infrastructure/messaging``.

Configuration keys are Spring's canonical form of the runtime contract's environment names:
``Gmf__Dealerfunding__DbUrl`` binds to ``gmf.dealerfunding.db-url`` through the generated
``EnvBinding``, which kebab-cases each ``__`` segment with ``kebab`` below (the same rule in Java).
"""
from __future__ import annotations

import re

from engine.dotnet_gen.patterns import JDBC_PATTERNS, PATTERNS
from engine.stacks.contracts import ident, pascal

CONFIG_ROOT = "gmf"
# Java keywords and literals (JLS 3.9); contextual keywords (var, record, yield, ...) are legal names.
JAVA_KEYWORDS = frozenset({
    "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char", "class", "const",
    "continue", "default", "do", "double", "else", "enum", "extends", "final", "finally", "float", "for",
    "goto", "if", "implements", "import", "instanceof", "int", "interface", "long", "native", "new",
    "package", "private", "protected", "public", "return", "short", "static", "strictfp", "super",
    "switch", "synchronized", "this", "throw", "throws", "transient", "try", "void", "volatile", "while",
    "true", "false", "null", "_",
})
_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")

# (file under the package root, class, method) per pattern, over the cased values ``row_names`` makes.
_SERVICE = ("core/{process}Service.java", "{process}Service")
_CONTROLLER = ("api/{process}Controller.java", "{process}Controller")
_REPOSITORY = ("infrastructure/{process}Repository.java", "{process}Repository")
_MAPPINGS = ("core/{process}Mappings.java", "{process}Mappings")
_BUS = ("core/messaging/MessageBus.java", "MessageBus")
_SERIAL = ("core/{process}Serialization.java", "{process}Serialization")
_FILES = ("infrastructure/{process}FileStore.java", "{process}FileStore")
_CLIENT = ("infrastructure/clients/{resource}Client.java", "{resource}Client", "{activity_m}")
_STEP = (*_SERVICE, "{activity_m}")
_ENTRY = (*_SERVICE, "execute")
_TEMPLATES: dict[str, tuple[str, str, str]] = {
    "rest.receive": (*_CONTROLLER, "{activity_m}"),
    "http.receive": (*_CONTROLLER, "{activity_m}"),
    "soap.receive": ("api/{process}SoapService.java", "{process}SoapService", "{activity_m}"),
    "jms.receive": ("infrastructure/messaging/{process}Consumer.java", "{process}Consumer", "handle"),
    "timer.start": ("infrastructure/jobs/{process}Job.java", "{process}Job", "execute"),
    "jdbc.query": (*_REPOSITORY, "{activity_m}"),
    "jdbc.update": (*_REPOSITORY, "{activity_m}"),
    "jdbc.call": (*_REPOSITORY, "{activity_m}"),
    "http.invoke": _CLIENT,
    "rest.invoke": _CLIENT,
    "soap.invoke": _CLIENT,
    "jms.send": (*_BUS, "publish"),
    "jms.publish": (*_BUS, "publish"),
    "jms.reply": (*_BUS, "reply"),
    "jms.request_reply": (*_BUS, "request"),
    "call.process": ("core/{target}Service.java", "{target}Service", "execute"),
    "mapper": (*_MAPPINGS, "{mapping_m}"),
    "assign": (*_MAPPINGS, "{mapping_m}"),
    "input.mapping": (*_MAPPINGS, "{mapping_m}"),
    "throw": ("core/exceptions/{exception}.java", "{exception}", "{exception}"),
    "catch": _ENTRY,
    "reply": (*_CONTROLLER, "{starter_m}"),
    "end": _ENTRY,
    "log": _ENTRY,
    "noop": _ENTRY,
    "wait": _ENTRY,
    "checkpoint": _ENTRY,
    "confirm": _ENTRY,
    "engine.command": _STEP,
    "group.loop": _STEP,
    "group.iterate": _STEP,
    "group.transaction": _STEP,
    "group.critical": _STEP,
    "flow.parallel": _STEP,
    "java.invoke": ("core/interop/{activity}.java", "{activity}", "invoke"),
    "xslt.transform": ("core/{process}Transforms.java", "{process}Transforms", "{transform_m}"),
    "json.parse": (*_SERIAL, "{activity_m}"),
    "json.render": (*_SERIAL, "{activity_m}"),
    "xml.parse": (*_SERIAL, "{activity_m}"),
    "xml.render": (*_SERIAL, "{activity_m}"),
    "shared.get": ("infrastructure/state/SharedVariables.java", "SharedVariables", "{get_m}"),
    "shared.set": ("infrastructure/state/SharedVariables.java", "SharedVariables", "{set_m}"),
    "file.read": (*_FILES, "{activity_m}"),
    "file.write": (*_FILES, "{activity_m}"),
    "file.list": (*_FILES, "{activity_m}"),
    "ftp.get": ("infrastructure/files/FtpGateway.java", "FtpGateway", "{activity_m}"),
    "ftp.put": ("infrastructure/files/FtpGateway.java", "FtpGateway", "{activity_m}"),
    "mail.send": ("infrastructure/messaging/MailSender.java", "MailSender", "{activity_m}"),
    "mail.receive": ("infrastructure/messaging/{process}MailPoller.java", "{process}MailPoller", "execute"),
    "cmd.exec": ("infrastructure/interop/CommandRunner.java", "CommandRunner", "{activity_m}"),
    "unknown": _STEP,
}


def kebab(identifier: str) -> str:
    """Spring's canonical segment for an identifier: ``GMFFundingDB`` -> ``gmf-funding-db``,
    ``DbUrl`` -> ``db-url``. ``EnvBinding.kebab`` in the generated code is the same rule."""
    return "-".join(w.lower() for w in _WORD.findall(identifier)) or identifier.lower()


def package_name(app: str) -> str:
    """``com.gmf.<app lower alnum>`` for the application's type name or id."""
    return "com.gmf." + (re.sub(r"[^a-z0-9]", "", app.lower()) or "app")


def lower_camel(name: str) -> str:
    """``LookupDealer`` -> ``lookupDealer``, ``GMFFundingDB`` -> ``gmfFundingDB``, ``URL`` -> ``url``."""
    m = re.match(r"[A-Z]+", name)
    if not m:
        return name
    run = m.group(0)
    if len(run) == len(name) or len(run) == 1:
        return name[: len(run)].lower() + name[len(run):]
    return run[:-1].lower() + name[len(run) - 1:]  # keep the capital that starts the next word


class JavaNaming:
    platform = "Java"
    config_file = "application.yml"
    messaging_actor = "ServiceBus"
    reserved = JAVA_KEYWORDS

    def escape(self, name: str) -> str:
        """A Java keyword with a trailing underscore (``new`` -> ``new_``); anything else unchanged."""
        return f"{name}_" if name in self.reserved else name

    def type_name(self, words: str, fallback: str = "Value") -> str:
        """PascalCase type name (``dealer funding-request`` -> ``DealerFundingRequest``)."""
        return self.escape(ident(words, fallback))

    def method_name(self, prefix: str, words: str) -> str:
        """``prefix`` and ``words`` as one lowerCamel method (``("", "Lookup Dealer")`` ->
        ``lookupDealer``, ``("Execute", "")`` -> ``execute``, ``("Handle", "funding event")`` ->
        ``handleFundingEvent``)."""
        core = pascal(prefix) + pascal(words) or "Execute"
        if core[0].isdigit():
            core = f"Step{core}"
        return self.escape(lower_camel(core))

    def file_name(self, words: str) -> str:
        """``LookupDealer.java``: one public type per file, named for it."""
        return f"{ident(words)}.java"

    def exception_name(self, words: str) -> str:
        """The exception of a fault: ``DealerInactiveFault`` -> ``DealerInactiveFaultException``."""
        fault = ident(words, "Step")
        return fault if fault.endswith("Exception") else f"{fault}Exception"

    def config_key(self, module: str, *names: str) -> str:
        """``gmf.<module>.<name>`` in Spring's canonical form (``("dealerfunding", "DB_URL")`` ->
        ``gmf.dealerfunding.db-url``): what ``Gmf__<Module>__<Name>`` binds to. One name is a
        substitution variable; two are a resource and one of its settings."""
        if len(names) == 1:
            parts = [ident(names[0], "Var")]
        elif len(names) == 2:
            parts = [ident(names[0], "Resource"), ident(names[1], "Setting")]
        else:
            raise ValueError(f"config_key takes a setting or a resource and its setting, not {names!r}")
        return ".".join([CONFIG_ROOT, kebab(ident(module, "Module")), *(kebab(p) for p in parts)])

    def connection_key(self, resource: str) -> str:
        """``connection-strings.<resource>``: what ``ConnectionStrings__<Resource>`` binds to."""
        return f"connection-strings.{kebab(ident(resource, 'Resource'))}"

    def pattern_paths(self, pattern: str) -> tuple[str, str, str]:
        """(file under the package root, class, method) templates of ``pattern``; ``KeyError`` for an
        unknown pattern. The file starts with the folder of ``PATTERNS[pattern].project``."""
        PATTERNS[pattern]  # KeyError for a pattern the catalogue does not have
        return _TEMPLATES[pattern]

    def row_names(self, pattern: str, words: dict[str, str]) -> tuple[str, str, str]:
        """(file relative to the row's layer folder, class, method) of a design row from raw words."""
        file_t, class_t, method_t = self.pattern_paths(pattern)
        resource = ident(words["resource"], "Step")
        if resource.endswith("Client") and len(resource) > len("Client"):
            resource = resource[: -len("Client")]
        activity = words["activity"]
        values = {
            "process": ident(words["process"], "Process"), "activity": ident(activity, "Step"),
            "resource": resource, "exception": self.exception_name(words["fault"]),
            "target": ident(words["target"], "Step") if words["target"] else "Step",
            "activity_m": self.method_name("", activity), "starter_m": self.method_name("", words["starter"]),
            "mapping_m": self.method_name("", f"{activity} Mapping"),
            "transform_m": self.method_name("", f"{activity} Transform"),
            "get_m": self.method_name("get", activity), "set_m": self.method_name("set", activity),
        }
        layer = PATTERNS[pattern].project
        file = file_t.format(**values)
        return file[len(layer) + 1:], class_t.format(**values), method_t.format(**values)

    def layer_folder(self, kind: str, app: str) -> str:
        """The folder of a layer kind (``core`` -> ``src/main/java/com/gmf/gmfcredit/core``)."""
        root = package_name(app).replace(".", "/")
        if kind == "tests":
            return f"src/test/java/{root}"
        if kind not in ("api", "core", "infrastructure"):
            raise KeyError(kind)
        return f"src/main/java/{root}/{kind}"

    def contract_target(self, app: str, module: str, stem: str) -> tuple[str, str]:
        """(package, file in the core folder) of a contract: one holder class per contract,
        ``<Stem>Contract``, with its records nested, since Java allows one public type per file
        and a record is usually named like its contract."""
        base = f"{package_name(app)}.core.contracts"
        holder = f"{ident(stem)}Contract.java"
        mod = re.sub(r"[^a-z0-9]", "", module.lower())
        if not mod:
            return base, f"contracts/{holder}"
        return f"{base}.{mod}", f"contracts/{mod}/{holder}"

    def layout_scaffold(self, projects: dict[str, str], process: str,
                        patterns: set[str]) -> dict[str, set[str]]:
        """The files the Java generator writes next to the mapped ones, by layer kind (relative to
        the layer folder); ``process`` is the type name."""
        files: dict[str, set[str]] = {
            "api": {"SecurityConfig.java", "FaultHandler.java", "DevTokenController.java", "BusDevController.java"},
            "core": {f"{process}Service.java", "contracts/TibcoPath.java", "contracts/Masking.java",
                     "exceptions/ProcessFault.java", "messaging/MessageBus.java", "tibco/TibValue.java",
                     "tibco/TibText.java", "tibco/ProcessState.java", "tibco/ProcessFlow.java",
                     "tibco/ProcessResult.java"},
            "infrastructure": {"messaging/InMemoryMessageBus.java", "messaging/ServiceBusMessageBus.java"},
            "tests": {f"{process}RulesTests.java", f"{process}BranchTests.java", f"{process}ScenarioTests.java",
                      "support/TestDatabase.java", "support/Fixtures.java", "support/ScenarioSupport.java"},
        }
        if patterns & JDBC_PATTERNS:
            files["infrastructure"].add("DataSources.java")
        return files
