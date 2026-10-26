from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from api.app.auth.roles import dev_principal
from api.app.models import AuditLog, ChatMessage
from api.app.services.exiqoian import (
    HISTORY_CHARS,
    HISTORY_MESSAGES,
    LEXICON_TTL_S,
    RATE_LIMIT,
    RATE_LIMITED,
    RATE_WINDOW_S,
    ask,
    check_rate,
    create_conversation,
    delete_conversation,
    get_conversation,
    held_personas,
    history_turns,
    list_conversations,
    list_messages,
    project_lexicon,
    resolve_persona,
)
from api.app.services.knowledge import KnowledgeUnavailable
from api.tests.test_analysis_gates import SYNTHETIC, _scanned, _wait_job
from engine.exiqoian.replies import NOT_CONFIGURED_REPLY, OFF_TOPIC_REPLY, UNAVAILABLE_REPLY
from engine.memory.graph import MemoryUnavailable
from engine.tests.fakes_memory import FakeRetriever, hit

SAMPLE_HITS = [
    hit("chunk:p:rule:R1", "Chunk", "analysis", 0.9, text="Dealer must be ACTIVE before funding. Other detail."),
    hit("chunk:p:design:decision:x", "Chunk", "design", 0.5, text="LookupDealer maps the dealer query."),
]
FAKE = FakeRetriever(hits=SAMPLE_HITS)
QUESTION = "What does DealerFundingRequest do with the funding request queue?"


class RaisingRetriever:
    def __init__(self, exc: Exception):
        self.exc = exc

    async def retrieve(self, *a, **kw):
        raise self.exc


@pytest.fixture
async def session(app):
    async with app.state.sessionmaker() as s:
        yield s


def msg(role: str, text: str, verdict: str | None = None) -> ChatMessage:
    return ChatMessage(role=role, text=text, verdict=verdict)


async def go(session, project_id, conv, owner, text, *, retriever=FAKE, persona="sme"):
    return await ask(session, retriever=retriever, provider=None, project_id=project_id, conversation_id=conv.id,
                     owner=owner, persona=persona, text=text, on_stage=None)


async def test_ask_stores_both_messages_and_one_audit_row(session, project_id):
    c = await create_conversation(session, project_id, "sme.vicky", "sme")
    user, bot = await go(session, project_id, c, "sme.vicky", "What does DealerFundingRequest do? ssn 123-45-6789")
    assert "123-45-6789" not in user.text and bot.verdict == "answer" and bot.cited["spans"]
    assert "[S" not in bot.text  # stored assistant text feeds the next prompt's history
    rows = (await session.execute(select(AuditLog).where(AuditLog.action == "exiqoian asked"))).scalars().all()
    assert len(rows) == 1 and rows[0].detail["verdict"] == "answer" and rows[0].detail["message_id"] == bot.id
    assert set(rows[0].detail) == {"conversation_id", "message_id", "verdict", "rule", "persona", "used_model", "provider",
                                   "sources", "cited", "dropped_sources", "question"}
    assert "123-45-6789" not in rows[0].detail["question"] and rows[0].project_id == project_id and rows[0].actor_kind == "user"
    conv = await get_conversation(session, project_id, "sme.vicky", c.id)
    assert conv.title.startswith("What does DealerFundingRequest") and "123-45" not in conv.title
    assert [m.role for m in await list_messages(session, c.id)] == ["user", "assistant"]


async def test_title_is_the_first_question_only_and_at_most_80_chars(session, project_id):
    c = await create_conversation(session, project_id, "sme.vicky", "sme")
    await go(session, project_id, c, "sme.vicky", "Describe the funding request queue " + "x" * 200)
    await go(session, project_id, c, "sme.vicky", "hi")
    assert len((await get_conversation(session, project_id, "sme.vicky", c.id)).title) == 80
    assert (await get_conversation(session, project_id, "sme.vicky", c.id)).title.startswith("Describe the funding")


async def test_declined_question_is_stored_and_audited_with_its_rule(session, project_id):
    c = await create_conversation(session, project_id, "sme.vicky", "sme")
    _, bot = await go(session, project_id, c, "sme.vicky", "what is 2+2")
    assert (bot.verdict, bot.text, bot.rule, bot.used_model) == ("off_topic", OFF_TOPIC_REPLY, "arithmetic", False)
    row = (await session.execute(select(AuditLog).where(AuditLog.action == "exiqoian asked"))).scalar_one()
    assert row.detail["rule"] == "arithmetic" and row.detail["sources"] == 0


