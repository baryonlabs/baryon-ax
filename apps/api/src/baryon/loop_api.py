"""HTTP API of the question–certification loop (loop.py). main.py builds it with its services.

    POST /v1/loop/actions                 record a decision or request (who, what, situation) — the asker's own
    POST /v1/loop/actions/retract         take back the actions recorded from one message (edited or deleted)
    POST /v1/loop/threads/understand      read one Slack thread snapshot: purpose, requests, decisions, claims, contests
                                          (thread_understanding.py). Nothing is stored.
    POST /v1/loop/threads/merge           merge a thread reading into the ontology (ontology_merge): one change set
                                          {added, merged, refined, contested, relations, stubs}, applied at once;
                                          the same thread again changes nothing (agent/admin)
    POST /v1/loop/rounds                  run a round: ontology from new documents, gap judgment, questions (admin/agent)
    GET  /v1/loop/questions               open questions for me (or all, admin)
    POST /v1/loop/questions/{id}/answer   pick an expected answer (+ correction), or "모두 틀림" with my own words
    POST /v1/loop/questions/{id}/skip     not now (asked again next round)
    GET  /v1/loop/items                   knowledge items (terms and criteria) with their state
    GET  /v1/loop/items/{id}              one item with the source text to compare against
    POST /v1/loop/items/{id}/verify       draft → verified (agent's first-source check)
    POST /v1/loop/items/{id}/decision     approve (certify) | hold | reject — people only, gate enforced here
    GET  /v1/loop/decision-kb             certified items only
    GET  /v1/loop/ontology                ontology.json (terms / relations / questions)
    GET  /v1/loop/ossie                   Apache Ossie model of the same
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import loop, ontology_merge, thread_understanding
from .loop import CERTIFIED, DRAFT, VERIFIED, LoopError, LoopStore


@dataclass
class LoopDeps:
    principal: Callable[[str | None, str | None, str | None], Any]  # principal_from_headers
    acting_service: Callable[[], str | None]
    documents: Any                                                   # DocumentService
    audit: Any
    complete: Callable[[list[dict[str, str]]], Awaitable[str]]
    model_ready: Callable[[], bool]
    publish: Callable[[Any, str, str, str], Awaitable[str]]          # (principal, filename, markdown, trace_id) → document id


class ActionBody(BaseModel):
    text: str = Field(min_length=4, max_length=1000)
    context: str = Field("", max_length=4000)
    source: str = Field("api", max_length=40)       # slack | secretary | web | api
    source_ref: str = Field("", max_length=300)     # e.g. slack:C1:1712.33 (where to ask back)
    message_ref: str = Field("", max_length=300)    # the message it was read from (slack:C1:1712.40), to retract it
    judge: bool = False                              # judge now instead of at the next round


class RetractBody(BaseModel):
    """The message was edited or deleted: what it stated is no longer true (its actions are retracted, their open
    questions withdrawn). ``keep_texts``: what the edited message still states (action text, spaces removed)."""
    message_ref: str = Field(min_length=3, max_length=300)
    keep_texts: list[str] = Field(default_factory=list, max_length=50)
    reason: str = Field("edited", max_length=40)


class ThreadBody(BaseModel):
    """A thread snapshot (examples/slack-bot thread_snapshot.understanding_payload); messages are checked by the reader."""
    channel: str = Field("", max_length=64)
    thread_ts: str = Field("", max_length=32)
    is_dm: bool = False
    me: dict[str, Any] = Field(default_factory=dict)
    owners: list[str] = Field(default_factory=list, max_length=50)
    messages: list[dict[str, Any]] = Field(default_factory=list, max_length=2000)
    interactions: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    use_model: bool = True                           # false: the deterministic rules only


class MergeBody(BaseModel):
    """What to merge into the organization's ontology: a thread reading (POST /threads/understand) or a snapshot."""
    understanding: dict[str, Any] | None = None
    thread: ThreadBody | None = None
    use_model: bool = True                           # false: rules only (same-as and the thread reading)
    dry_run: bool = False                            # the change set without writing it


class RoundBody(BaseModel):
    documents: int = Field(5, ge=0, le=50)
    chunks_per_document: int = Field(4, ge=1, le=20)
    actions: int = Field(20, ge=0, le=200)


