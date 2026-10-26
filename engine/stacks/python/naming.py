"""``PythonNaming``: the Python identifiers and paths of a design (target-stacks spec section 2, ruling X16).

One distribution, package ``<app snake>`` (``GmfCredit`` -> ``gmf_credit``) in a ``src`` layout.
Layer folders are the spec's: ``api``, ``core`` and ``infrastructure`` are sub-packages under
``src/<pkg>/``, ``tests`` is the top-level ``tests`` folder. Classes are PascalCase, functions and
modules snake_case (``Lookup Dealer`` -> ``lookup_dealer``), Python keywords are escaped with a
trailing underscore (``class`` -> ``class_``). Every service method is ``async def``; a method name
never carries an ``_async`` suffix.

``pattern_paths`` gives every pattern's (file, class, method) template relative to the package root,
the file always under the folder of the pattern's project kind (``core/{process_s}_service.py``);
``row_names`` fills it and returns the file relative to that layer folder, which is what a
``MappingRow.file`` is. The bus port sits in ``core/bus.py`` (Core must not depend on
Infrastructure); its implementations are in ``infrastructure/``.

Configuration keys are the runtime contract's environment names in upper snake case:
``Gmf__Dealerfunding__DbUrl`` is ``GMF_DEALERFUNDING_DB_URL`` and ``ConnectionStrings__GMFFundingDB``
is ``CONNECTION_STRINGS_GMF_FUNDING_DB``; the generated ``config`` module applies the same rule
(``upper_snake`` below) to every ``__``-separated variable.
"""
from __future__ import annotations

import keyword
import re

from engine.dotnet_gen.patterns import JDBC_PATTERNS, PATTERNS
from engine.stacks.contracts import ident, pascal

CONFIG_ROOT = "GMF"
PYTHON_KEYWORDS = frozenset(keyword.kwlist)
WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")

# (file under the package root, class, method) per pattern, over the cased values ``row_names`` makes.
_SERVICE = ("core/{process_s}_service.py", "{process}Service")
_ROUTES = ("api/{process_s}_routes.py", "{process}Routes")
_REPOSITORY = ("infrastructure/{process_s}_repository.py", "{process}Repository")
_MAPPINGS = ("core/{process_s}_mappings.py", "{process}Mappings")
_BUS = ("core/bus.py", "MessageBus")
_SERIAL = ("core/{process_s}_serialization.py", "{process}Serialization")
_FILES = ("infrastructure/{process_s}_file_store.py", "{process}FileStore")
_CLIENT = ("infrastructure/clients/{resource_s}_client.py", "{resource}Client", "{activity_m}")
_STEP = (*_SERVICE, "{activity_m}")
_ENTRY = (*_SERVICE, "execute")
_TEMPLATES: dict[str, tuple[str, str, str]] = {
    "rest.receive": (*_ROUTES, "{activity_m}"),
    "http.receive": (*_ROUTES, "{activity_m}"),
    "soap.receive": ("api/{process_s}_soap_service.py", "{process}SoapService", "{activity_m}"),
    "jms.receive": ("infrastructure/messaging/{process_s}_consumer.py", "{process}Consumer", "handle"),
    "timer.start": ("infrastructure/jobs/{process_s}_job.py", "{process}Job", "execute"),
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
    "call.process": ("core/{target_s}_service.py", "{target}Service", "execute"),
    "mapper": (*_MAPPINGS, "{mapping_m}"),
    "assign": (*_MAPPINGS, "{mapping_m}"),
    "input.mapping": (*_MAPPINGS, "{mapping_m}"),
    "throw": ("core/exceptions/{exception_s}.py", "{exception}", "__init__"),
    "catch": _ENTRY,
    "reply": (*_ROUTES, "{starter_m}"),
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
    "java.invoke": ("core/interop/{activity_s}.py", "{activity}", "invoke"),
    "xslt.transform": ("core/{process_s}_transforms.py", "{process}Transforms", "{transform_m}"),
    "json.parse": (*_SERIAL, "{activity_m}"),
    "json.render": (*_SERIAL, "{activity_m}"),
    "xml.parse": (*_SERIAL, "{activity_m}"),
    "xml.render": (*_SERIAL, "{activity_m}"),
    "shared.get": ("infrastructure/state/shared_variables.py", "SharedVariables", "{get_m}"),
    "shared.set": ("infrastructure/state/shared_variables.py", "SharedVariables", "{set_m}"),
    "file.read": (*_FILES, "{activity_m}"),
    "file.write": (*_FILES, "{activity_m}"),
    "file.list": (*_FILES, "{activity_m}"),
    "ftp.get": ("infrastructure/files/ftp_gateway.py", "FtpGateway", "{activity_m}"),
    "ftp.put": ("infrastructure/files/ftp_gateway.py", "FtpGateway", "{activity_m}"),
    "mail.send": ("infrastructure/messaging/mail_sender.py", "MailSender", "{activity_m}"),
    "mail.receive": ("infrastructure/messaging/{process_s}_mail_poller.py", "{process}MailPoller", "execute"),
    "cmd.exec": ("infrastructure/interop/command_runner.py", "CommandRunner", "{activity_m}"),
    "unknown": _STEP,
}