async def test_conversations_are_owner_private(session, project_id):
    c = await create_conversation(session, project_id, "sme.vicky", "sme")
    with pytest.raises(HTTPException) as e:
        await get_conversation(session, project_id, "qa.priya", c.id)
    assert e.value.status_code == 404 and e.value.detail == "conversation not found"
    with pytest.raises(HTTPException):
        await go(session, project_id, c, "qa.priya", "hi")
    with pytest.raises(HTTPException):
        await delete_conversation(session, project_id, "qa.priya", c.id)
    assert await list_conversations(session, project_id, "qa.priya") == []
    assert [x.id for x in await list_conversations(session, project_id, "sme.vicky")] == [c.id]
    await delete_conversation(session, project_id, "sme.vicky", c.id)
    assert await list_conversations(session, project_id, "sme.vicky") == []


async def test_conversation_list_is_newest_first_and_capped_at_50(session, project_id):
    ids = [(await create_conversation(session, project_id, "sme.vicky", "sme")).id for _ in range(52)]
    got = await list_conversations(session, project_id, "sme.vicky")
    assert len(got) == 50 and got[0].id == ids[-1]


async def test_rate_limit_is_twelve_a_minute(session, project_id):
    assert (RATE_LIMIT, RATE_WINDOW_S, LEXICON_TTL_S) == (12, 60, 300) and HISTORY_MESSAGES == 6 and HISTORY_CHARS == 600
    c = await create_conversation(session, project_id, "qa.priya", "qa")
    for _ in range(12):
        await check_rate(session, "qa.priya")
        await go(session, project_id, c, "qa.priya", "hi", persona="qa")
    with pytest.raises(HTTPException) as e:
        await check_rate(session, "qa.priya")
    assert e.value.status_code == 429 and e.value.detail == RATE_LIMITED
    await check_rate(session, "sme.vicky")  # per user


@pytest.mark.parametrize("exc,reply,rule", [
    (MemoryUnavailable("ServiceUnavailable"), UNAVAILABLE_REPLY, "memory_unavailable"),
    (KnowledgeUnavailable("memory unavailable: ServiceUnavailable"), UNAVAILABLE_REPLY, "memory_unavailable"),
    (KnowledgeUnavailable("memory not configured"), NOT_CONFIGURED_REPLY, "not_configured"),
])
async def test_memory_unavailable_is_a_stored_unavailable_reply(session, project_id, exc, reply, rule):
    c = await create_conversation(session, project_id, "sme.vicky", "sme")
    _, bot = await go(session, project_id, c, "sme.vicky", QUESTION, retriever=RaisingRetriever(exc))
    assert bot.verdict == "unavailable" and bot.text == reply and bot.rule == rule
    row = (await session.execute(select(AuditLog).where(AuditLog.action == "exiqoian asked"))).scalar_one()
    assert row.detail["verdict"] == "unavailable"


async def test_lexicon_holds_project_names_and_parts(client, app):
    pid, procs = await _scanned(client)
    async with app.state.sessionmaker() as session:
        lex, ids, names = await project_lexicon(session, pid)
    assert {"dealerfundingrequest", "dealer", "funding", "tibco", "dotnet"} <= lex and names.process
    assert set(ids.values()) == {p["id"] for p in procs.values()}


async def test_lexicon_is_cached_per_project(client, app):
    pid, _ = await _scanned(client)
    async with app.state.sessionmaker() as session:
        first = await project_lexicon(session, pid)
        assert await project_lexicon(session, pid) is first


def test_history_is_last_six_masked_and_skips_declines():
    msgs = [msg("user", f"q{i} " + "x" * 700) for i in range(4)] + [msg("assistant", OFF_TOPIC_REPLY, verdict="off_topic")] \
        + [msg("assistant", "a ssn 123-45-6789", verdict="answer")] + [msg("user", "q5"), msg("assistant", "a5", verdict="answer")]
    turns = history_turns(msgs)
    assert len(turns) == 6 and all(len(t.text) <= 600 for t in turns)
    assert OFF_TOPIC_REPLY not in [t.text for t in turns] and not any("123-45-6789" in t.text for t in turns)
    assert [t.text[:2] for t in turns][:3] == ["q0", "q1", "q2"]  # q3, the declined question, is gone


def test_history_masks_before_cutting_and_never_carries_markers():
    turns = history_turns([msg("user", "x" * 595 + " 123-45-6789"), msg("assistant", "Fact [S1] holds [S2, S3].", "answer")])
    assert "123-45" not in turns[0].text and "6789" not in turns[0].text
    assert "[S" not in turns[1].text


