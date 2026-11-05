"""ORM models; importing this package registers every table on ``Base.metadata``."""
from api.app.models.analysis import AnalysisRun, Challenge, CoverageItem, Finding, Rule
from api.app.models.build import Build, BuildFile, Metric, TestResult
from api.app.models.chat import ChatConversation, ChatMessage
from api.app.models.connector import ConnectorLink
from api.app.models.design import DesignRecord
from api.app.models.domain import Domain, GraphEdge, GraphNode
from api.app.models.expert_rule import ExpertRule
from api.app.models.gate import AuditLog, Gate
from api.app.models.infra import AzureConnection, InfraAsset, Plan
from api.app.models.iteration import EditSet, Iteration, IterationInput, ProjectRepo
from api.app.models.job import Job, JobEvent, ModelCall
from api.app.models.memory import MemoryEvent
from api.app.models.process import Process
from api.app.models.project import Application, File, Project, ScanReport, Upload
from api.app.models.settings import Eval, Provider, Setting
from api.app.models.verify import FieldDiff, Scenario, ScenarioRun

__all__ = [
    "AnalysisRun",
    "Application",
    "AuditLog",
    "AzureConnection",
    "Build",
    "BuildFile",
    "Challenge",
    "ChatConversation",
    "ChatMessage",
    "ConnectorLink",
    "CoverageItem",
    "DesignRecord",
    "Domain",
    "EditSet",
    "Eval",
    "ExpertRule",
    "FieldDiff",
    "File",
    "Finding",
    "Gate",
    "GraphEdge",
    "GraphNode",
    "InfraAsset",
    "Iteration",
    "IterationInput",
    "Job",
    "JobEvent",
    "MemoryEvent",
    "Metric",
    "ModelCall",
    "Plan",
    "Process",
    "Project",
    "ProjectRepo",
    "Provider",
    "Rule",
    "ScanReport",
    "Scenario",
    "ScenarioRun",
    "Setting",
    "TestResult",
    "Upload",
]
