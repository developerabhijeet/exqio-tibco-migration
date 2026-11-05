"""``GoNaming``: the Go identifiers and paths of a design (target-stacks spec section 2, ruling X16).

One Go module, path ``<app slug>`` (``gmf.credit`` -> ``gmf-credit``). Layer folders are the spec's:
``internal/api``, ``internal/core`` and ``internal/infrastructure`` (each a package of that name),
``tests`` (package ``tests``, black-box, inside the module root so it may import ``internal/``).
Types and methods are exported PascalCase (``Lookup Dealer`` -> ``LookupDealer``, no ``Async``);
file names are snake_case (``dealer_funding_request_service.go``); Go keywords are escaped with a
trailing underscore (``type`` -> ``type_``), which only a lower-case name can meet.

Go packages may not import each other in a cycle, so the folders are chosen by who calls whom:
the core services call ``internal/infrastructure`` (repositories, data sources, file stores) and
its leaf packages (``clients``, ``state``, ``files``, ``mail``, ``interop``); the packages that call
the services (``internal/api``, ``internal/infrastructure/messaging`` for the JMS consumers,
``internal/infrastructure/jobs`` for the timers) are never called by them. The JMS port the
services publish through is ``core.MessageBus`` (``message_bus.go``); the buses behind it are in
``internal/platform``.

Configuration keys are the runtime contract's environment names in upper snake case:
``Gmf__Dealerfunding__DbUrl`` is read as ``GMF_DEALERFUNDING_DB_URL`` (``platform.Config`` binds
every ``__``-separated name with ``snake`` below, the same rule in Go).
"""
from __future__ import annotations

import re

from engine.dotnet_gen.patterns import JDBC_PATTERNS, PATTERNS
from engine.stacks.contracts import ident, pascal

# The Go keywords (the spec's list); predeclared identifiers are lower case and never meet an
# exported name.
GO_KEYWORDS = frozenset({
    "break", "case", "chan", "const", "continue", "default", "defer", "else", "fallthrough", "for",
    "func", "go", "goto", "if", "import", "interface", "map", "package", "range", "return", "select",
    "struct", "switch", "type", "var",
})
_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")
# A file name ending in one of these after an underscore is a test file or carries a build
# constraint (``foo_test.go``, ``foo_windows.go``, ``foo_arm64.go``): such a stem gets ``_go``.
_SUFFIXES = frozenset({
    "test", "aix", "android", "darwin", "dragonfly", "freebsd", "hurd", "illumos", "ios", "js", "linux",
    "nacl", "netbsd", "openbsd", "plan9", "solaris", "wasip1", "windows", "zos", "386", "amd64", "amd64p32",
    "arm", "armbe", "arm64", "arm64be", "loong64", "mips", "mipsle", "mips64", "mips64le", "mips64p32",
    "mips64p32le", "ppc", "ppc64", "ppc64le", "riscv", "riscv64", "s390", "s390x", "sparc", "sparc64", "wasm",
})

# (file under internal/, class, method) per pattern, over the values ``row_names`` makes.
_SERVICE = ("core/{process_s}_service.go", "{process}Service")
_HANDLER = ("api/{process_s}_handler.go", "{process}Handler")
_REPOSITORY = ("infrastructure/{process_s}_repository.go", "{process}Repository")
_MAPPINGS = ("core/{process_s}_mappings.go", "{process}Mappings")
_BUS = ("core/message_bus.go", "MessageBus")
_SERIAL = ("core/{process_s}_serialization.go", "{process}Serialization")
_FILES = ("infrastructure/{process_s}_file_store.go", "{process}FileStore")
_CLIENT = ("infrastructure/clients/{resource_s}_client.go", "{resource}Client", "{activity_m}")
_STEP = (*_SERVICE, "{activity_m}")
_ENTRY = (*_SERVICE, "Execute")
_TEMPLATES: dict[str, tuple[str, str, str]] = {
    "rest.receive": (*_HANDLER, "{activity_m}"),
    "http.receive": (*_HANDLER, "{activity_m}"),
    "soap.receive": ("api/{process_s}_soap.go", "{process}SoapService", "{activity_m}"),
    "jms.receive": ("infrastructure/messaging/{process_s}_consumer.go", "{process}Consumer", "Handle"),
    "timer.start": ("infrastructure/jobs/{process_s}_job.go", "{process}Job", "Run"),
    "jdbc.query": (*_REPOSITORY, "{activity_m}"),
    "jdbc.update": (*_REPOSITORY, "{activity_m}"),
    "jdbc.call": (*_REPOSITORY, "{activity_m}"),
    "http.invoke": _CLIENT,
    "rest.invoke": _CLIENT,
    "soap.invoke": _CLIENT,
    "jms.send": (*_BUS, "Publish"),
    "jms.publish": (*_BUS, "Publish"),
    "jms.reply": (*_BUS, "Reply"),
    "jms.request_reply": (*_BUS, "Request"),
    "call.process": ("core/{target_s}_service.go", "{target}Service", "Execute"),
    "mapper": (*_MAPPINGS, "{mapping_m}"),
    "assign": (*_MAPPINGS, "{mapping_m}"),
    "input.mapping": (*_MAPPINGS, "{mapping_m}"),
    "throw": ("core/{exception_s}.go", "{exception}", "New{exception}"),
    "catch": _ENTRY,
    "reply": (*_HANDLER, "{starter_m}"),
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
    "java.invoke": ("core/interop/{activity_s}.go", "{activity}", "Invoke"),
    "xslt.transform": ("core/{process_s}_transforms.go", "{process}Transforms", "{transform_m}"),
    "json.parse": (*_SERIAL, "{activity_m}"),
    "json.render": (*_SERIAL, "{activity_m}"),
    "xml.parse": (*_SERIAL, "{activity_m}"),
    "xml.render": (*_SERIAL, "{activity_m}"),
    "shared.get": ("infrastructure/state/shared_variables.go", "SharedVariables", "{get_m}"),
    "shared.set": ("infrastructure/state/shared_variables.go", "SharedVariables", "{set_m}"),
    "file.read": (*_FILES, "{activity_m}"),
    "file.write": (*_FILES, "{activity_m}"),
    "file.list": (*_FILES, "{activity_m}"),
    "ftp.get": ("infrastructure/files/ftp_gateway.go", "FtpGateway", "{activity_m}"),
    "ftp.put": ("infrastructure/files/ftp_gateway.go", "FtpGateway", "{activity_m}"),
    "mail.send": ("infrastructure/mail/mail_sender.go", "MailSender", "{activity_m}"),
    "mail.receive": ("infrastructure/messaging/{process_s}_mail_poller.go", "{process}MailPoller", "Run"),
    "cmd.exec": ("infrastructure/interop/command_runner.go", "CommandRunner", "{activity_m}"),
    "unknown": _STEP,
}


