"""The seven personas: phase order for source selection, one prompt line, starter questions."""
from typing import Literal

from pydantic import BaseModel

from engine.citations.model import UiPhase

PersonaKey = Literal["sme", "architect", "engineer", "qa", "ops", "admin", "viewer"]
PERSONA_ORDER: tuple[PersonaKey, ...] = ("sme", "architect", "engineer", "qa", "ops", "admin", "viewer")


class Persona(BaseModel):
    key: PersonaKey
    label: str
    phase_order: tuple[UiPhase, ...]
    prompt_line: str


def _p(key: PersonaKey, label: str, order: str, line: str) -> Persona:
    return Persona(key=key, label=label, phase_order=tuple(order.split()), prompt_line=line)  # type: ignore[arg-type]


PERSONAS: dict[PersonaKey, Persona] = {p.key: p for p in (
    _p("sme", "TIBCO SME", "validate discover design verify build deploy memory",
       "The reader is the TIBCO SME: lead with business rules, findings and process behaviour, name activities."),
    _p("architect", "Solution architect", "design validate build discover verify deploy memory",
       "The reader is the solution architect: lead with design decisions, mappings and their reasons."),
    _p("engineer", "Engineer", "build design verify validate discover deploy memory",
       "The reader is the engineer: lead with generated code, classes, methods, tests and build errors."),
    _p("qa", "QA lead", "verify build validate design discover deploy memory",
       "The reader is the QA lead: lead with scenarios, parity results, differences and the findings behind them."),
    _p("ops", "Operations", "deploy build verify design discover validate memory",
       "The reader is operations: lead with the deployment plan, infrastructure and configuration."),
    _p("admin", "Administrator", "discover validate design build verify deploy memory",
       "The reader administers the platform: give a balanced overview."),
    _p("viewer", "Viewer", "discover validate design build verify deploy memory",
       "The reader is a stakeholder with read-only access: use plain language and avoid code detail unless asked."),
)}


def default_persona(roles: frozenset[str]) -> PersonaKey:
    """The first held role in PERSONA_ORDER; ``viewer`` when none is held."""
    return next((k for k in PERSONA_ORDER if k in roles), "viewer")


class SuggestionNames(BaseModel):
    process: str | None = None  # the first process with an analysis
    rule_key: str | None = None  # its first rule key
    class_name: str | None = None  # its first generated class


def suggestions(persona: PersonaKey, names: SuggestionNames) -> list[str]:
    """Three starter questions templated with the project's names, generic when a name is missing."""
    proc = names.process or "this estate"
    rule = names.rule_key or "the business rules"
    cls = names.class_name or "the generated services"
    if persona == "sme":
        return [f"What does {proc} do and which rules apply to it?" if names.process else "What does this estate contain?",
                f"Which findings and rules were found for {proc}?" if names.process else "Which findings are open?",
                f"Where does rule {rule} apply?" if names.rule_key else "Which business rules were found?"]
    if persona == "architect":
        return [f"Why was {proc} designed the way it is?" if names.process else "Which design decisions were taken?",
                "Which mappings changed in the latest design?",
                f"How does the design map rule {rule}?" if names.rule_key else "Which gates are pending?"]
    if persona == "engineer":
        return [f"What does {cls} do?" if names.class_name else "Which classes were generated?",
                "Which build errors are open?",
                f"Which tests cover {cls}?" if names.class_name else "Which tests were generated?"]
    if persona == "qa":
        return ["Which parity scenarios failed?",
                f"Which scenarios cover {proc}?" if names.process else "What does verification cover?",
                f"How is rule {rule} tested?" if names.rule_key else "Which differences were found?"]
    if persona == "ops":
        return ["What does the deployment plan create?", "Which configuration does the service need?",
                f"What does {proc} depend on at runtime?" if names.process else "Which queues and tables are used?"]
    return [f"What does {proc} do?" if names.process else "What does this estate contain?",
            "What happened in each phase so far?",
            f"What was decided about {proc}?" if names.process else "Which decisions were made?"]
