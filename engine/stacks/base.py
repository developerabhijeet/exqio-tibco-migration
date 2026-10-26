"""The stack abstraction: what every target stack (.NET, Java, Go, Python) provides.

``StackGenerator`` is the per-stack object the registry hands out; ``StackRunner`` is the
restore/build/format/test/run surface ``DotnetRunner`` already has; ``SourceLanguage`` holds the
text checks the agents use on generated code; ``StackNaming`` turns design names into the
stack's identifiers and paths. See ``docs/superpowers/specs/2026-10-25-target-stacks-design.md``
sections 1 to 3.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NamedTuple, Protocol, runtime_checkable

from pydantic import BaseModel

if TYPE_CHECKING:  # the .NET models are the shared result shapes; imported lazily at runtime
    from engine.dotnet_gen.generator import GeneratedSolution
    from engine.dotnet_gen.runner import BuildResult, ServiceHandle, StepResult, TestRun

StackKey = Literal["dotnet", "java", "go", "python"]
STACK_KEYS: tuple[StackKey, ...] = ("dotnet", "java", "go", "python")


class Mirrors(BaseModel):
    """Package mirror URLs a generated project restores from; empty = the tool's own default."""

    nuget: str = ""
    maven: str = ""
    goproxy: str = ""
    pypi: str = ""


class ProjectFolder(BaseModel):
    name: str
    folder: str
    build_file: str | None  # the .csproj; None where the stack has one build file at the root


class StackLayout(BaseModel):
    """The layout contract between a stack's generator and infrastructure / Verify."""

    solution_file: str | None
    projects: dict[str, ProjectFolder]  # keyed by project kind: api, core, infrastructure, tests
    api_entry: str  # what ``run_service`` starts
    test_root: str
    dockerfile_context: dict[str, Any]  # template inputs for the stack's Dockerfile
    contract_file: str | None  # ``.exiqo/service-contract.json``; None for .NET


class StackStatus(BaseModel):
    key: str
    label: str
    available: bool
    reason: str | None
    required_tools: list[str]


@runtime_checkable
class StackRunner(Protocol):
    """Restore, build, format, test and run a generated project (``DotnetRunner`` satisfies it)."""

    def restore(self, env: dict[str, str] | None = None) -> StepResult: ...

    def build(self) -> BuildResult: ...

    def format_write(self, whitespace_only: bool = False) -> StepResult: ...

    def format_check(self, whitespace_only: bool = False) -> StepResult: ...

    def test(self) -> TestRun: ...

    def run_service(self, port: int, env: dict[str, str] | None = None,
                    timeout_s: float = 180.0) -> ServiceHandle: ...

    def env(self, extra: dict[str, str] | None = None) -> dict[str, str]: ...


class ForbiddenPattern(NamedTuple):
    """Something a Code Builder fill may not introduce into a production file
    (``builder.check_filled_file``): the fill is refused with ``reason`` when ``pattern`` matches
    more often in the new text than in the original. ``in_code`` counts the matches on
    ``SourceLanguage.strip_comments`` of both texts, so a mention in a comment does not count."""

    pattern: re.Pattern[str]
    reason: str
    in_code: bool = False


@runtime_checkable
class SourceLanguage(Protocol):
    """The code-text checks the agents run on generated source (spec section 3)."""

    comment_prefix: str

    @property
    def forbidden(self) -> tuple[ForbiddenPattern, ...]:
        """What a fill of a production file may not introduce, checked in order (assertions, test
        annotations, warning suppression, an extra fault handler, a file outside the project)."""
        ...

    @property
    def placeholder(self) -> re.Pattern[str]: ...

    def test_placeholder(self, test_id: str) -> str:
        """The failing statement of a test with no body, naming ``test_id`` (``fail("no test body
        for R1");``). It must be a valid statement for any ``test_id`` free of quotes, backticks,
        backslashes and line breaks (the agents remove those first,
        ``test_engineer.placeholder_safe``), and contain the text ``no test body for <test_id>``
        inside one plain string literal."""
        ...

    def strip_comments(self, text: str) -> str: ...

    def code_only(self, text: str) -> str: ...

    def declarations(self, text: str) -> list[str]: ...

    def tibco_comments(self, text: str) -> list[str]: ...

    def is_test_path(self, path: str) -> bool: ...

    def check_test_body(self, body: str, stem: str, kind: str = "rule",
                        json_kinds: dict[str, str] | None = None) -> str | None: ...