async def test_follow_up_uses_stored_history(session, project_id):
    c = await create_conversation(session, project_id, "sme.vicky", "sme")
    retriever = FakeRetriever(hits=SAMPLE_HITS)
    await go(session, project_id, c, "sme.vicky", QUESTION, retriever=retriever)
    _, bot = await go(session, project_id, c, "sme.vicky", "and its tests?", retriever=retriever)
    assert bot.verdict == "answer" and "DealerFundingRequest" in retriever.calls[-1].query


def test_personas_held_and_resolved():
    ops = dev_principal("ops.sam")
    assert held_personas(ops) == ["ops", "admin"] and resolve_persona(ops, None) == "ops"
    assert resolve_persona(ops, "admin") == "admin"
    with pytest.raises(HTTPException) as e:
        resolve_persona(ops, "sme")
    assert e.value.status_code == 403 and e.value.detail == "persona not held"
    assert held_personas(dev_principal("nobody")) == ["viewer"] and resolve_persona(dev_principal("viewer.val"), None) == "viewer"


async def test_rate_limit_holds_under_concurrency(app, project_id):
    async with app.state.sessionmaker() as s:
        c = await create_conversation(s, project_id, "qa.priya", "qa")

    async def one(i: int):
        async with app.state.sessionmaker() as s:
            try:
                await go(s, project_id, c, "qa.priya", f"hi {i}", persona="qa")
                return "ok"
            except HTTPException as e:
                return e.status_code

    results = await asyncio.gather(*(one(i) for i in range(16)))
    assert results.count("ok") == 12 and results.count(429) == 4


async def test_user_row_is_committed_before_the_answer_is_computed(app, project_id):
    seen = {}

    class Spy:
        async def retrieve(self, *a, **kw):
            async with app.state.sessionmaker() as other:
                seen["rows"] = len((await other.execute(select(ChatMessage))).scalars().all())
            return []

    async with app.state.sessionmaker() as s:
        c = await create_conversation(s, project_id, "sme.vicky", "sme")
        await go(s, project_id, c, "sme.vicky", QUESTION, retriever=Spy())
    assert seen["rows"] == 1


async def test_an_unexpected_failure_is_stored_audited_and_counted(session, project_id):
    c = await create_conversation(session, project_id, "sme.vicky", "sme")
    _, bot = await go(session, project_id, c, "sme.vicky", QUESTION, retriever=RaisingRetriever(RuntimeError("boom")))
    assert (bot.verdict, bot.text, bot.rule) == ("unavailable", UNAVAILABLE_REPLY, "error")
    assert len((await session.execute(select(AuditLog).where(AuditLog.action == "exiqoian asked"))).scalars().all()) == 1


async def test_lexicon_refreshes_when_the_project_changes_under_the_cache(client, app):
    pid = (await client.post("/projects", json={"name": "late"})).json()["id"]
    async with app.state.sessionmaker() as session:
        before, _, names = await project_lexicon(session, pid)
    assert "dealerfundingrequest" not in before and names.process is None
    await client.post(f"/projects/{pid}/upload", json={"path": str(SYNTHETIC)})
    job = (await client.post(f"/projects/{pid}/scan", json={})).json()["job_id"]
    await _wait_job(client, job)
    async with app.state.sessionmaker() as session:
        after, _, names = await project_lexicon(session, pid)
    assert "dealerfundingrequest" in after and names.process


async def test_lexicon_parts_skip_stopwords_and_short_words(client, app):
    """`MapToResponse` must not make "to" a project word: it would switch off the off-topic filter."""
    from engine.memory.search import query_terms
    pid, _ = await _scanned(client)
    async with app.state.sessionmaker() as session:
        lex, _, _ = await project_lexicon(session, pid)
    from engine.knowledge.embeddings import STOPWORDS
    extra = {w for w in lex if (w in STOPWORDS or len(w) < 3) and w not in {"bw", "sql", "jms"}}
    assert extra == set(), extra
    assert query_terms("how to cook dosa") and "to" not in lex


async def test_deleting_the_project_removes_its_conversations_and_messages(session, project_id):
    """The runbook says so: both chat tables cascade from the project."""
    from sqlalchemy import delete, func

    from api.app.models import ChatConversation, Project
    c = await create_conversation(session, project_id, "sme.vicky", "sme")
    await go(session, project_id, c, "sme.vicky", "hi")
    await session.execute(delete(Project).where(Project.id == project_id))
    await session.commit()
    for model in (ChatConversation, ChatMessage):
        assert (await session.execute(select(func.count()).select_from(model))).scalar_one() == 0
