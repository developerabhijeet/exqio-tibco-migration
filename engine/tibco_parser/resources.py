"""Parser for BW6 (.jdbcResource, .httpClientResource, .httpConnResource,
.jmsConnResource) and BW5 (.sharedjdbc, .sharedhttp, .sharedjmscon) resources.

Both dialects map onto one `Resource` shape with consistent snake_case
`settings` keys. Anything not mapped lands in `settings["raw"]` so nothing is
dropped. Secrets are replaced by "***" and their keys listed in `masked_fields`.
"""
import re
from pathlib import Path
from typing import Any

from engine.ir.models import Resource
from engine.tibco_parser.xmlutil import local, read_xml

MASK = "***"
_SECRET = re.compile(r"password|credential|secret", re.IGNORECASE)
_META_NS = ("http://www.omg.org/XMI", "http://www.w3.org/2001/XMLSchema-instance")


def _is_meta(attr_key: str) -> bool:
    """xmi:id / xmi:version / xsi:type bookkeeping attributes carry no settings."""
    return attr_key.startswith("{") and attr_key[1:].split("}", 1)[0] in _META_NS


_INT_KEYS = {
    "max_connections", "login_timeout", "port", "timeout_ms", "circuit_breaker_failure_rate",
    "circuit_breaker_window", "circuit_breaker_sleep_ms", "max_concurrent_requests",
}
_BOOL_KEYS = {"use_ssl", "use_xa", "use_jndi", "use_shared_jndi_config", "auto_gen_client_id"}

# BW6 attribute name -> (settings key). Anything else on the element goes to raw.
_BW6_JDBC = {"username": "username", "password": "password", "maxConnections": "max_connections",
             "loginTimeout": "login_timeout", "jdbcDriver": "driver", "dbURL": "jdbc_url"}
_BW6_HTTP_CLIENT = {
    "cmdExecutionIsolationTimeout": "timeout_ms",
    "cmdCircuitBreakerErrorThresholdPercentage": "circuit_breaker_failure_rate",
    "cmdCircuitBreakerRequestVolumeThreshold": "circuit_breaker_window",
    "cmdCircuitBreakerSleepWindow": "circuit_breaker_sleep_ms",
    "cmdExecutionIsolationSemaphoreMaxConcRequests": "max_concurrent_requests",
    "port": "port", "host": "host",
}
_BW6_HTTP_CONN = {"port": "port", "host": "host"}
_BW6_JMS = {"providerURL": "provider_url", "username": "username", "password": "password",
            "clientID": "client_id", "connectionFactoryType": "connection_factory_type",
            "messagingStyle": "messaging_style"}

_BW5_JDBC = {"driver": "driver", "location": "jdbc_url", "maxConnections": "max_connections",
             "loginTimeout": "login_timeout", "connectionType": "connection_type",
             "UseSharedJndiConfig": "use_shared_jndi_config", "user": "username",
             "password": "password"}
_BW5_JMS = {"ProviderURL": "provider_url", "NamingURL": "naming_url",
            "NamingInitialContextFactory": "factory", "TopicFactoryName": "topic_factory",
            "QueueFactoryName": "queue_factory", "NamingPrincipal": "naming_principal",
            "NamingCredential": "naming_credential", "username": "username",
            "password": "password", "clientID": "client_id",
            "autoGenClientID": "auto_gen_client_id", "UseXACF": "use_xa",
            "UseJNDI": "use_jndi", "UseSharedJndiConfig": "use_shared_jndi_config"}
_BW5_HTTP = {"Host": "host", "Port": "port", "serverType": "server_type", "useSsl": "use_ssl"}

_KIND_BY_EXT = {
    ".jdbcresource": ("jdbc", "bw6"), ".httpclientresource": ("http", "bw6"),
    ".httpconnresource": ("http", "bw6"), ".jmsconnresource": ("jms", "bw6"),
    ".sharedjdbc": ("jdbc", "bw5"), ".sharedhttp": ("http", "bw5"),
    ".sharedjmscon": ("jms", "bw5"),
}
_BW5_PROJECT_MARKERS = ("defaultVars", ".project", ".designtimelibs")


