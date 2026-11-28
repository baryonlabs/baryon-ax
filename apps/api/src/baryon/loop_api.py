"""HTTP API of the question–certification loop (loop.py). main.py builds it with its services.

    POST /v1/loop/actions                 record a decision or request (who, what, situation) — the asker's own
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

from . import loop
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
    judge: bool = False                              # judge now instead of at the next round


class RoundBody(BaseModel):
    documents: int = Field(5, ge=0, le=50)
    chunks_per_document: int = Field(3, ge=1, le=20)
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
                  "source": body.source, "source_ref": body.source_ref, "status": "new", "created_at": loop.now()}
        store.update(principal.organization_id, lambda data: data["actions"].__setitem__(action["id"], action))
        deps.audit.emit("loop.action_recorded", principal, str(uuid.uuid4()), action_id=action["id"], source=body.source)
        if body.judge:
            await judge_actions(principal, [action["id"]])
            action = store.load(principal.organization_id)["actions"][action["id"]]
            question = next((q for q in store.load(principal.organization_id)["questions"].values() if q["action_id"] == action["id"]), None)
            return {**action, "question": question}
        return action

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
        # ① ontology: documents the loop has not read yet (or that changed), a few chunks each
        extracted: list[tuple[dict, int, dict]] = []
        if deps.model_ready() and body.documents:
            seen = data["seen_documents"]
            # The ontology is shared by the whole organization, so it is built only from documents every member can
            # read; restricted documents would leak through term definitions.
            reader = deps.principal("loop-reader", principal.organization_id, "member")
            fresh = [doc for doc in deps.documents.list_for(reader)
                     if "member" in (doc.get("allowed_roles") or [])
                     and seen.get(doc["id"]) != doc.get("created_at") and "loop" not in (doc.get("tags") or [])]
            for listed in fresh[: body.documents]:
                document = deps.documents.documents.get(listed["id"])
                if not document:
                    continue
                chunks = [str(item.get("text") or "") for item in document.chunks] or [document.content]
                for number_in_doc, text in enumerate(chunks[: body.chunks_per_document], 1):
                    if len(text.strip()) < 40:
                        continue
                    try:
                        extracted.append((listed, number_in_doc, await loop.extract_from_chunk(listed, number_in_doc, text, deps.complete)))
                    except Exception:  # noqa: BLE001
                        summary["errors"] += 1
                summary["documents"] += 1
                data["seen_documents"][listed["id"]] = listed.get("created_at")

        seen_now = dict(data["seen_documents"])

        def apply(fresh: dict) -> None:
            fresh["seen_documents"].update(seen_now)
            for listed, chunk, result in extracted:
                for term in result["terms"]:
                    source = {"type": "document", "document_id": listed["id"], "title": listed.get("title", ""), "chunk": chunk,
                              "quote": term["quote"], "matched": term["verified"]}
                    before = fresh["terms"].get(loop._term_id(term["label"]))
                    key = loop.merge_term(fresh["terms"], term["label"], term["definition"], source, number, term["verified"])
                    summary["terms"] += 0 if before else 1
                    summary["contested"] += 1 if loop.is_contested(fresh["terms"][key]) and not (before and loop.is_contested(before)) else 0
                for rel in result["relations"]:
                    s_id, o_id = loop._term_id(rel["s"]), loop._term_id(rel["o"])
                    if s_id in fresh["terms"] and o_id in fresh["terms"] and not any(
                            r["s_id"] == s_id and r["p"] == rel["p"] and r["o_id"] == o_id for r in fresh["relations"]):
                        fresh["relations"].append({**rel, "s_id": s_id, "o_id": o_id, "status": DRAFT, "round": number,
                                                   "sources": [{"document_id": listed["id"], "chunk": chunk}]})
                        summary["relations"] += 1

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