class AnswerBody(BaseModel):
    choice: int | None = Field(None, ge=0, le=2)
    none_correct: bool = False
    text: str = Field("", max_length=1000)


class DecisionBody(BaseModel):
    decision: str
    reason: str = Field("", max_length=1000)
    checked: list[int] = Field(default_factory=list, max_length=50)  # indexes of the sources compared
    contest_choice: int | None = None
    contest_reason: str = Field("", max_length=1000)


AGENT_ROLES = {"service", "agent"}


def build_router(deps: LoopDeps, store: LoopStore | None = None) -> APIRouter:
    store = store or LoopStore()
    router = APIRouter(prefix="/v1/loop", tags=["loop"])
    agents = {item.strip() for item in os.getenv("LOOP_AGENT_USERS", "slack-bot,discord-bot,baryon-agent").split(",") if item.strip()}

    def who(x_user_id, x_organization_id, x_roles):
        return deps.principal(x_user_id, x_organization_id, x_roles)

    def is_person(principal) -> bool:
        """A person, not an agent: no service/agent role, not a bot acting as itself."""
        acting = deps.acting_service()
        if principal.roles & AGENT_ROLES or principal.user_id in agents:
            return False
        return not (acting and acting == principal.user_id)

    def fail(error: LoopError):
        raise HTTPException(error.status, {"code": error.code, "message": str(error)})

    def item_of(data: dict, item_id: str) -> dict:
        item = data["terms"].get(item_id)
        if not item:
            raise HTTPException(404, "Knowledge item not found")
        return item

    # ---------------------------------------------------------------- actions and rounds

    @router.post("/actions")
    async def record_action(body: ActionBody, x_user_id: str | None = Header(None), x_organization_id: str | None = Header(None),
                            x_roles: str | None = Header(None)) -> dict:
        principal = who(x_user_id, x_organization_id, x_roles)
        action = {"id": loop._id("a"), "text": body.text.strip(), "context": body.context.strip(), "actor": principal.user_id,
                  "source": body.source, "source_ref": body.source_ref, "status": "new", "created_at": loop.now(),
                  **({"message_ref": body.message_ref} if body.message_ref else {})}
        store.update(principal.organization_id, lambda data: data["actions"].__setitem__(action["id"], action))
        deps.audit.emit("loop.action_recorded", principal, str(uuid.uuid4()), action_id=action["id"], source=body.source)
        if body.judge:
            await judge_actions(principal, [action["id"]])
            action = store.load(principal.organization_id)["actions"][action["id"]]
            question = next((q for q in store.load(principal.organization_id)["questions"].values() if q["action_id"] == action["id"]), None)
            return {**action, "question": question}
        return action

    @router.post("/actions/retract")
    async def retract_actions(body: RetractBody, x_user_id: str | None = Header(None), x_organization_id: str | None = Header(None),
                              x_roles: str | None = Header(None)) -> dict:
        """Retract the actions recorded from one message: an admin or agent (the Slack bot) for anyone's, a person
        for their own. Questions still open on them are withdrawn; answered ones and certified items stay."""
        principal = who(x_user_id, x_organization_id, x_roles)
        anyone = "admin" in principal.roles or bool(principal.roles & AGENT_ROLES)
        keep = {re.sub(r"\s+", "", text) for text in body.keep_texts}

        def change(data: dict) -> list[str]:
            ids = []
            for action in data["actions"].values():
                if action.get("message_ref") != body.message_ref or action.get("status") == "retracted":
                    continue
                if not anyone and action.get("actor") != principal.user_id:
                    continue
                if re.sub(r"\s+", "", action.get("text", "")) in keep:
                    continue
                action.update(status="retracted", retracted_at=loop.now(), retracted_reason=body.reason)
                ids.append(action["id"])
            for question in data["questions"].values():
                if question.get("action_id") in ids and question.get("status") == "open":
                    question["status"] = "withdrawn"
            return ids

        ids = store.update(principal.organization_id, change)
        if ids:
            deps.audit.emit("loop.actions_retracted", principal, str(uuid.uuid4()), action_ids=ids, reason=body.reason)
        return {"retracted": len(ids), "ids": ids}

    @router.post("/threads/understand")
    async def understand_thread(body: ThreadBody, x_user_id: str | None = Header(None), x_organization_id: str | None = Header(None),
                                x_roles: str | None = Header(None)) -> dict:
        principal = who(x_user_id, x_organization_id, x_roles)
        complete = deps.complete if body.use_model and deps.model_ready() else None
        result = await thread_understanding.understand(body.model_dump(), complete)
        deps.audit.emit("loop.thread_understood", principal, str(uuid.uuid4()), method=result["method"],
                        messages=len(body.messages), decisions=len(result["decisions"]), dropped=result["dropped"])
        return result

    @router.post("/threads/merge")
    async def merge_thread(body: MergeBody, x_user_id: str | None = Header(None), x_organization_id: str | None = Header(None),
                           x_roles: str | None = Header(None)) -> dict:
        principal = who(x_user_id, x_organization_id, x_roles)
        if "admin" not in principal.roles and not principal.roles & AGENT_ROLES:
            raise HTTPException(403, "Administrator or agent role required")
        complete = deps.complete if body.use_model and deps.model_ready() else None
        if body.understanding is not None:
            result = body.understanding
        elif body.thread is not None:
            result = await thread_understanding.understand(body.thread.model_dump(), complete)
        else:
            raise HTTPException(422, "understanding or thread is required")
        claims, relations, _types = ontology_merge.items_from_understanding(result)
        same_as = await ontology_merge.same_as_for(store.load(principal.organization_id),
                                                   ontology_merge.labels_of(claims, relations), complete)

        def change(data: dict) -> ontology_merge.ChangeSet:
            return ontology_merge.merge(data, claims, relations, same_as=same_as, write=not body.dry_run)

        changes = change(store.load(principal.organization_id)) if body.dry_run else store.update(principal.organization_id, change)
        deps.audit.emit("loop.thread_merged", principal, str(uuid.uuid4()), dry_run=body.dry_run, **changes.counts())
        return {"channel": result.get("channel", ""), "thread_ts": result.get("thread_ts", ""), "method": result.get("method", ""),
                "dry_run": body.dry_run, **{k: v for k, v in changes.to_dict().items()}}

    async def judge_actions(principal, action_ids: list[str]) -> list[dict]:
        data = store.load(principal.organization_id)
        complete = deps.complete if deps.model_ready() else None
        outcomes = []
        for action_id in action_ids:
            action = data["actions"].get(action_id)
            if not action or action["status"] != "new":
                continue
            terms = loop.related_terms(data, action["text"] + " " + action.get("context", ""))
            try:
                passages = deps.documents.search(principal, action["text"], 3)
            except Exception:  # noqa: BLE001 - search trouble is not a reason to skip judging
                passages = []
            try:
                verdict = await loop.judge_gap(action, terms, passages, complete)
                question = None if verdict["explained"] else await loop.make_question(action, terms, complete)
            except Exception as error:  # noqa: BLE001 - the model failed: try again next round
                outcomes.append({"action_id": action_id, "error": type(error).__name__})
                continue
            outcomes.append({"action_id": action_id, "verdict": verdict, "question": question,
                             "terms": [term["id"] for term in terms]})

        def apply(fresh: dict) -> list[dict]:
            created = []
            for outcome in outcomes:
                action = fresh["actions"].get(outcome["action_id"])
                if not action or "verdict" not in outcome:
                    continue
                action["verdict"] = outcome["verdict"]
                action["related_terms"] = outcome["terms"]
                if outcome["question"] is None:
                    action["status"] = "explained"
                    continue
                question = {"id": loop._id("q"), "action_id": action["id"], "asked_to": action["actor"],
                            "source_ref": action.get("source_ref", ""), "status": "open", "created_at": loop.now(),
                            "asked_count": 0, **outcome["question"]}
                fresh["questions"][question["id"]] = question
                action["status"] = "asked"
                created.append(question)
            return created

        created = store.update(principal.organization_id, apply)
        for question in created:
            deps.audit.emit("loop.question_created", principal, str(uuid.uuid4()), question_id=question["id"], action_id=question["action_id"])
        return created

    @router.post("/rounds")
    async def run_round(body: RoundBody | None = None, x_user_id: str | None = Header(None), x_organization_id: str | None = Header(None),
                        x_roles: str | None = Header(None)) -> dict:
        principal = who(x_user_id, x_organization_id, x_roles)
        if "admin" not in principal.roles and not principal.roles & AGENT_ROLES:
            raise HTTPException(403, "Administrator or agent role required")
        body = body or RoundBody()
        data = store.load(principal.organization_id)
        number = len(data["rounds"]) + 1
        summary: dict[str, Any] = {"round": number, "started_at": loop.now(), "documents": 0, "terms": 0, "relations": 0,
                                   "contested": 0, "questions": 0, "explained": 0, "errors": 0}
        # ① ontology: documents are read a few chunks per round, continuing where the last round stopped, until the
        # whole document is covered (data["read_progress"]: document id → created_at, next chunk, total).
        extracted: list[tuple[dict, int, dict]] = []
        if deps.model_ready() and body.documents:
            progress = data.setdefault("read_progress", {})
            # The ontology is shared by the whole organization, so it is built only from documents every member can
            # read; restricted documents would leak through term definitions.
            reader = deps.principal("loop-reader", principal.organization_id, "member")
            waiting = []
            for doc in deps.documents.list_for(reader):
                if "member" not in (doc.get("allowed_roles") or []) or "loop" in (doc.get("tags") or []):
                    continue
                state = progress.get(doc["id"])
                if state and state.get("created_at") == doc.get("created_at") and state.get("done"):
                    continue
                if data["seen_documents"].get(doc["id"]) == doc.get("created_at") and not state:
                    state = {"created_at": doc.get("created_at"), "next": body.chunks_per_document, "done": False}  # read by an older round
                if not state or state.get("created_at") != doc.get("created_at"):
                    state = {"created_at": doc.get("created_at"), "next": 0, "done": False}
                waiting.append((doc, state))
            waiting.sort(key=lambda pair: pair[1]["next"])  # new documents first, then the ones half read
            for listed, state in waiting[: body.documents]:
                document = deps.documents.documents.get(listed["id"])
                if not document:
                    continue
                chunks = [str(item.get("text") or "") for item in document.chunks] or [document.content]
                start = int(state.get("next") or 0)
                for index in range(start, min(start + body.chunks_per_document, len(chunks))):
                    text = chunks[index]
                    if len(text.strip()) < 40:
                        continue
                    try:
                        extracted.append((listed, index + 1, await loop.extract_from_chunk(listed, index + 1, text, deps.complete)))
                    except Exception:  # noqa: BLE001
                        summary["errors"] += 1
                state["next"] = min(start + body.chunks_per_document, len(chunks))
                state["total"] = len(chunks)
                state["done"] = state["next"] >= len(chunks)
                progress[listed["id"]] = state
                summary["documents"] += 1
                if state["done"]:
                    data["seen_documents"][listed["id"]] = listed.get("created_at")
            summary["documents_waiting"] = sum(1 for _, state in waiting if not state.get("done"))

        progress_now = dict(data.get("read_progress") or {})
        seen_now = dict(data["seen_documents"])

        claims, relations = [], []
        for listed, chunk, result in extracted:
            for term in result["terms"]:
                source = {"type": "document", "document_id": listed["id"], "title": listed.get("title", ""), "chunk": chunk,
                          "quote": term["quote"], "matched": term["verified"]}
                claims.append(ontology_merge.document_claim(term["label"], term["definition"], source, term["verified"]))
            for rel in result["relations"]:
                relations.append(ontology_merge.Relation(s=rel["s"], p=rel["p"], o=rel["o"], source={
                    "type": "document", "document_id": listed["id"], "chunk": chunk, "quote": rel.get("quote", "")}))
        same_as = {}
        if claims and deps.model_ready():
            same_as = await ontology_merge.same_as_for(store.load(principal.organization_id),
                                                       ontology_merge.labels_of(claims, relations), deps.complete)

        def apply(fresh: dict) -> None:
            fresh["seen_documents"].update(seen_now)
            fresh.setdefault("read_progress", {}).update(progress_now)
            # one merger for every source (ontology_merge): candidates, same / refine / contest, stub ends, one change set
            changes = ontology_merge.merge(fresh, claims, relations, same_as=same_as, round_number=number)
            summary["terms"] += len(changes.added)
            summary["contested"] += len(changes.contested)
            summary["relations"] += len(changes.relations)
            summary.update(merged=len(changes.merged), refined=len(changes.refined), stubs=len(changes.stubs))

        store.update(principal.organization_id, apply)
        # ②–④ actions waiting for judgment
        pending = [a["id"] for a in sorted(store.load(principal.organization_id)["actions"].values(), key=lambda a: a["created_at"])
                   if a["status"] == "new"][: body.actions]
        created = await judge_actions(principal, pending)
        summary["questions"] = len(created)
        after = store.load(principal.organization_id)["actions"]
        summary["explained"] = sum(1 for action_id in pending if after.get(action_id, {}).get("status") == "explained")
        summary["finished_at"] = loop.now()
        store.update(principal.organization_id, lambda fresh: fresh["rounds"].append(summary))
        deps.audit.emit("loop.round", principal, str(uuid.uuid4()), **{k: v for k, v in summary.items() if isinstance(v, int)})
        return summary

    # ---------------------------------------------------------------- questions

    @router.get("/questions")
    async def list_questions(status: str = Query("open"), everyone: bool = Query(False), x_user_id: str | None = Header(None),
                             x_organization_id: str | None = Header(None), x_roles: str | None = Header(None)) -> dict:
        principal = who(x_user_id, x_organization_id, x_roles)
        data = store.load(principal.organization_id)
        show_all = everyone and ("admin" in principal.roles or principal.roles & AGENT_ROLES)
        rows = [{**q, "action": data["actions"].get(q["action_id"], {})} for q in data["questions"].values()
                if (status == "all" or q["status"] == status) and (show_all or q["asked_to"] == principal.user_id)]
        return {"data": sorted(rows, key=lambda q: q["created_at"])}

    @router.post("/questions/{question_id}/asked")
    async def mark_asked(question_id: str, x_user_id: str | None = Header(None), x_organization_id: str | None = Header(None),
                         x_roles: str | None = Header(None)) -> dict:
        """A front end showed the question to its person (so it is not shown twice in one round)."""
        principal = who(x_user_id, x_organization_id, x_roles)

        def change(data: dict) -> dict:
            question = data["questions"].get(question_id)
            if not question:
                raise HTTPException(404, "Question not found")
            question["asked_count"] = int(question.get("asked_count") or 0) + 1
            question["last_asked_at"] = loop.now()
            return question

        return store.update(principal.organization_id, change)

    @router.post("/questions/{question_id}/answer")
    async def answer(question_id: str, body: AnswerBody, x_user_id: str | None = Header(None), x_organization_id: str | None = Header(None),
                     x_roles: str | None = Header(None)) -> dict:
        principal = who(x_user_id, x_organization_id, x_roles)
        if not is_person(principal):
            raise HTTPException(403, {"code": "forbidden_role", "message": "질문에는 사람이 답합니다"})

        def change(data: dict) -> dict:
            question = data["questions"].get(question_id)
            if not question:
                raise HTTPException(404, "Question not found")
            if question["asked_to"] != principal.user_id and "admin" not in principal.roles:
                raise HTTPException(403, "This question was asked to someone else")
            if question["status"] == "answered":
                raise HTTPException(409, "Already answered")
            try:
                statement = loop.answer_text(question, body.choice, body.none_correct, body.text)
            except LoopError as error:
                fail(error)
            action = data["actions"][question["action_id"]]
            item = loop.criterion_item(question, action, statement, principal.user_id)
            # The agent's check: the item's sources are the recorded action and this answer, word for word.
            item = loop.verify(item, "agent:source-match")
            data["terms"][item["id"]] = item
            question.update(status="answered", answered_at=loop.now(), item_id=item["id"],
                            answer={"choice": body.choice, "none_correct": body.none_correct, "text": body.text, "statement": statement})
            action["status"] = "answered"
            return item

        item = store.update(principal.organization_id, change)
        deps.audit.emit("loop.question_answered", principal, str(uuid.uuid4()), question_id=question_id, item_id=item["id"],
                        choice=body.choice, none_correct=body.none_correct, corrected=bool(body.text.strip()))
        return item

    @router.post("/questions/{question_id}/skip")
    async def skip(question_id: str, x_user_id: str | None = Header(None), x_organization_id: str | None = Header(None),
                   x_roles: str | None = Header(None)) -> dict:
        principal = who(x_user_id, x_organization_id, x_roles)

        def change(data: dict) -> dict:
            question = data["questions"].get(question_id)
            if not question or (question["asked_to"] != principal.user_id and "admin" not in principal.roles):
                raise HTTPException(404, "Question not found")
            question["skipped"] = int(question.get("skipped") or 0) + 1  # stays open: asked again next round
            return question

        return store.update(principal.organization_id, change)

    # ---------------------------------------------------------------- items and certification

    @router.get("/items")
    async def list_items(status: str | None = Query(None), kind: str | None = Query(None), mine: bool = Query(False),
                         x_user_id: str | None = Header(None), x_organization_id: str | None = Header(None),
                         x_roles: str | None = Header(None)) -> dict:
        principal = who(x_user_id, x_organization_id, x_roles)
        items = [item for item in store.load(principal.organization_id)["terms"].values()
                 if (not status or item["status"] == status) and (not kind or item.get("kind") == kind)
                 and (not mine or item.get("answered_by") == principal.user_id)]
        return {"data": sorted(items, key=lambda item: item.get("created_at", ""), reverse=True)}

    def with_source_text(principal, item: dict) -> dict:
        """The certification screen compares each claim with its primary source, so send the source text along."""
        sources = []
        for source in item.get("sources") or []:
            shown = dict(source)
            if source.get("type") == "document":
                document = deps.documents.documents.get(source.get("document_id", ""))
                if document and deps.documents.can_view(principal, document):
                    chunks = [str(c.get("text") or "") for c in document.chunks] or [document.content]
                    index = int(source.get("chunk") or 1) - 1
                    shown["text"] = chunks[index] if 0 <= index < len(chunks) else ""
                else:
                    shown["text"] = ""
                    shown["unavailable"] = True
            else:
                shown["text"] = source.get("quote", "")
            sources.append(shown)
        return {**item, "sources": sources, "contested": loop.is_contested(item)}

    @router.get("/items/{item_id}")
    async def get_item(item_id: str, x_user_id: str | None = Header(None), x_organization_id: str | None = Header(None),
                       x_roles: str | None = Header(None)) -> dict:
        principal = who(x_user_id, x_organization_id, x_roles)
        return with_source_text(principal, item_of(store.load(principal.organization_id), item_id))

    @router.post("/items/{item_id}/verify")
    async def verify_item(item_id: str, x_user_id: str | None = Header(None), x_organization_id: str | None = Header(None),
                          x_roles: str | None = Header(None)) -> dict:
        principal = who(x_user_id, x_organization_id, x_roles)

        def change(data: dict) -> dict:
            item = item_of(data, item_id)
            if item.get("kind") == "term" and not any(source.get("matched") for source in item.get("sources") or []):
                fail(LoopError("source_mismatch", "근거 문장이 출처 원문에 없어 확인됨으로 올릴 수 없습니다"))
            try:
                data["terms"][item_id] = loop.verify(item, principal.user_id)
            except LoopError as error:
                fail(error)
            return data["terms"][item_id]

        return store.update(principal.organization_id, change)

    @router.post("/items/{item_id}/decision")
    async def decision(item_id: str, body: DecisionBody, x_user_id: str | None = Header(None), x_organization_id: str | None = Header(None),
                       x_roles: str | None = Header(None)) -> dict:
        principal = who(x_user_id, x_organization_id, x_roles)
        person = is_person(principal)
        session = {"checked": body.checked, "contest": {"choice": body.contest_choice, "reason": body.contest_reason}}
        trace_id = str(uuid.uuid4())

        def change(data: dict) -> dict:
            item = item_of(data, item_id)
            if not person:
                deps.audit.emit("loop.decision_refused", principal, trace_id, item_id=item_id, decision=body.decision, code="forbidden_role")
                fail(LoopError("forbidden_role", "인증 결정은 사람만 내릴 수 있습니다", 403))
            if item.get("kind") == "criterion" and item.get("answered_by") != principal.user_id and "admin" not in principal.roles:
                raise HTTPException(403, "Only the person who answered, or an administrator, can certify this criterion")
            try:
                data["terms"][item_id] = loop.decide(item, principal.user_id, person, body.decision, body.reason, session)
            except LoopError as error:
                deps.audit.emit("loop.decision_refused", principal, trace_id, item_id=item_id, decision=body.decision, code=error.code)
                fail(error)
            return data["terms"][item_id]

        item = store.update(principal.organization_id, change)
        deps.audit.emit("loop.decision", principal, trace_id, item_id=item_id, decision=body.decision,
                        status=item["status"], checked=sorted(set(body.checked)))
        if item["status"] == CERTIFIED:
            # Certified knowledge becomes a document, so answers can cite it like any other source.
            markdown = render_certified(item)
            filename = "certified-" + re.sub(r"[^0-9A-Za-z가-힣_-]", "-", item["label"])[:60] + ".md"
            try:
                document_id = await deps.publish(principal, filename, markdown, trace_id)
                item = store.update(principal.organization_id, lambda data: data["terms"][item_id].update(document_id=document_id)
                                    or data["terms"][item_id])
            except Exception:  # noqa: BLE001 - certification stands even if indexing failed; the item is in the KB API
                pass
        return item

    @router.get("/decision-kb")
    async def decision_kb(x_user_id: str | None = Header(None), x_organization_id: str | None = Header(None),
                          x_roles: str | None = Header(None)) -> dict:
        principal = who(x_user_id, x_organization_id, x_roles)
        return {"data": [item for item in store.load(principal.organization_id)["terms"].values() if item["status"] == CERTIFIED]}

    @router.get("/ontology")
    async def ontology(certified_only: bool = Query(False), x_user_id: str | None = Header(None),
                       x_organization_id: str | None = Header(None), x_roles: str | None = Header(None)) -> dict:
        principal = who(x_user_id, x_organization_id, x_roles)
        return loop.ontology_json(store.load(principal.organization_id), certified_only)

    @router.get("/ossie")
    async def ossie(x_user_id: str | None = Header(None), x_organization_id: str | None = Header(None), x_roles: str | None = Header(None)):
        principal = who(x_user_id, x_organization_id, x_roles)
        model = loop.ossie_model(store.load(principal.organization_id), principal.organization_id)
        return JSONResponse(model, headers={"Content-Disposition": 'attachment; filename="baryon-loop.ossie.json"'})

    @router.get("/stats")
    async def stats(x_user_id: str | None = Header(None), x_organization_id: str | None = Header(None), x_roles: str | None = Header(None)) -> dict:
        principal = who(x_user_id, x_organization_id, x_roles)
        data = store.load(principal.organization_id)
        items = list(data["terms"].values())
        count = lambda values, key, value: sum(1 for v in values if v.get(key) == value)  # noqa: E731
        return {"items": {state: count(items, "status", state) for state in (DRAFT, VERIFIED, CERTIFIED)},
                "contested": sum(1 for item in items if loop.is_contested(item)),
                "criteria": count(items, "kind", "criterion"), "relations": len(data["relations"]),
                "actions": {state: count(data["actions"].values(), "status", state) for state in ("new", "explained", "asked", "answered")},
                "questions": {state: count(data["questions"].values(), "status", state) for state in ("open", "answered")},
                "ontology_coverage": {"documents_done": sum(1 for v in (data.get("read_progress") or {}).values() if v.get("done")),
                                      "documents_partial": sum(1 for v in (data.get("read_progress") or {}).values() if not v.get("done")),
                                      "chunks_read": sum(int(v.get("next") or 0) for v in (data.get("read_progress") or {}).values()),
                                      "chunks_total": sum(int(v.get("total") or 0) for v in (data.get("read_progress") or {}).values())},
                "rounds": len(data["rounds"]), "last_round": data["rounds"][-1] if data["rounds"] else None}

    return router


def render_certified(item: dict) -> str:
    lines = [f"# {item['label']}", "", f"> 인증된 지식 · {item.get('certified_by', '')} · {item.get('certified_at', '')[:10]}", "",
             item["definition"], ""]
    if item.get("action"):
        lines += ["## 이 기준이 나온 행동", "", item["action"], ""]
    for source in item.get("sources") or []:
        if source.get("type") == "answer":
            lines += ["## 질문과 답", "", f"- 질문: {source.get('question', '')}", f"- 답: {source.get('quote', '')}", ""]
        elif source.get("type") == "document":
            lines += [f"- 출처: {source.get('title', '')} · 조각 {source.get('chunk', '')}: {source.get('quote', '')}"]
    if item.get("resolution"):
        lines += ["", f"쟁점 해석 선택: {item['resolution']}"]
    return "\n".join(lines).strip() + "\n"