def _coerce(key: str, value: str) -> Any:
    if key in _INT_KEYS and re.fullmatch(r"-?\d+", value):
        return int(value)
    if key in _BOOL_KEYS and value.lower() in ("true", "false"):
        return value.lower() == "true"
    return value


def _to_raw(el) -> Any:
    """Lossless-enough dict form of an element we have no explicit mapping for."""
    attrs = {f"@{local(k)}": v for k, v in el.attrib.items() if not _is_meta(k)}
    text = (el.text or "").strip()
    kids: dict[str, Any] = {}
    for c in el:
        if not isinstance(c.tag, str):
            continue
        name = local(c.tag)
        val = _to_raw(c)
        if name in kids:
            if not isinstance(kids[name], list):
                kids[name] = [kids[name]]
            kids[name].append(val)
        else:
            kids[name] = val
    if not attrs and not kids:
        return text
    out: dict[str, Any] = {**attrs, **kids}
    if text:
        out["#text"] = text
    return out


class _Builder:
    def __init__(self) -> None:
        self.settings: dict[str, Any] = {}
        self.raw: dict[str, Any] = {}
        self.bindings: dict[str, str] = {}

    def put(self, key: str, value: str, *, drop_empty: bool = True) -> None:
        if drop_empty and value == "":
            return
        self.settings[key] = _coerce(key, value)

    def attrs(self, el, mapping: dict[str, str], prefix: str = "") -> None:
        for k, v in el.attrib.items():
            if _is_meta(k):
                continue
            name = local(k)
            if name in mapping:
                self.put(mapping[name], v)
            elif _SECRET.search(name):
                self.put(_snake(name), v)
            else:
                self.raw[prefix + name] = v

    def binding(self, el) -> None:
        tpl, prop = el.get("template"), el.get("propName")
        if tpl and prop:
            self.bindings[tpl] = prop
        else:
            self.raw.setdefault("substitutionBindings", []).append(_to_raw(el))

    def finish(self) -> tuple[dict[str, Any], list[str]]:
        masked: list[str] = []
        for k, v in list(self.settings.items()):
            if _SECRET.search(k) and isinstance(v, str) and v != "":
                self.settings[k] = MASK
                masked.append(k)
        if self.bindings:
            self.settings["substitution_bindings"] = self.bindings
        if self.raw:
            self.settings["raw"] = self.raw
        return self.settings, masked


def _snake(name: str) -> str:
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower()


def _children(el, name: str):
    return [c for c in el if local(c.tag) == name]


# --------------------------------------------------------------------- BW6
def _parse_bw6(root, kind: str, ext: str) -> dict[str, Any]:
    b = _Builder()
    cfg = next((c for c in root if local(c.tag) == "configuration"), None)
    if cfg is None:
        raise ValueError("BW6 resource has no <configuration> element")
    if kind == "jdbc":
        top, nested = _BW6_JDBC, _BW6_JDBC
    elif kind == "jms":
        top, nested = _BW6_JMS, _BW6_JMS
    elif ext == ".httpclientresource":
        b.settings["role"] = "client"
        top, nested = _BW6_HTTP_CLIENT, _BW6_HTTP_CLIENT
    else:
        b.settings["role"] = "server"
        top, nested = _BW6_HTTP_CONN, _BW6_HTTP_CONN
    b.attrs(cfg, top)
    for child in cfg:
        if not isinstance(child.tag, str):
            continue
        name = local(child.tag)
        if name == "substitutionBindings":
            b.binding(child)
            continue
        if name in ("connectionConfig", "tcpDetails"):
            xsi_type = next((v for k, v in child.attrib.items() if local(k) == "type"), None)
            if xsi_type:
                b.raw[f"{name}.type"] = xsi_type.rsplit(":", 1)[-1]
            b.attrs(child, nested, prefix=f"{name}.")
            for sub in child:
                if local(sub.tag) == "substitutionBindings":
                    b.binding(sub)
                elif isinstance(sub.tag, str):
                    b.raw[f"{name}.{local(sub.tag)}"] = _to_raw(sub)
            continue
        b.raw[name] = _to_raw(child)
    # Studio keeps the jndi:reference (subject binding) beside the configuration.
    for ref in _children(root, "reference"):
        if ref.get("value"):
            b.raw["reference." + (ref.get("key") or "ref")] = ref.get("value")
    return {"b": b}