def words_of(identifier: str) -> list[str]:
    """The words of an identifier: ``GMFFundingDB`` -> ``GMF``, ``Funding``, ``DB``."""
    return _WORD.findall(identifier)


def snake(identifier: str) -> str:
    """``DealerFundingRequest`` -> ``dealer_funding_request``, ``GMFFundingDB`` -> ``gmf_funding_db``."""
    return "_".join(w.lower() for w in words_of(identifier)) or identifier.lower()


def env_key(identifier: str) -> str:
    """One segment of a configuration key: ``DbUrl`` -> ``DB_URL``. ``platform.snake`` in the
    generated code is the same rule."""
    return snake(identifier).upper()


def go_file(stem: str) -> str:
    """``<stem>.go``, with ``_go`` added where the last word would make it a test or a
    platform-specific file (``lookup_test`` -> ``lookup_test_go.go``)."""
    last = stem.rsplit("_", 1)[-1] if "_" in stem else ""
    return f"{stem}_go.go" if last in _SUFFIXES else f"{stem}.go"


def package_name_of(folder: str) -> str:
    """The package a folder holds: its last element (``internal/infrastructure/clients`` -> ``clients``)."""
    return folder.rstrip("/").rsplit("/", 1)[-1].replace("-", "_")


def module_path(app: str) -> str:
    """The Go module path of an application type name or id: ``GmfCredit`` -> ``gmf-credit``."""
    return "-".join(w.lower() for w in words_of(ident(app, "App"))) or "app"


def lower_first(name: str) -> str:
    """``LookupDealer`` -> ``lookupDealer``, ``GMFFundingDB`` -> ``gmfFundingDB`` (unexported)."""
    m = re.match(r"[A-Z]+", name)
    if not m:
        return name
    run = m.group(0)
    if len(run) == len(name) or len(run) == 1:
        return name[: len(run)].lower() + name[len(run):]
    return run[:-1].lower() + name[len(run) - 1:]