def snake(identifier: str) -> str:
    """``GMFFundingDB`` -> ``gmf_funding_db``, ``DealerFundingRequest`` -> ``dealer_funding_request``."""
    return "_".join(w.lower() for w in WORD.findall(identifier)) or identifier.lower()


def upper_snake(identifier: str) -> str:
    """``DbUrl`` -> ``DB_URL``: one segment of a configuration key (the generated ``config`` rule)."""
    return snake(identifier).upper()


def package_name(app: str) -> str:
    """The import package of the application's type name or id (``GmfCredit`` -> ``gmf_credit``)."""
    name = snake(ident(app, "App"))
    name = re.sub(r"[^a-z0-9_]", "", name) or "app"
    return f"app_{name}" if name[0].isdigit() or name in PYTHON_KEYWORDS else name


class PythonNaming:
    platform = "Python"
    config_file = "config.py"
    messaging_actor = "ServiceBus"
    reserved = PYTHON_KEYWORDS

    def escape(self, name: str) -> str:
        """A Python keyword with a trailing underscore (``class`` -> ``class_``); anything else unchanged."""
        return f"{name}_" if name in self.reserved else name

    def type_name(self, words: str, fallback: str = "Value") -> str:
        """PascalCase class name (``dealer funding-request`` -> ``DealerFundingRequest``)."""
        return self.escape(ident(words, fallback))

    def method_name(self, prefix: str, words: str) -> str:
        """``prefix`` and ``words`` as one snake_case function (``("", "Lookup Dealer")`` ->
        ``lookup_dealer``, ``("Execute", "")`` -> ``execute``, ``("Handle", "funding event")`` ->
        ``handle_funding_event``)."""
        core = pascal(prefix) + pascal(words) or "Execute"
        name = snake(core)
        if name[0].isdigit():
            name = f"step_{name}"
        return self.escape(name)

    def module_name(self, words: str) -> str:
        """The module that holds class ``type_name(words)``: ``lookup_dealer``."""
        name = snake(ident(words))
        return self.escape(f"m_{name}" if name[0].isdigit() else name)

    def file_name(self, words: str) -> str:
        """``lookup_dealer.py``: the module named for the class it holds."""
        return f"{self.module_name(words)}.py"

    def exception_name(self, words: str) -> str:
        """The exception of a fault: ``DealerInactiveFault`` -> ``DealerInactiveFaultError``."""
        fault = ident(words, "Step")
        return fault if fault.endswith(("Error", "Exception")) else f"{fault}Error"

    def config_key(self, module: str, *names: str) -> str:
        """``GMF_<MODULE>_<NAME>`` (``("dealerfunding", "DB_URL")`` -> ``GMF_DEALERFUNDING_DB_URL``):
        what ``Gmf__<Module>__<Name>`` reads as. One name is a substitution variable; two are a
        resource and one of its settings."""
        if len(names) == 1:
            parts = [ident(names[0], "Var")]
        elif len(names) == 2:
            parts = [ident(names[0], "Resource"), ident(names[1], "Setting")]
        else:
            raise ValueError(f"config_key takes a setting or a resource and its setting, not {names!r}")
        return "_".join([CONFIG_ROOT, upper_snake(ident(module, "Module")), *(upper_snake(p) for p in parts)])

    def connection_key(self, resource: str) -> str:
        """``CONNECTION_STRINGS_<RESOURCE>``: what ``ConnectionStrings__<Resource>`` reads as."""
        return f"CONNECTION_STRINGS_{upper_snake(ident(resource, 'Resource'))}"

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
        process = ident(words["process"], "Process")
        target = ident(words["target"], "Step") if words["target"] else "Step"
        exception = self.exception_name(words["fault"])
        values = {
            "process": process, "process_s": snake(process), "activity": ident(activity, "Step"),
            "activity_s": self.module_name(activity or "Step"), "resource": resource, "resource_s": snake(resource),
            "exception": exception, "exception_s": snake(exception), "target": target, "target_s": snake(target),
            "activity_m": self.method_name("", activity), "starter_m": self.method_name("", words["starter"]),
            "mapping_m": self.method_name("", f"{activity} Mapping"),
            "transform_m": self.method_name("", f"{activity} Transform"),
            "get_m": self.method_name("get", activity), "set_m": self.method_name("set", activity),
        }
        layer = PATTERNS[pattern].project
        file = file_t.format(**values)
        return file[len(layer) + 1:], class_t.format(**values), method_t.format(**values)

    def layer_folder(self, kind: str, app: str) -> str:
        """The folder of a layer kind (``core`` -> ``src/gmf_credit/core``, ``tests`` -> ``tests``)."""
        if kind == "tests":
            return "tests"
        if kind not in ("api", "core", "infrastructure"):
            raise KeyError(kind)
        return f"src/{package_name(app)}/{kind}"

    def contract_target(self, app: str, module: str, stem: str) -> tuple[str, str]:
        """(package, file in the core folder) of a contract: one module per contract, named for its
        stem, in a package per TIBCO module (``contracts/dealerfunding/funding_request.py``)."""
        base = f"{package_name(app)}.core.contracts"
        file = self.file_name(stem)
        mod = re.sub(r"[^a-z0-9]", "", module.lower())
        if mod and mod[0].isdigit():
            mod = f"m{mod}"
        if not mod:
            return base, f"contracts/{file}"
        return f"{base}.{mod}", f"contracts/{mod}/{file}"

    def layout_scaffold(self, projects: dict[str, str], process: str,
                        patterns: set[str]) -> dict[str, set[str]]:
        """The files the Python generator writes next to the mapped ones, by layer kind (relative to
        the layer folder); ``process`` is the type name."""
        p = snake(process)
        files: dict[str, set[str]] = {
            "api": {"__init__.py", "auth.py", "devtoken.py", "bus_dev.py", "fault.py"},
            "core": {"__init__.py", f"{p}_service.py", "tib.py", "masking.py", "bus.py", "faults.py",
                     "contracts/__init__.py", "exceptions/__init__.py"},
            "infrastructure": {"__init__.py", "bus_memory.py", "bus_servicebus.py"},
            "tests": {f"test_{p}_rules.py", f"test_{p}_branch.py", f"test_{p}_scenario.py", "conftest.py",
                      "scenario_support.py"},
        }
        if patterns & JDBC_PATTERNS:
            files["infrastructure"].add("database.py")
        return files