# --------------------------------------------------------------------- BW5
def _leaf_text(el) -> str:
    return (el.text or "").strip()


def _parse_bw5(root, kind: str) -> dict[str, Any]:
    b = _Builder()
    cfg = next((c for c in root if local(c.tag) == "config"), None)
    if cfg is None:
        raise ValueError("BW5 resource has no <config> element")
    mapping = {"jdbc": _BW5_JDBC, "jms": _BW5_JMS, "http": _BW5_HTTP}[kind]

    def walk(el, prefix: str = "") -> None:
        for child in el:
            if not isinstance(child.tag, str):
                continue
            name = local(child.tag)
            has_kids = any(isinstance(c.tag, str) for c in child)
            if name == "HttpConnectionProperties":
                _bw5_http_props(b, child)
            elif name in ("NamingEnvironment", "ConnectionAttributes"):
                walk(child)
            elif has_kids:
                b.raw[prefix + name] = _to_raw(child)
            elif name in mapping:
                b.put(mapping[name], _leaf_text(child))
            elif _SECRET.search(name):
                b.put(_snake(name), _leaf_text(child))
            elif _leaf_text(child) != "":
                b.raw[prefix + name] = _leaf_text(child)

    walk(cfg)
    return {"b": b}


def _bw5_http_props(b: _Builder, props) -> None:
    table: dict[str, dict[str, str]] = {}
    for row in _children(props, "row"):
        names = _children(row, "PropertyName")
        pname = _leaf_text(names[0]) if names else ""
        if not pname:
            continue
        val = _children(row, "PropertyValue")
        dflt = _children(row, "PropertyDefaultValue")
        table[pname] = {"value": _leaf_text(val[0]) if val else "", "default": _leaf_text(dflt[0]) if dflt else ""}
        if pname.startswith("connectionTimeOut") and table[pname]["value"]:
            b.put("timeout_ms", table[pname]["value"])
    if table:
        b.raw["http_connection_properties"] = table


# --------------------------------------------------------------- ids / entry
def _bw6_id(path: Path) -> str | None:
    parts = path.parts
    for i in range(len(parts) - 2, 0, -1):
        if parts[i] == "Resources":
            module = parts[i - 1]
            package = ".".join(parts[i + 1:-1])
            return ".".join(p for p in (module, package, path.stem) if p)
    return None


def _bw5_project_root(path: Path) -> Path:
    for anc in path.parents:
        if any((anc / m).exists() for m in _BW5_PROJECT_MARKERS):
            return anc
    return path.parent


def _bw5_id(path: Path, project_root: Path | None) -> str:
    root = project_root or _bw5_project_root(path)
    try:
        rel = path.relative_to(root).with_suffix("")
    except ValueError:
        rel = Path(path.stem)
    return f"{root.name}/{rel.as_posix()}"


def parse_resource(path: Path, project_root: Path | None = None) -> Resource:
    """Parse one shared resource; `project_root` overrides BW5 project detection."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext not in _KIND_BY_EXT:
        raise ValueError(f"Unsupported resource file type: {path.name}")
    kind, dialect = _KIND_BY_EXT[ext]
    root = read_xml(path)
    if dialect == "bw6":
        built = _parse_bw6(root, kind, ext)
        rid = _bw6_id(path) or root.get("name") or path.stem
    else:
        built = _parse_bw5(root, kind)
        rid = _bw5_id(path, project_root)
    settings, masked = built["b"].finish()
    name = path.stem
    return Resource(
        id=rid, kind=kind, name=name, source_path=str(path),
        settings=settings, masked_fields=masked,
    )