class GoNaming:
    platform = "Go"
    config_file = "internal/platform/settings.go"
    messaging_actor = "ServiceBus"
    reserved = GO_KEYWORDS

    def escape(self, name: str) -> str:
        """A Go keyword with a trailing underscore (``type`` -> ``type_``); anything else unchanged."""
        return f"{name}_" if name in self.reserved else name

    def type_name(self, words: str, fallback: str = "Value") -> str:
        """Exported PascalCase type name (``dealer funding-request`` -> ``DealerFundingRequest``)."""
        return self.escape(ident(words, fallback))

    def method_name(self, prefix: str, words: str) -> str:
        """``prefix`` and ``words`` as one exported method (``("", "Lookup Dealer")`` ->
        ``LookupDealer``, ``("Execute", "")`` -> ``Execute``, ``("Handle", "funding event")`` ->
        ``HandleFundingEvent``)."""
        core = pascal(prefix) + pascal(words) or "Execute"
        if core[0].isdigit():
            core = f"Step{core}"
        return self.escape(core)

    def file_name(self, words: str) -> str:
        """``lookup_dealer.go``: the snake_case file that holds type ``type_name(words)``."""
        return go_file(snake(ident(words)))

    def exception_name(self, words: str) -> str:
        """The error type of a fault: ``DealerInactiveFault`` stays, ``Timeout`` -> ``TimeoutFault``."""
        fault = ident(words, "Step")
        return fault if fault.endswith(("Fault", "Error")) else f"{fault}Fault"

    def config_key(self, module: str, *names: str) -> str:
        """``GMF_<MODULE>_<NAME>`` (``("dealerfunding", "DB_URL")`` -> ``GMF_DEALERFUNDING_DB_URL``):
        what ``Gmf__<Module>__<Name>`` is read as. One name is a substitution variable; two are a
        resource and one of its settings."""
        if len(names) == 1:
            parts = [ident(names[0], "Var")]
        elif len(names) == 2:
            parts = [ident(names[0], "Resource"), ident(names[1], "Setting")]
        else:
            raise ValueError(f"config_key takes a setting or a resource and its setting, not {names!r}")
        return "_".join(["GMF", env_key(ident(module, "Module")), *(env_key(p) for p in parts)])

    def connection_key(self, resource: str) -> str:
        """``CONNECTION_STRINGS_<RESOURCE>``: what ``ConnectionStrings__<Resource>`` is read as."""
        return f"CONNECTION_STRINGS_{env_key(ident(resource, 'Resource'))}"

    def pattern_paths(self, pattern: str) -> tuple[str, str, str]:
        """(file under ``internal/``, type, method) templates of ``pattern``; ``KeyError`` for an
        unknown pattern. The file starts with the folder of ``PATTERNS[pattern].project``."""
        PATTERNS[pattern]  # KeyError for a pattern the catalogue does not have
        return _TEMPLATES[pattern]

    def row_names(self, pattern: str, words: dict[str, str]) -> tuple[str, str, str]:
        """(file relative to the row's layer folder, type, method) of a design row from raw words."""
        file_t, class_t, method_t = self.pattern_paths(pattern)
        resource = ident(words["resource"], "Step")
        if resource.endswith("Client") and len(resource) > len("Client"):
            resource = resource[: -len("Client")]
        activity = words["activity"]
        process = ident(words["process"], "Process")
        target = ident(words["target"], "Step") if words["target"] else "Step"
        exception = self.exception_name(words["fault"])
        values = {
            "process": process, "process_s": snake(process), "activity": ident(activity, "Step"),
            "activity_s": snake(ident(activity, "Step")), "resource": resource, "resource_s": snake(resource),
            "exception": exception, "exception_s": snake(exception), "target": target, "target_s": snake(target),
            "activity_m": self.method_name("", activity), "starter_m": self.method_name("", words["starter"]),
            "mapping_m": self.method_name("", f"{activity} Mapping"),
            "transform_m": self.method_name("", f"{activity} Transform"),
            "get_m": self.method_name("Get", activity), "set_m": self.method_name("Set", activity),
        }
        layer = PATTERNS[pattern].project
        file = file_t.format(**values)
        stem, _, _ = file.rpartition(".go")
        file = go_file(stem)
        return file[len(layer) + 1:], class_t.format(**values), method_t.format(**values)

    def layer_folder(self, kind: str, app: str) -> str:
        """The folder of a layer kind (``core`` -> ``internal/core``, ``tests`` -> ``tests``)."""
        if kind == "tests":
            return "tests"
        if kind not in ("api", "core", "infrastructure"):
            raise KeyError(kind)
        return f"internal/{kind}"

    def contract_target(self, app: str, module: str, stem: str) -> tuple[str, str]:
        """(import path, file in the core folder) of a contract: package ``contracts/<module>``, one
        file per contract named for it (``contracts/dealerfunding/funding_request.go``)."""
        base = f"{module_path(app)}/internal/core/contracts"
        file = go_file(snake(ident(stem)))
        mod = re.sub(r"[^a-z0-9]", "", module.lower())
        if not mod:
            return base, f"contracts/{file}"
        return f"{base}/{mod}", f"contracts/{mod}/{file}"

    def layout_scaffold(self, projects: dict[str, str], process: str,
                        patterns: set[str]) -> dict[str, set[str]]:
        """The files the Go generator writes next to the mapped ones, by layer kind (relative to
        the layer folder); ``process`` is the type name."""
        p = snake(process)
        files: dict[str, set[str]] = {
            "api": {"router.go", f"{p}_handler.go"} if patterns & {"rest.receive", "http.receive"} else {"router.go"},
            "core": {f"{p}_service.go", "services.go", "message_bus.go"},
            "infrastructure": set(),
            "tests": {f"{p}_rules_test.go", f"{p}_branch_test.go", f"{p}_scenario_test.go", "testdb_test.go",
                      "scenario_support_test.go"},
        }
        if patterns & JDBC_PATTERNS:
            files["infrastructure"].update({"db.go", f"{p}_repository.go"})
        return files
