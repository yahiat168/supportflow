"""Knowledge subagent: retrieve product/policy evidence and write a grounded answer.
For the troubleshooting route it only retrieves, and hands evidence to the troubleshooting agent."""
from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from app.config import get_settings
from app.graph import llm
from app.graph.common import (NO_EVIDENCE_PREFIX, chunks_to_evidence, extractive_answer, format_evidence,
                              result_update, retrieval_to_state)
from app.graph.prompts import GROUNDED_ANSWER_SYSTEM
from app.graph.state import SubagentResult, SupportState
from app.observability.tracing import observation
from app.rag.schemas import SearchFilters
from app.tools.support_tools import search_knowledge_base

log = logging.getLogger("supportflow.knowledge")
TROUBLESHOOT_SOURCES = ["troubleshooting", "faq", "release_notes", "user_guide", "internal_guide"]
DEFAULT_QUESTION = "Could you tell me which CloudBox plan, feature, or setting your question is about?"


class GroundedAnswer(BaseModel):
    answer: str
    used_doc_ids: list[str] = Field(default_factory=list)
    insufficient_evidence: bool = False
    follow_up_question: str | None = None


def no_evidence_answer(question: str | None = None, missing: str | None = None) -> str:
    q = question or DEFAULT_QUESTION
    miss = f" {missing.rstrip('.')}." if missing else ""
    return f"{NO_EVIDENCE_PREFIX} to answer that reliably, so I don't want to guess.{miss} {q}"


def knowledge_node(state: SupportState) -> dict:
    route = state.get("route", "knowledge")
    query = state.get("standalone_query") or state["request"]
    filters = SearchFilters(source_type=TROUBLESHOOT_SOURCES) if route == "troubleshooting" else None
    with observation("knowledge_subagent", as_type="agent", input={"query": query, "route": route}) as obs:
        res = search_knowledge_base(query, filters, top_k=get_settings().retrieval_top_k)
        events = [res.event.model_dump()]
        if not res.ok:
            result = SubagentResult(
                agent="knowledge", status="error", summary=res.message, tool_events=events,
                draft_answer="I couldn't search the CloudBox knowledge base just now, so I can't give a sourced answer. "
                             "Please try again in a moment.",
                missing_information=["retry later"],
            )
            obs.update(output=result.summary, level="ERROR")
            return result_update(result, no_evidence=True, trajectory=["knowledge:search_failed"])
        retrieval = res.data
        evidence = chunks_to_evidence(retrieval.chunks)
        conflicts = [c.model_dump() for c in retrieval.conflicts]

        if route == "troubleshooting":
            result = SubagentResult(agent="knowledge", status="ok" if evidence else "no_evidence",
                                    summary=f"Retrieved {len(evidence)} troubleshooting chunks",
                                    evidence=evidence, tool_events=events)
            obs.update(output=result.summary)
            upd = result_update(result, trajectory=["knowledge:retrieve_for_troubleshooting"],
                                **retrieval_to_state(retrieval))
            upd.pop("draft_answer"); upd.pop("used_doc_ids"); upd.pop("missing_information")
            return upd

        if not evidence:
            result = SubagentResult(agent="knowledge", status="no_evidence", summary="No relevant evidence",
                                    tool_events=events, draft_answer=no_evidence_answer(),
                                    missing_information=[DEFAULT_QUESTION])
            obs.update(output=result.summary)
            return result_update(result, no_evidence=True, trajectory=["knowledge:no_evidence"],
                                 **retrieval_to_state(retrieval))

        superseded = {c["superseded_doc_id"] for c in conflicts}
        answer, used, insufficient, question = _compose(query, state, evidence, conflicts, superseded)
        if insufficient or not answer:
            result = SubagentResult(agent="knowledge", status="no_evidence", summary="Evidence did not answer the question",
                                    evidence=evidence, tool_events=events,
                                    draft_answer=no_evidence_answer(question),
                                    missing_information=[question or DEFAULT_QUESTION])
            obs.update(output=result.summary)
            return result_update(result, no_evidence=True, trajectory=["knowledge:insufficient"],
                                 **retrieval_to_state(retrieval))

        result = SubagentResult(agent="knowledge", status="ok", summary=f"Answered from {used}",
                                evidence=evidence, tool_events=events, draft_answer=answer, used_doc_ids=used)
        obs.update(output={"used_doc_ids": used, "conflicts": conflicts})
        return result_update(result, no_evidence=False, trajectory=["knowledge:answer"], **retrieval_to_state(retrieval))


def _conflict_note(conflicts: list[dict], evidence) -> str:
    titles = {e.doc_id: (e.title, e.version) for e in evidence}
    notes = []
    for c in conflicts:
        old = titles.get(c["superseded_doc_id"], (c["superseded_doc_id"], c["superseded_version"]))
        new = titles.get(c["preferred_doc_id"], (c["preferred_doc_id"], c["preferred_version"]))
        notes.append(f"Note: the older source \"{old[0]}\" (version {old[1]}) lists different details. "
                     f"I followed the newer official source \"{new[0]}\" (version {new[1]}); if your contract is on the "
                     f"older edition, please confirm with support.")
    return " ".join(notes)


def _compose(query, state, evidence, conflicts, superseded):
    settings = get_settings()
    if not settings.is_offline:
        try:
            history = "\n".join(f"{t['role']}: {t['content']}" for t in state.get("history", [])[-4:])
            out = llm.structured(
                GroundedAnswer, GROUNDED_ANSWER_SYSTEM,
                f"Conversation so far:\n{history or '(none)'}\n\nQuestion: {state['request']}\n"
                f"Standalone query: {query}\n\nEvidence:\n{format_evidence(evidence, conflicts)}",
                name="knowledge_answer",
            )
            valid = {e.doc_id for e in evidence}
            used = [d for d in out.used_doc_ids if d in valid]
            return out.answer, used, out.insufficient_evidence, out.follow_up_question
        except (llm.LLMUnavailable, llm.LLMBudgetExceeded) as exc:
            log.warning("knowledge LLM fallback: %s", exc)
    if not _offline_covers(query, evidence):
        return "", [], True, None
    focus = state.get("focus_query") or query
    answer, used = extractive_answer(focus, evidence, exclude_docs=superseded)
    if not answer and focus != query:
        answer, used = extractive_answer(query, evidence, exclude_docs=superseded)
    if answer and conflicts:
        answer = f"{answer}\n\n{_conflict_note(conflicts, evidence)}"
        used = list(dict.fromkeys(used + [c["preferred_doc_id"] for c in conflicts]))
    return answer, used, False, None


_GENERIC = {"cloudbox", "fil", "account", "plan", "support", "user", "use", "work", "need", "want", "know", "differ",
            "short", "answer", "question", "help", "change", "new", "many", "much", "read", "somewher", "heard",
            "think", "said", "say", "someon", "number", "way", "thing", "possibl", "abl", "mean"}


def _offline_covers(query: str, evidence) -> bool:
    """Offline sufficiency test: the specific (non-generic) terms of the question must appear in the evidence."""
    from app.rag.embeddings import tokenize

    specific = {t for t in tokenize(query) if t not in _GENERIC}
    if not specific:
        return True
    ev_tokens = set()
    for e in evidence:
        ev_tokens |= set(tokenize(e.content + " " + e.title))
    return len(specific & ev_tokens) / len(specific) >= 0.4
