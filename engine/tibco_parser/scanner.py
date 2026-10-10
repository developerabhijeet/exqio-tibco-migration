"""Estate scanner: the entry point of the platform (tab 1, Upload).

``scan_folder(root)`` walks an uploaded folder, classifies every file, discovers
the applications in it and runs both parsers:

* BW6 modules are folders holding ``META-INF/module.bwm``. BW6 applications are
  folders holding ``META-INF/TIBCO.xml`` (the ``*.application`` project or an
  application folder beside the module); each claims the modules its
  ``packaging:module/packaging:symbolicName`` entries name (module symbolic name
  from the module's ``META-INF/MANIFEST.MF``, else its folder name), nearest
  folder first. Modules no application claims are grouped by their parent folder.
  The application id is the application's bundle symbolic name (or folder name)
  without the ``.application`` suffix; module names are module folder names.
* A module folder inside a ``*.application/`` folder whose process files (at
  least one) are all sha256-identical to those of a module with the same symbolic name outside any
  ``*.application/`` folder is a packaged copy (Studio export): its process files
  are flagged ``duplicate_of`` the source file and the copy is not parsed or
  collected twice.
* BW5 projects are folders containing ``.process`` files: the nearest ancestor
  holding ``vcrepo.dat`` or ``defaultVars/`` is the project root, else the
  top-most folder of loose ``.process`` files. One Application per project root,
  named after the root folder, with that one module.
* Shared resources, substitution variables, contracts (XSD, WSDL and the JSON
  files under ``Service Descriptors``) and SQL files are collected into the
  owning Application. A SQL file outside every application goes to the
  closest application(s) with at least one ``jdbc.*`` activity (deepest common
  folder; ties go to all). ``ScanResult.sql_files`` lists every SQL file.
* ``synthetic`` is true when ``SYNTHETIC.txt`` sits at the scan root.

``ScanResult.report`` is one ``CompletenessReport`` across every parsed file.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from lxml import etree
from pydantic import BaseModel, Field

from engine.ir.models import Application, Contract, Process, Resource, SubstVar
from engine.tibco_parser import bw5, bw6
from engine.tibco_parser.completeness import CompletenessCollector, CompletenessReport
from engine.tibco_parser.contracts import parse_contract
from engine.tibco_parser.resources import _KIND_BY_EXT, parse_resource
from engine.tibco_parser.substvar import parse_substvar
from engine.tibco_parser.xmlutil import local, read_xml

FileKind = Literal["bwp", "process", "bwm", "xsd", "wsdl", "json", "substvar", "resource", "sql", "manifest", "other"]
_SKIP_DIRS = {".git", "__MACOSX", ".svn"}
_APP_SUFFIX = ".application"


class FileEntry(BaseModel):
    path: str  # posix path relative to the scan root
    kind: FileKind
    dialect: Literal["bw5", "bw6"] | None = None
    sha256: str
    duplicate_of: str | None = None  # relative path of the identical source file


class ScanResult(BaseModel):
    applications: list[Application] = Field(default_factory=list)
    report: CompletenessReport
    files: list[FileEntry] = Field(default_factory=list)
    synthetic: bool = False
    sql_files: list[str] = Field(default_factory=list)  # every SQL file in the upload (relative paths)


# --------------------------------------------------------------------- helpers
def _kind(path: Path) -> str:
    ext = path.suffix.lower()
    simple = {".bwp": "bwp", ".process": "process", ".bwm": "bwm", ".xsd": "xsd", ".wsdl": "wsdl",
              ".json": "json", ".substvar": "substvar", ".sql": "sql"}
    if ext in simple:
        return simple[ext]
    if path.name in ("MANIFEST.MF", "TIBCO.xml") and path.parent.name == "META-INF":
        return "manifest"
    if ext.startswith(".shared") or (ext.endswith("resource") and len(ext) > len(".resource")):
        return "resource"
    return "other"


def _manifest_value(manifest: Path, key: str) -> str | None:
    """Value of a MANIFEST.MF header (continuation lines joined; attributes after ';' dropped)."""
    if not manifest.is_file():
        return None
    lines: list[str] = []
    for line in manifest.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(" ") and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    for line in lines:
        name, sep, value = line.partition(":")
        if sep and name.strip() == key:
            return value.split(";", 1)[0].strip() or None
    return None


def _is_under(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


def _common_depth(a: Path, b: Path) -> int:
    return len(Path(os.path.commonpath([a, b])).parts)


@dataclass
class _Module:
    dir: Path
    name: str  # folder name (the IR module name)
    symbolic: str
    packaged_copy_of: _Module | None = None


@dataclass
class _App:
    id: str
    dialect: str
    dirs: list[Path]  # application folder (if any) then module folders, or the BW5 project root
    modules: list[_Module] = field(default_factory=list)
    root: Path | None = None  # BW5 project root
    declared: list[str] = field(default_factory=list)


class _Scanner:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.collector = CompletenessCollector()
        self.files: dict[Path, FileEntry] = {}

    def rel(self, p: Path) -> str:
        r = p.relative_to(self.root).as_posix()
        return r

    # ---- inventory
    def inventory(self) -> None:
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
            for fn in sorted(filenames):
                p = Path(dirpath) / fn
                if not p.is_file():
                    continue
                digest = hashlib.sha256(p.read_bytes()).hexdigest()
                self.files[p] = FileEntry(path=self.rel(p), kind=_kind(p), sha256=digest)  # type: ignore[arg-type]

    def of_kind(self, *kinds: str) -> list[Path]:
        return [p for p, f in self.files.items() if f.kind in kinds]

    # ---- BW6
    def modules(self) -> list[_Module]:
        out = []
        for bwm in self.of_kind("bwm"):
            if bwm.name != "module.bwm" or bwm.parent.name != "META-INF":
                continue
            d = bwm.parent.parent
            symbolic = _manifest_value(d / "META-INF" / "MANIFEST.MF", "Bundle-SymbolicName") or d.name
            out.append(_Module(dir=d, name=d.name, symbolic=symbolic))
        return sorted(out, key=lambda m: m.dir)

    def _in_application_folder(self, d: Path) -> bool:
        rel = d.relative_to(self.root).parts
        return any(part.endswith(_APP_SUFFIX) for part in rel[:-1])

    def mark_packaged_copies(self, modules: list[_Module]) -> None:
        sources = [m for m in modules if not self._in_application_folder(m.dir)]
        for m in modules:
            if not self._in_application_folder(m.dir):
                continue
            for src in (s for s in sources if s.symbolic == m.symbolic):
                mine = [p for p in self.of_kind("bwp") if _is_under(p, m.dir)]
                theirs = {self.files[p].sha256: p for p in self.of_kind("bwp") if _is_under(p, src.dir)}
                pairs = {}
                for p in mine:
                    twin = src.dir / p.relative_to(m.dir)
                    if twin in self.files and self.files[twin].sha256 == self.files[p].sha256:
                        pairs[p] = twin
                    elif self.files[p].sha256 in theirs:
                        pairs[p] = theirs[self.files[p].sha256]
                if mine and len(pairs) == len(mine):
                    m.packaged_copy_of = src
                    for p, twin in pairs.items():
                        self.files[p].duplicate_of = self.files[twin].path
                        self.collector.explain(self.files[p].path,
                                               f"packaged copy of {self.files[twin].path} (identical sha256); "
                                               "not parsed twice")
                    self.collector.explain(self.rel(m.dir) + "/",
                                           f"packaged copy of module {src.name} ({self.rel(src.dir)}) inside an "
                                           f"{_APP_SUFFIX} folder; its files are not collected twice")
                    break

    def bw6_apps(self, modules: list[_Module]) -> list[_App]:
        real = [m for m in modules if m.packaged_copy_of is None]
        module_dirs = {m.dir for m in modules}
        claimed: set[Path] = set()
        apps: list[_App] = []
        for tibco in sorted(self.of_kind("manifest")):
            if tibco.name != "TIBCO.xml":
                continue
            d = tibco.parent.parent
            if d in module_dirs:
                continue
            try:
                xml = read_xml(tibco)
            except (OSError, etree.XMLSyntaxError) as exc:
                self.collector.failure(str(tibco), f"{type(exc).__name__}: {exc}")
                continue
            declared = [(c.text or "").strip() for el in xml.iter() if local(el.tag) == "module"
                        for c in el if local(c.tag) == "symbolicName" and (c.text or "").strip()]
            symbolic = _manifest_value(d / "META-INF" / "MANIFEST.MF", "Bundle-SymbolicName") or d.name
            app_id = symbolic.removesuffix(_APP_SUFFIX)
            app = _App(id=app_id, dialect="bw6", dirs=[d], declared=declared)
            for sym in declared:
                cands = [m for m in real if m.dir not in claimed and sym in (m.symbolic, m.name)]
                if not cands:
                    self.collector.explain(self.rel(tibco), f"application module {sym!r} is not in the upload")
                    continue
                best = max(cands, key=lambda m: (_common_depth(m.dir, d), -len(m.dir.parts)))
                claimed.add(best.dir)
                app.modules.append(best)
                app.dirs.append(best.dir)
            apps.append(app)
        by_parent: dict[Path, list[_Module]] = {}
        for m in real:
            if m.dir not in claimed:
                # a module uploaded on its own (the scan root) is named after itself
                by_parent.setdefault(m.dir.parent if m.dir != self.root else m.dir, []).append(m)
        for parent, mods in sorted(by_parent.items()):
            apps.append(_App(id=parent.name, dialect="bw6", dirs=[m.dir for m in mods], modules=mods))
        return apps

    # ---- BW5
    def bw5_projects(self, bw6_dirs: list[Path]) -> list[_App]:
        procs = [p for p in self.of_kind("process") if not any(_is_under(p, d) for d in bw6_dirs)]
        roots: dict[Path, list[Path]] = {}
        orphans: list[Path] = []
        for p in procs:
            root = next((a for a in p.parents if _is_under(a, self.root)
                         and ((a / "vcrepo.dat").is_file() or (a / "defaultVars").is_dir())), None)
            if root is None:
                orphans.append(p)
            else:
                roots.setdefault(root, []).append(p)
        loose: list[Path] = []
        for d in sorted({p.parent for p in orphans}, key=lambda x: len(x.parts)):
            if not any(_is_under(d, r) for r in loose):
                loose.append(d)
        for p in orphans:
            roots.setdefault(next(r for r in loose if _is_under(p, r)), []).append(p)
        return [_App(id=r.name, dialect="bw5", dirs=[r], root=r) for r in sorted(roots)]

    # ---- parsing
    @staticmethod
    def _guard(fn):
        """Run one parse unit; a scan never stops on a bad file. Returns (result, error)."""
        try:
            return fn(), None
        except Exception as exc:  # noqa: BLE001 - any parser error becomes a ParseFailure, the scan goes on
            return None, exc

    def _try(self, path: Path, fn):
        """Parse a support file (resource, substvar, contract) with completeness bookkeeping."""
        self.collector.file_found(str(path))
        result, exc = self._guard(fn)
        if exc is not None:
            self.collector.failure(str(path), f"{type(exc).__name__}: {exc}")
            return None
        self.collector.file_parsed(str(path))
        return result

    def build(self, app: _App, owned: list[Path]) -> Application:
        processes: list[Process] = []
        resources: list[Resource] = []
        substvars: list[SubstVar] = []
        contracts: list[Contract] = []
        sql: list[str] = []
        for p in sorted(owned):
            entry = self.files[p]
            kind = entry.kind
            if kind == "bwp" and app.dialect == "bw6":
                module = next((m for m in app.modules if _is_under(p, m.dir)), None)
                # the parsers record their own failures before re-raising
                proc, _ = self._guard(lambda p=p, module=module: bw6.parse_bwp(
                    p, module.name if module else p.parent.name, app.id, self.collector))
                if proc is not None:
                    processes.append(proc)
            elif kind == "process" and app.dialect == "bw5":
                proc, _ = self._guard(lambda p=p: bw5.parse_process(p, app.id, self.collector, project_root=app.root))
                if proc is not None:
                    processes.append(proc)
            elif kind == "resource":
                if p.suffix.lower() in _KIND_BY_EXT:
                    r = self._try(p, lambda p=p: parse_resource(p, project_root=app.root))
                    if r is not None:
                        resources.append(r)
                else:
                    self.collector.explain(entry.path, f"shared resource type {p.suffix} has no IR resource kind "
                                                       "(jdbc, http, jms, file); kept as a file only")
            elif kind == "substvar":
                vs = self._try(p, lambda p=p: parse_substvar(p))
                substvars.extend(vs or [])
            elif kind in ("xsd", "wsdl") or (kind == "json" and "Service Descriptors" in p.parts):
                c = self._try(p, lambda p=p: parse_contract(p))
                if c is not None:
                    contracts.append(c)
            elif kind == "sql":
                sql.append(entry.path)
        return Application(
            id=app.id, name=app.id, dialect=app.dialect,  # type: ignore[arg-type]
            modules=[m.name for m in app.modules] if app.dialect == "bw6" else [app.id],
            processes=sorted(processes, key=lambda x: x.id), resources=sorted(resources, key=lambda r: r.id),
            substvars=substvars, contracts=sorted(contracts, key=lambda c: c.id), sql_files=sql,
        )

    def scan(self) -> ScanResult:
        self.inventory()
        modules = self.modules()
        self.mark_packaged_copies(modules)
        bw6_apps = self.bw6_apps(modules)
        bw6_dirs = [m.dir for m in modules] + [d for a in bw6_apps for d in a.dirs]
        apps = [a for a in bw6_apps if a.modules] + self.bw5_projects(bw6_dirs)
        for a in bw6_apps:
            if not a.modules:
                self.collector.explain(self.rel(a.dirs[0]) + "/", "application with none of its modules in the "
                                                                  "upload; nothing to migrate")
        seen: dict[str, int] = {}
        for a in apps:
            n = seen.get(a.id, 0) + 1
            seen[a.id] = n
            if n > 1:
                a.id = f"{a.id}~{n}"

        # ownership: the deepest application, module, packaged-copy or project folder holding a file
        owners: list[tuple[Path, _App | None]] = [(m.dir, None) for m in modules if m.packaged_copy_of]
        for a in apps:
            owners.extend((d, a) for d in a.dirs)
        owners.sort(key=lambda o: len(o[0].parts), reverse=True)
        owned: dict[int, list[Path]] = {id(a): [] for a in apps}
        stray_sql: list[Path] = []
        for p, entry in self.files.items():
            owner = next(((d, a) for d, a in owners if _is_under(p, d)), None)
            if owner is None:
                if entry.kind == "sql":
                    stray_sql.append(p)
                continue
            app = owner[1]
            if app is None:  # packaged copy
                entry.dialect = "bw6"
                continue
            entry.dialect = app.dialect  # type: ignore[assignment]
            owned[id(app)].append(p)
        for p, entry in self.files.items():
            if entry.kind in ("bwp", "bwm"):
                entry.dialect = "bw6"
            elif entry.kind == "process":
                entry.dialect = "bw5"

        applications = [self.build(a, owned[id(a)]) for a in apps]
        self.attach_stray_sql(stray_sql, apps, applications)
        return ScanResult(
            applications=applications, report=self.collector.report(),
            files=sorted(self.files.values(), key=lambda f: f.path),
            synthetic=(self.root / "SYNTHETIC.txt").is_file(),
            sql_files=sorted(f.path for f in self.files.values() if f.kind == "sql"),
        )

    def attach_stray_sql(self, stray: list[Path], apps: list[_App], applications: list[Application]) -> None:
        """A SQL file outside every application goes to the closest application(s) that use JDBC
        (deepest common folder; ties go to all). With no JDBC application it stays estate-level only."""
        jdbc = [(a, built) for a, built in zip(apps, applications, strict=True)
                if any(act.type.startswith("jdbc.") for proc in built.processes for act in proc.activities)]
        for p in stray:
            if not jdbc:
                self.collector.explain(self.files[p].path, "SQL file outside every application and no "
                                                           "application uses JDBC; kept at estate level")
                continue
            depth = {id(a): max(_common_depth(p, d) for d in a.dirs) for a, _ in jdbc}
            best = max(depth.values())
            for a, built in jdbc:
                if depth[id(a)] == best:
                    built.sql_files = sorted({*built.sql_files, self.files[p].path})


def scan_folder(root: Path) -> ScanResult:
    """Scan an uploaded folder: classify files, discover applications, run both parsers."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"not a folder: {root}")
    return _Scanner(root).scan()