@runtime_checkable
class StackNaming(Protocol):
    """Identifiers, paths and stack words of a design (spec section 2): what the record's names are.

    The design passes RAW words, never pre-cased identifiers: an activity name as TIBCO spells it
    (``"Lookup Dealer"``, ``"LookupDealer"``, ``"lookup-dealer"``), a process name, a resource name.
    Each stack splits and cases them its own way, so one design code path serves every stack.

    Contract (``tests/test_stacks_design.py::test_naming_contract`` runs it over every stack):

    * ``type_name(words, fallback="Value")``: the stack's type identifier. Empty words give
      ``fallback``; words that would start with a digit are prefixed with it.
    * ``method_name(prefix, words)``: a method, the verb ``prefix`` joined before ``words``; empty
      ``words`` give the prefix alone, both empty give the stack's ``execute``.
    * ``file_name(words)``: the source file that holds type ``type_name(words)``, with extension.
    * ``exception_name(words)``: the exception type a TIBCO fault named ``words`` is thrown as.
    * ``config_key(module, *names)``: the configuration key of a setting, from raw words.
    * ``connection_key(resource)``: the configuration key of a JDBC resource's connection string.
    * ``reserved`` / ``escape(name)``: the stack's keywords, and an identifier made safe from them;
      ``type_name`` and ``method_name`` already return escaped names.
    * ``platform``, ``config_file``, ``messaging_actor``: words the design writes into its text
      (open questions, ignored-config reasons, the sequence's message broker).
    * ``row_names(pattern, words)``: the (file, class, method) of a design row, from the raw words
      of ``patterns.TEMPLATE_KEYS`` (``activity`` ``"Lookup Dealer"``, ``process``, ``resource``, ...).
      A stack builds them with its own ``type_name``, ``method_name`` and ``file_name``.
    * ``layer_folder``, ``contract_target``, ``layout_scaffold``: where things go.

    Per stack, for the activity ``Lookup Dealer`` (``method_name("", "Lookup Dealer")``); .NET is
    implemented, the other rows are the targets Tasks 7, 12 and 14 implement:

    ======  ======================  =====================  =========================  ==========================
    stack   type_name               method_name            file_name                  config_key("dealerfunding", "DB_URL")
    ======  ======================  =====================  =========================  ==========================
    .NET    ``LookupDealer``        ``LookupDealerAsync``  ``LookupDealer.cs``        ``Gmf:Dealerfunding:DbUrl``
    Java    ``LookupDealer``        ``lookupDealer``       ``LookupDealer.java``      ``gmf.dealerfunding.db-url``
    Go      ``LookupDealer``        ``LookupDealer``       ``lookup_dealer.go``       ``GMF_DEALERFUNDING_DB_URL``
    Python  ``LookupDealer``        ``lookup_dealer``      ``lookup_dealer.py``       ``GMF_DEALERFUNDING_DB_URL``
    ======  ======================  =====================  =========================  ==========================

    ``method_name("Execute", "")`` is ``ExecuteAsync`` / ``execute`` / ``Execute`` / ``execute``;
    escaping: Java ``new`` -> ``new_``, Python ``class`` -> ``class_``, Go ``type`` -> ``type_``;
    .NET PascalCase names never meet a C# keyword, and a lower-case one is written ``@name``.
    """

    platform: str  # ".NET", "Java", "Go", "Python"
    config_file: str  # where configuration is written: "appsettings.json", "application.yml", ...
    messaging_actor: str  # the broker in the sequence diagram: "ServiceBus"
    reserved: frozenset[str]

    def escape(self, name: str) -> str: ...

    def type_name(self, words: str, fallback: str = "Value") -> str: ...

    def method_name(self, prefix: str, words: str) -> str: ...

    def file_name(self, words: str) -> str: ...

    def exception_name(self, words: str) -> str: ...

    def config_key(self, module: str, *names: str) -> str: ...

    def connection_key(self, resource: str) -> str: ...

    def row_names(self, pattern: str, words: dict[str, str]) -> tuple[str, str, str]: ...

    def layer_folder(self, kind: str, app: str) -> str: ...

    def contract_target(self, app: str, module: str, stem: str) -> tuple[str, str]: ...

    def layout_scaffold(self, projects: dict[str, str], process: str,
                        patterns: set[str]) -> dict[str, set[str]]: ...


@runtime_checkable
class StackGenerator(Protocol):
    """One object per stack (spec section 1)."""

    key: StackKey
    label: str
    required_tools: tuple[str, ...]
    source_suffixes: tuple[str, ...]
    prompt_pack: str  # folder under engine/agents/prompts/; "" = the top level (.NET)
    download_excluded: frozenset[str]
    service_process_marker: str

    @property
    def language(self) -> SourceLanguage: ...

    @property
    def naming(self) -> StackNaming: ...

    def layout(self, app_name: str) -> StackLayout: ...

    def generate(self, app: Any, process_ids: list[str], designs: Any, rules: Any, findings: Any,
                 scenarios: list[Any], out_dir: Path, mirrors: Mirrors) -> GeneratedSolution: ...

    def runner(self, root: Path, *, env: dict[str, str] | None = None,
               coverage_scope: set[str] | None = None, timeout_s: int | None = None,
               api_entry: str | None = None) -> StackRunner: ...
