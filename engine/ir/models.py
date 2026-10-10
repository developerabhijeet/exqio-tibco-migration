"""Pydantic v2 models for the neutral IR (design spec section 4.3).

Parsers for BW5 and BW6 both produce these models; every later component
(analyst, designer, generator, parity evaluator) consumes only these.
"""
import warnings
from typing import Any, Literal

from pydantic import BaseModel, Field

Dialect = Literal["bw5", "bw6"]


class InputBinding(BaseModel):
    target: str
    expression: str
    language: Literal["xpath", "xslt"]
    source_line: int | None = None


class Activity(BaseModel):
    id: str
    name: str
    type: str  # canonical type from catalogue.CANONICAL_TYPES (or a reported unknown)
    native_type: str
    config: dict[str, Any] = Field(default_factory=dict)
    input_bindings: list[InputBinding] = Field(default_factory=list)
    output_schema: str | None = None
    source_line: int | None = None
    group_id: str | None = None


class Transition(BaseModel):
    id: str
    from_id: str
    to_id: str
    kind: Literal["success", "conditional", "otherwise", "error"]
    expression: str | None = None
    source_line: int | None = None


class Group(BaseModel):
    id: str
    kind: Literal["loop", "iterate", "transaction", "critical", "parallel", "scope"]
    member_ids: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)


class Starter(BaseModel):
    activity_id: str
    kind: Literal["rest", "http", "soap", "jms", "timer", "subprocess"]
    config: dict[str, Any] = Field(default_factory=dict)


class FaultHandler(BaseModel):
    id: str
    fault_name: str | None = None
    activity_ids: list[str] = Field(default_factory=list)


class ProcessCall(BaseModel):
    activity_id: str
    target_process: str


class UnknownElement(BaseModel):
    path: str
    tag: str
    reason: str


class Complexity(BaseModel):
    score: float
    band: Literal["low", "medium", "high"]
    factors: dict[str, float] = Field(default_factory=dict)


with warnings.catch_warnings():
    # The IR contract names this field "schema", which shadows the deprecated
    # BaseModel.schema() classmethod. The shadowing is intentional and harmless
    # (we use model_json_schema()), so silence Pydantic's notice for this class only.
    warnings.simplefilter("ignore", UserWarning)

    class Variable(BaseModel):
        name: str
        schema: str | None = None
        scope: Literal["process", "scope"] = "process"


class Process(BaseModel):
    id: str
    application: str
    module: str = ""
    package: str = ""
    name: str
    dialect: Dialect
    source_path: str
    starter: Starter | None = None
    activities: list[Activity] = Field(default_factory=list)
    transitions: list[Transition] = Field(default_factory=list)
    groups: list[Group] = Field(default_factory=list)
    variables: list[Variable] = Field(default_factory=list)
    fault_handlers: list[FaultHandler] = Field(default_factory=list)
    calls: list[ProcessCall] = Field(default_factory=list)
    complexity: Complexity | None = None
    unknown_elements: list[UnknownElement] = Field(default_factory=list)
    contracts_used: list[str] = Field(default_factory=list)
    raw_xml_sha256: str = ""


class Resource(BaseModel):
    id: str
    kind: Literal["jdbc", "http", "jms", "file"]
    name: str
    source_path: str = ""
    settings: dict[str, Any] = Field(default_factory=dict)
    masked_fields: list[str] = Field(default_factory=list)


class SubstVar(BaseModel):
    name: str
    value: str
    type: str = "String"
    source_path: str = ""


class Contract(BaseModel):
    id: str
    kind: Literal["xsd", "wsdl", "json"]
    path: str
    root_elements: list[str] = Field(default_factory=list)
    text: str = ""


class Application(BaseModel):
    id: str
    name: str
    dialect: Dialect
    modules: list[str] = Field(default_factory=list)
    processes: list[Process] = Field(default_factory=list)
    resources: list[Resource] = Field(default_factory=list)
    substvars: list[SubstVar] = Field(default_factory=list)
    contracts: list[Contract] = Field(default_factory=list)
    sql_files: list[str] = Field(default_factory=list)
