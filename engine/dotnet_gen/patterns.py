"""Deterministic pattern catalogue: canonical TIBCO step type to .NET pattern (design spec 7.2).

Every canonical type in ``engine.ir.catalogue.CANONICAL_TYPES`` (49) has a pattern. Templates are
``str.format`` strings over ``TEMPLATE_KEYS``; ``design.build_design_skeleton`` fills them:

==============  ===========================================================================
key             value
==============  ===========================================================================
app             PascalCase application id (``gmf.credit`` -> ``GmfCredit``)
process         PascalCase process name (``DealerFundingRequest``)
activity        PascalCase activity (or group) name (``LookupDealer``)
starter         PascalCase name of the process starter activity (``get``  -> ``Get``)
resource        shared resource base name with a trailing ``Client`` removed (``Equifax``)
fault           fault name of a throw (``DealerInactiveFault``)
exception       exception class of a throw: ``fault`` + ``Exception`` unless it already ends so
                (``DealerInactiveFaultException``), so it never clashes with the fault contract record
target          PascalCase name of a called subprocess (``AuditLog``)
destination     JMS queue or topic name, verbatim (``gmf.funding.events``)
==============  ===========================================================================

A pattern with a ``decision`` is flagged: the design record carries a Decision for every step
that uses it (a recommendation, not a decision, until the architect approves G3).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

TEMPLATE_KEYS: tuple[str, ...] = (
    "app", "process", "activity", "starter", "resource", "fault", "exception", "target", "destination",
)


@dataclass(frozen=True)
class DecisionHint:
    title: str
    choice: str
    alternatives: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class PatternSpec:
    name: str
    project: Literal["api", "core", "infrastructure", "tests"]
    file_template: str  # relative to the project folder
    class_template: str
    method_template: str
    nuget: list[str] = field(default_factory=list)
    description: str = ""
    origin: Literal["code", "agent"] = "code"
    decision: DecisionHint | None = None


_SERVICE = ("Services/{process}Service.cs", "{process}Service")
_CONTROLLER = ("Controllers/{process}Controller.cs", "{process}Controller")
_REPOSITORY = ("Repositories/{process}Repository.cs", "{process}Repository")
_MAPPINGS = ("Mappings/{process}Mappings.cs", "{process}Mappings")
_BUS = ("Messaging/IMessageBus.cs", "IMessageBus")
_SERIAL = ("Serialization/{process}Serialization.cs", "{process}Serialization")
_FILES = ("Files/{process}FileStore.cs", "{process}FileStore")
_SB = "Azure.Messaging.ServiceBus"
_DAPPER = ["Dapper", "Npgsql"]
_RESILIENCE = "Microsoft.Extensions.Http.Resilience"

_SAGA = DecisionHint(
    title="Checkpoint and confirm semantics",
    choice="Persist saga state in Postgres (outbox table) and resume from the last completed step",
    alternatives=("Drop the checkpoint and rely on idempotent re-delivery",
                  "Durable workflow engine (Azure Durable Functions or Temporal)"),
    reason="BW checkpoints let a restarted engine resume a job; .NET has no engine-level equivalent, "
           "so recovery must be designed explicitly.",
)
_SHARED = DecisionHint(
    title="Shared variable storage",
    choice="IDistributedCache (Redis) so every replica sees one value",
    alternatives=("IMemoryCache, correct only for a single instance", "A Postgres table with row locks"),
    reason="BW shared and job-shared variables live in one engine; App Service or AKS runs several "
           "replicas, so in-process memory changes behaviour.",
)
_FTP = DecisionHint(
    title="FTP transfer",
    choice="FluentFTP client wrapper behind an IFtpGateway interface, credentials from Key Vault",
    alternatives=("Azure Blob Storage SFTP endpoint", "Replace the file drop with an API or a queue"),
    reason="FTP is a transport the target platform may retire; the wrapper keeps the call site stable "
           "while the transport is decided.",
)
_MAIL = DecisionHint(
    title="Mail transport",
    choice="MailKit over SMTP or IMAP behind an interface, credentials from Key Vault",
    alternatives=("Azure Communication Services Email", "Microsoft Graph sendMail or mailbox subscription"),
    reason="The mail server and its authentication change with the hosting platform.",
)

_P: list[PatternSpec] = [
    # ---- starters
    PatternSpec("rest.receive", "api", *_CONTROLLER, "{activity}Async", ["Swashbuckle.AspNetCore"],
                "ASP.NET Core controller action on the same route and verb; the action calls the "
                "process service."),
    PatternSpec("http.receive", "api", *_CONTROLLER, "{activity}Async", ["Swashbuckle.AspNetCore"],
                "ASP.NET Core controller action on the HTTP Receiver resource path and method."),
    PatternSpec("soap.receive", "api", "Soap/{process}SoapService.cs", "{process}SoapService",
                "{activity}Async", ["CoreWCF.Http", "CoreWCF.Primitives"],
                "CoreWCF service contract on the same WSDL operation; the operation calls the process "
                "service."),
    PatternSpec("jms.receive", "infrastructure", "Messaging/{process}Consumer.cs", "{process}Consumer",
                "HandleAsync", [_SB],
                "IMessageBus consumer: Azure.Messaging.ServiceBus processor in production, in-memory bus "
                "for tests and local run; the JMS selector becomes a subscription rule or a guard."),
    PatternSpec("timer.start", "infrastructure", "Jobs/{process}Job.cs", "{process}Job", "ExecuteAsync",
                ["Microsoft.Extensions.Hosting"],
                "BackgroundService with the schedule read from configuration."),
    # ---- data
    PatternSpec("jdbc.query", "infrastructure", *_REPOSITORY, "{activity}Async", _DAPPER,
                "Repository method on Dapper with the SQL statement verbatim and parameters bound in "
                "order; rule and finding IDs in the XML doc comment."),
    PatternSpec("jdbc.update", "infrastructure", *_REPOSITORY, "{activity}Async", _DAPPER,
                "Repository method on Dapper (ExecuteAsync) with the statement verbatim and parameters "
                "bound in order."),
    PatternSpec("jdbc.call", "infrastructure", *_REPOSITORY, "{activity}Async", _DAPPER,
                "Repository method calling the stored procedure through Dapper with CommandType."
                "StoredProcedure; output parameters mapped by name."),
    # ---- outbound calls
    PatternSpec("http.invoke", "infrastructure", "Clients/{resource}Client.cs", "{resource}Client",
                "{activity}Async", [_RESILIENCE],
                "Typed IHttpClientFactory client with timeout, retry and circuit breaker from the HTTP "
                "client resource settings."),
    PatternSpec("rest.invoke", "infrastructure", "Clients/{resource}Client.cs", "{resource}Client",
                "{activity}Async", [_RESILIENCE],
                "Typed IHttpClientFactory client for the REST reference with System.Text.Json bodies and "
                "resilience from the resource settings."),
    PatternSpec("soap.invoke", "infrastructure", "Clients/{resource}Client.cs", "{resource}Client",
                "{activity}Async", ["System.ServiceModel.Http", _RESILIENCE],
                "WCF client proxy generated from the WSDL (dotnet-svcutil) wrapped in a typed client."),
    # ---- messaging
    PatternSpec("jms.send", "core", *_BUS, 'PublishAsync("{destination}")', [_SB],
                "IMessageBus.PublishAsync to the Service Bus queue named in the design record."),
    PatternSpec("jms.publish", "core", *_BUS, 'PublishAsync("{destination}")', [_SB],
                "IMessageBus.PublishAsync to the Service Bus topic named in the design record."),
    PatternSpec("jms.reply", "core", *_BUS, "ReplyAsync", [_SB],
                "IMessageBus.ReplyAsync to the ReplyTo entity of the received message, carrying the "
                "correlation id."),
    PatternSpec("jms.request_reply", "core", *_BUS, 'RequestAsync("{destination}")', [_SB],
                "IMessageBus.RequestAsync: send with ReplyTo and a session id, then await the reply on a "
                "session receiver with the activity timeout."),
    # ---- flow and process structure
    PatternSpec("call.process", "core", "Services/{target}Service.cs", "{target}Service", "ExecuteAsync",
                [], "Service method call; a separate project only when the subprocess is shared across "
                "applications."),
    PatternSpec("mapper", "core", *_MAPPINGS, "{activity}Mapping", [],
                "Generated C# mapping method with the TIBCO expression kept as a comment; simple paths by "
                "code, arithmetic and functions by the builder agent."),
    PatternSpec("assign", "core", *_MAPPINGS, "{activity}Mapping", [],
                "Generated assignment to a process variable with the TIBCO expression kept as a comment."),
    PatternSpec("throw", "core", "Exceptions/{exception}.cs", "{exception}", ".ctor", [],
                "Typed exception carrying the fault contract record; mapped to ProblemDetails in one "
                "exception handler in the Api project."),
    PatternSpec("catch", "core", *_SERVICE, "ExecuteAsync", [],
                "try/catch with an exception filter on the typed exception (catch-all for catchAll) "
                "around the scope the handler covers."),
    PatternSpec("reply", "api", *_CONTROLLER, "{starter}Async", [],
                "Controller action result: the reply mapping builds the response record and the status "
                "code, faults become ProblemDetails."),
    PatternSpec("end", "core", *_SERVICE, "ExecuteAsync", [],
                "Return from the service method with the end mapping as the result."),
    PatternSpec("log", "core", *_SERVICE, "ExecuteAsync", ["Microsoft.Extensions.Logging.Abstractions"],
                "ILogger call with structured fields (message template); sensitive fields masked."),
    PatternSpec("noop", "core", *_SERVICE, "ExecuteAsync", [],
                "No code: a trace comment naming the TIBCO step keeps lineage."),
    PatternSpec("wait", "core", *_SERVICE, "ExecuteAsync", [],
                "await Task.Delay with the interval from configuration and the cancellation token."),
    PatternSpec("checkpoint", "core", *_SERVICE, "ExecuteAsync", [],
                "Saga state persisted at this step so a restarted instance resumes here.",
                decision=_SAGA),
    PatternSpec("confirm", "core", *_SERVICE, "ExecuteAsync", [],
                "Saga step completion recorded (message settled or state row confirmed).",
                decision=_SAGA),
    PatternSpec("engine.command", "core", *_SERVICE, "{activity}Async", [],
                "No .NET engine to command: statistics become OpenTelemetry metrics and health checks; "
                "the call site is reviewed by the builder.", origin="agent",
                decision=DecisionHint(
                    title="Engine command",
                    choice="Replace with OpenTelemetry metrics and ASP.NET Core health checks",
                    alternatives=("Drop the step and document it", "Expose an admin endpoint"),
                    reason="BW engine commands inspect or control the BW engine itself, which does not "
                           "exist after migration.")),
    PatternSpec("group.loop", "core", *_SERVICE, "{activity}Async", [],
                "while or do/while loop with the group condition; chunking when the IR shows batch "
                "semantics."),
    PatternSpec("group.iterate", "core", *_SERVICE, "{activity}Async", [],
                "foreach over the iterated list with the index and current element variables."),
    PatternSpec("group.transaction", "core", *_SERVICE, "{activity}Async", ["Npgsql"],
                "Npgsql transaction (one connection, one NpgsqlTransaction) around the group members; "
                "rollback on any exception."),
    PatternSpec("group.critical", "core", *_SERVICE, "{activity}Async", ["DistributedLock.Redis"],
                "Lock around the members: database row lock or distributed lock per design decision.",
                decision=DecisionHint(
                    title="Critical section lock",
                    choice="Postgres row lock (SELECT ... FOR UPDATE) when the section guards rows",
                    alternatives=("Redis distributed lock (DistributedLock.Redis)",
                                  "Postgres advisory lock"),
                    reason="A BW critical section serialises one engine; several replicas need a lock "
                           "that spans processes.")),
    PatternSpec("flow.parallel", "core", *_SERVICE, "{activity}Async", [],
                "Task.WhenAll over one task per branch; branch failures handled per the error "
                "transitions."),
    # ---- transforms and serialization
    PatternSpec("java.invoke", "core", "Interop/{activity}.cs", "{activity}", "InvokeAsync", [],
                "The Java code is carried into a documented decision: port to C# with tests, or wrap; "
                "never silently dropped.", origin="agent",
                decision=DecisionHint(
                    title="Java activity",
                    choice="Port the Java method to C# with unit tests generated from the observed "
                           "inputs and outputs",
                    alternatives=("Wrap the JAR behind a small HTTP sidecar",
                                  "Run the JAR through IKVM"),
                    reason="Java code is opaque to the parity evaluator; a port is testable, a wrapper "
                           "keeps a JVM in production.")),
    PatternSpec("xslt.transform", "core", "Transforms/{process}Transforms.cs", "{process}Transforms",
                "{activity}Transform", ["Saxon-HE"],
                "Saxon .NET with the stylesheet carried over verbatim, or a generated mapping when the "
                "stylesheet is simple.",
                decision=DecisionHint(
                    title="XSLT engine",
                    choice="Generated C# mapping when the stylesheet is simple, Saxon otherwise",
                    alternatives=("Saxon for every stylesheet", "XslCompiledTransform (XSLT 1.0 only)"),
                    reason="BW6 stylesheets are XSLT 2.0; .NET's built-in engine supports 1.0 only.")),
    PatternSpec("json.parse", "core", *_SERIAL, "{activity}", [],
                "System.Text.Json JsonSerializer.Deserialize into the contract record."),
    PatternSpec("json.render", "core", *_SERIAL, "{activity}", [],
                "System.Text.Json JsonSerializer.Serialize of the contract record."),
    PatternSpec("xml.parse", "core", *_SERIAL, "{activity}", [],
                "Generated XDocument mapping code (XmlReader with DTD processing prohibited) that reads "
                "each element by the record's TibcoPath and builds the contract record; the records "
                "carry JSON attributes only, so XmlSerializer is not used."),
    PatternSpec("xml.render", "core", *_SERIAL, "{activity}", [],
                "Generated XDocument mapping code that writes the contract record element by element "
                "from its TibcoPath, with the original target namespace; XmlSerializer is not used."),
    # ---- state
    PatternSpec("shared.get", "infrastructure", "State/SharedVariables.cs", "SharedVariables",
                'GetAsync("{activity}")', ["Microsoft.Extensions.Caching.StackExchangeRedis"],
                "Read of a shared variable through IDistributedCache or IMemoryCache per decision.",
                decision=_SHARED),
    PatternSpec("shared.set", "infrastructure", "State/SharedVariables.cs", "SharedVariables",
                'SetAsync("{activity}")', ["Microsoft.Extensions.Caching.StackExchangeRedis"],
                "Write of a shared variable through IDistributedCache or IMemoryCache per decision.",
                decision=_SHARED),
    # ---- files and external transports
    PatternSpec("file.read", "infrastructure", *_FILES, "{activity}Async", [],
                "File read through an IFileStore abstraction (local disk or Azure Files) with the path "
                "from configuration."),
    PatternSpec("file.write", "infrastructure", *_FILES, "{activity}Async", [],
                "File write or create through an IFileStore abstraction with the path from "
                "configuration."),
    PatternSpec("file.list", "infrastructure", *_FILES, "{activity}Async", [],
                "Directory listing through an IFileStore abstraction with the same filename pattern."),
    PatternSpec("ftp.get", "infrastructure", "Files/FtpGateway.cs", "FtpGateway", "{activity}Async",
                ["FluentFTP"], "FluentFTP download behind an IFtpGateway interface.", decision=_FTP),
    PatternSpec("ftp.put", "infrastructure", "Files/FtpGateway.cs", "FtpGateway", "{activity}Async",
                ["FluentFTP"], "FluentFTP upload behind an IFtpGateway interface.", decision=_FTP),
    PatternSpec("mail.send", "infrastructure", "Messaging/MailSender.cs", "MailSender", "{activity}Async",
                ["MailKit"], "MailKit SMTP send behind an IMailSender interface.", decision=_MAIL),
    PatternSpec("mail.receive", "infrastructure", "Messaging/{process}MailPoller.cs",
                "{process}MailPoller", "ExecuteAsync", ["MailKit"],
                "BackgroundService polling the mailbox with MailKit IMAP.", decision=_MAIL),
    PatternSpec("cmd.exec", "infrastructure", "Interop/CommandRunner.cs", "CommandRunner",
                "{activity}Async", [],
                "Process.Start wrapper with an allow-listed executable, arguments passed as a list "
                "(never a shell string), timeout and captured output.",
                decision=DecisionHint(
                    title="External command execution",
                    choice="Allow-listed Process.Start wrapper with argument lists and a timeout",
                    alternatives=("Replace the command with a library call or an API",
                                  "Run the command in a separate job container"),
                    reason="Executing commands from a service is a security risk (injection, container "
                           "image contents); it needs an explicit, reviewed boundary.")),
    # ---- not canonical types: rows the design adds next to activity rows
    PatternSpec("input.mapping", "core", *_MAPPINGS, "{activity}Mapping", [],
                "Input binding of a step: a generated mapping method with each TIBCO expression kept as "
                "a comment; simple paths by code, functions and arithmetic by the builder agent."),
    PatternSpec("unknown", "core", *_SERVICE, "{activity}Async", [],
                "Native type with no canonical mapping: the builder agent drafts it and an open question "
                "is raised.", origin="agent"),
]

PATTERNS: dict[str, PatternSpec] = {p.name: p for p in _P}
