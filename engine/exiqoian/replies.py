"""The fixed replies ExiQoian gives without calling a model (exact spec copy)."""
from typing import Literal

OFF_TOPIC_REPLY = (
    "I'm ExiQoian, and I only answer questions about this project: its TIBCO workflows, the "
    "modernized application, and the knowledge and memory from each phase. I can't help with that one.")
INJECTION_REPLY = (
    "I can't follow instructions that try to change how I work. Ask me about this project's "
    "workflows, the modernized application or one of its phases, and I'll answer from the "
    "project's memory.")
NOT_COVERED_REPLY = (
    "I couldn't find anything in this project's memory that answers that, so I won't guess. "
    "Try naming a process, an activity, a rule or a phase.")
INTRO_REPLY = (
    "I'm ExiQoian, this project's assistant. I answer from the project's memory: the TIBCO "
    "workflows, what each phase found and decided, and the modernized application, with a "
    "citation on every answer. I can't take actions or answer questions outside this project.")
UNAVAILABLE_REPLY = "Project memory is unavailable right now, so I can't answer. Try again shortly."
NOT_CONFIGURED_REPLY = "Project memory is not configured for this install, so I can't answer."

Verdict = Literal["answer", "intro", "off_topic", "injection", "not_covered", "unavailable"]

REPLY_FOR: dict[Verdict, str] = {
    "intro": INTRO_REPLY,
    "off_topic": OFF_TOPIC_REPLY,
    "injection": INJECTION_REPLY,
    "not_covered": NOT_COVERED_REPLY,
    "unavailable": UNAVAILABLE_REPLY,
}
