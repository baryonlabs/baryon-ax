"""Starter ontology packs on merge v2, and an idempotent importer for a new organization.

A *starter pack* is what an organization begins with before it has documents of its own: a small set
of typed entities (사람·조직, 시스템, 문서·규정, 절차, 자산·대상, 개념), typed relations between them, and a
handful of **slots** — values nobody has answered yet (``법인카드 —한도→ ?금액``). It is the merge v2 model
(``ontology_merge``, schema 2) written down as a file, so importing it puts the pack in the same store
the loop, the gap packs and Slack threads write to.

Two rules keep the pack from turning into the old ``related_to`` chain (docs/ontology-quality.md):

* the schema is **strict and closed**: every entity type, relation type and value type comes from the
  lists in :mod:`baryon.ontology`, unknown fields are rejected, and model output that breaks the schema
  is thrown away whole — never partially accepted;
* a label is never a verb form, a question word or a bare particle (:func:`baryon.ontology.is_entity_noise`),
  and ``related_to`` is not a relation type at all.

Import is idempotent: the pack id keys the document in ``packs``, and every entity, definition and
relation is keyed by its source (``source_key = starter_pack:<pack id>``), so importing the same file
again changes nothing. The pack is written only inside the organization's own ``LoopStore`` file.

Usage (from the repository root)::

    uv run --project apps/api python -m baryon.ontology_pack validate --pack examples/ontology/starter-pack.ko.json
    uv run --project apps/api python -m baryon.ontology_pack import --pack examples/ontology/starter-pack.ko.json --org demo --dry-run
    uv run --project apps/api python -m baryon.ontology_pack import --pack examples/ontology/starter-pack.ko.json --org demo

``--dry-run`` prints the diff a person would see (what would be added, what is already there) and writes
nothing. ``TENANCY_MODE=pooled`` additionally requires ``--org`` to be a registered, active tenant.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .ontology import VALUE_TYPE, EntityType, RelationType, ValueType, is_entity_noise
from .ontology_merge import DOCUMENT, Claim, Entity, Relation, label_key, merge

Complete = Callable[[list[dict[str, str]]], Awaitable[str]]

PACK_SCHEMA_VERSION = 2
RELATION_TYPES = tuple(RelationType.__args__)  # type: ignore[attr-defined]
ENTITY_TYPES = tuple(EntityType.__args__)      # type: ignore[attr-defined]
VALUE_TYPES = tuple(ValueType.__args__)        # type: ignore[attr-defined]
REPO_ROOT = Path(__file__).resolve().parents[4]


class PackError(ValueError):
    """The file is not a usable starter pack (bad path, empty organization id, closed tenant …)."""


class PackSchemaError(PackError):
    """The pack (or the model output that was supposed to become one) breaks the strict schema."""


# --------------------------------------------------------------------------- schema


class PackEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, max_length=40)
    type: EntityType
    definition: str = Field("", max_length=300)
    synonyms: list[str] = Field(default_factory=list, max_length=10)


class PackRelation(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    from_: str = Field(..., alias="from", min_length=1, max_length=40)
    type: RelationType
    to: str = Field(..., min_length=1, max_length=40)
    note: str = Field("", max_length=200)


class PackSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(..., min_length=1, max_length=40)
    relation: RelationType
    value_type: ValueType
    question: str = Field("", max_length=300)
    status: Literal["open"] = "open"


class StarterPack(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[2] = PACK_SCHEMA_VERSION
    id: str = Field(..., pattern=r"^[A-Za-z0-9_-]{2,60}$")
    name: str = Field(..., min_length=1, max_length=120)
    domain: str = Field(..., min_length=1, max_length=80)
    language: str = Field("ko", max_length=8)
    version: str = Field("", max_length=40)
    description: str = Field("", max_length=500)
    status: Literal["draft", "reviewed", "active"] = "draft"
    entities: list[PackEntity] = Field(..., min_length=1, max_length=200)
    relations: list[PackRelation] = Field(..., max_length=400)
    slots: list[PackSlot] = Field(..., max_length=100)
    # The fields a gap pack already has (the admin pack list reads them). A starter pack has no gaps or
    # acceptance questions of its own, so they default to empty rather than making the list render fail.
    source_gap_ids: list[str] = Field(default_factory=list, max_length=100)
    source_questions: list[str] = Field(default_factory=list, max_length=100)
    required_sources: list[dict[str, str]] = Field(default_factory=list, max_length=20)
    acceptance_questions: list[str] = Field(default_factory=list, max_length=100)
    notes: str = Field("", max_length=1000)


class _DraftEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, max_length=40)
    type: EntityType
    definition: str = Field("", max_length=300)
    synonyms: list[str] = Field(default_factory=list, max_length=10)


class _DraftRelation(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    from_: str = Field(..., alias="from", min_length=1, max_length=40)
    type: RelationType
    to: str = Field(..., min_length=1, max_length=40)


class _DraftSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(..., min_length=1, max_length=40)
    relation: RelationType
    value_type: ValueType
    question: str = Field("", max_length=300)


class DraftPack(BaseModel):
    """Exactly what a model may return. Anything else is a schema error and is dropped whole."""

    model_config = ConfigDict(extra="forbid")
    entities: list[_DraftEntity] = Field(..., min_length=1, max_length=60)
    relations: list[_DraftRelation] = Field(default_factory=list, max_length=120)
    slots: list[_DraftSlot] = Field(default_factory=list, max_length=40)


def _schema_errors(error: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors())


def _fail(problems: list[str]) -> None:
    if problems:
        raise PackSchemaError("starter pack is not usable: " + "; ".join(problems))


def validate_pack(data: dict) -> dict:
    """Validate a whole starter pack and return it normalized (``from`` aliases kept).

    Nothing is repaired and nothing is kept partially: one unknown field, one relation type outside
    the closed list, one relation whose end is not an entity, or one verb form as an entity and the
    file is rejected. The semantic checks are what keep a pack on the merge v2 model.
    """
    try:
        pack = StarterPack.model_validate(data)
    except ValidationError as error:
        raise PackSchemaError("starter pack does not match the schema: " + _schema_errors(error)) from error
    problems: list[str] = []
    names = {label_key(entity.name): entity.name for entity in pack.entities}
    for entity in pack.entities:
        if is_entity_noise(entity.name):
            problems.append(f"entity '{entity.name}' is a verb form, question word or particle")
        for synonym in entity.synonyms:
            if is_entity_noise(synonym):
                problems.append(f"synonym '{synonym}' of '{entity.name}' is a verb form, question word or particle")
    if len(names) != len(pack.entities):
        problems.append("entity names must be unique (spacing and case do not distinguish them)")
    seen_slots: set[tuple[str, str]] = set()
    for item in pack.slots:
        if label_key(item.subject) not in names:
            problems.append(f"slot subject '{item.subject}' is not one of the entities")
        if VALUE_TYPE[item.relation] != item.value_type:
            problems.append(f"slot '{item.subject}—{item.relation}' must ask for {VALUE_TYPE[item.relation]}, not {item.value_type}")
        key = (label_key(item.subject), item.relation)
        if key in seen_slots:
            problems.append(f"slot '{item.subject}—{item.relation}' is listed twice")
        seen_slots.add(key)
    seen_relations: set[tuple[str, str, str]] = set()
    for item in pack.relations:
        for end in (item.from_, item.to):
            if label_key(end) not in names:
                problems.append(f"relation end '{end}' is not one of the entities")
        if item.type not in RELATION_TYPES:  # pydantic already checks this; kept explicit for the message
            problems.append(f"relation type '{item.type}' is not in the closed list")
        key = (label_key(item.from_), item.type, label_key(item.to))
        if key in seen_relations:
            problems.append(f"relation '{item.from_}—{item.type}→{item.to}' is listed twice")
        seen_relations.add(key)
        if (label_key(item.from_), item.type) in seen_slots:
            problems.append(f"'{item.from_}—{item.type}' is both a typed relation and an open slot")
    _fail(problems)
    return pack.model_dump(by_alias=True)


def load_pack(path: str | Path) -> dict:
    file = Path(path).expanduser()
    if not file.is_file():
        raise PackError(f"pack file not found: {path}")
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PackError(f"pack file is not JSON: {path}") from error
    if not isinstance(data, dict):
        raise PackSchemaError("a starter pack must be a JSON object")
    return validate_pack(data)


def resolve_pack_path(path: str | Path) -> Path:
    """The path as given, or the same path under the repository root (so docs' commands work anywhere)."""
    file = Path(path).expanduser()
    if file.is_file():
        return file
    candidate = REPO_ROOT / file
    return candidate if candidate.is_file() else file


# --------------------------------------------------------------------------- model builder

_SYSTEM = f"""You write one starter ontology pack for a fictional company domain a person gives you.
Return JSON only, exactly in this shape:
{{"entities": [{{"name": str, "type": one of {list(ENTITY_TYPES)}, "definition": str, "synonyms": [str]}}],
 "relations": [{{"from": str, "type": one of {list(RELATION_TYPES)}, "to": str}}],
 "slots": [{{"subject": str, "relation": one of {list(RELATION_TYPES)}, "value_type": one of {list(VALUE_TYPES)}, "question": str}}]}}
Rules:
- every entity is a noun phrase (법인카드, 연차 휴가 신청, 취업규칙). Never a verb form (분실하면, 신고해),
  a question word (얼마, 누구, 언제) or a bare attribute (한도, 마감).
- every relation type is from the closed list; "related_to" does not exist. A relation links two entities.
- a slot is a value nobody in the organization has answered yet: subject is an entity, relation says which
  value is missing, and value_type is its kind (한도→금액, 기한→기간, 승인자·신고처·담당→사람·조직,
  제출처→시스템, 근거→문서·규정). Write the question that asks for it.
- do not repeat a (subject, relation) pair, and do not state the same pair as both a relation and a slot.
- write 12 to 18 entities, 8 or more relations and 4 to 6 slots. Korean names stay in Korean."""


def parse_draft(text: str) -> DraftPack:
    """Parse a model answer as :class:`DraftPack`. One broken field rejects the whole answer."""
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        raise PackSchemaError("model output has no JSON object")
    try:
        raw = json.loads(match.group(0))
    except ValueError as error:
        raise PackSchemaError("model output is not JSON") from error
    try:
        return DraftPack.model_validate(raw)
    except ValidationError as error:
        raise PackSchemaError("model output breaks the strict pack schema: " + _schema_errors(error)) from error


async def build_pack(brief: str, complete: Complete, *, pack_id: str, name: str, domain: str,
                     version: str = "", language: str = "ko") -> dict:
    """Build a starter pack from a one-paragraph domain brief with the configured model.

    The model answer is validated as a whole (:class:`DraftPack`, then :func:`validate_pack`). A single
    out-of-schema item raises :class:`PackSchemaError`; the caller must not fall back to the partially
    valid part of the answer.
    """
    text = await complete([{"role": "system", "content": _SYSTEM},
                           {"role": "user", "content": brief.strip()[:4000]}])
    draft = parse_draft(text)
    data = {
        "schema_version": PACK_SCHEMA_VERSION,
        "id": pack_id,
        "name": name,
        "domain": domain,
        "language": language,
        "version": version,
        "description": brief.strip()[:500],
        "status": "draft",
        "entities": [entity.model_dump() for entity in draft.entities],
        "relations": [item.model_dump(by_alias=True) for item in draft.relations],
        "slots": [slot.model_dump() for slot in draft.slots],
    }
    return validate_pack(data)


# --------------------------------------------------------------------------- import


def _tenant_check(organization_id: str) -> str:
    """The organization the pack goes to. Pooled mode only accepts a registered, active tenant."""
    from .tenants import TENANT_ID_PATTERN, TenancySettings, TenantRegistry

    organization = (organization_id or "").strip()
    if not organization or len(organization) > 120 or organization in (".", "..") or re.search(r"[\\/\x00]", organization):
        raise PackError("organization id must be a non-empty name without path separators")
    if TenancySettings.from_env().pooled:
        if not TENANT_ID_PATTERN.fullmatch(organization):
            raise PackError("pooled mode: organization id must match [a-z0-9-]{2,40}")
        tenant = TenantRegistry().get(organization)
        if tenant is None:
            raise PackError(f"pooled mode: organization '{organization}' is not a registered tenant")
        if not tenant.active:
            raise PackError(f"pooled mode: organization '{organization}' is {tenant.status}, not active")
    return organization


def pack_source(pack: dict) -> dict:
    return {"type": "starter_pack", "pack_id": pack["id"], "pack": pack["name"], "version": pack.get("version", "")}


def pack_items(pack: dict, skip_slots: set[tuple[str, str]] | None = None) -> tuple[list[Claim], list[Entity], list[Relation]]:
    """The pack as merge v2 input: typed entities, their definitions as claims, typed relations, open slots.

    A slot becomes a relation to a missing value (``법인카드 —한도→ ?금액``), exactly like a gap pack's
    slots, so the review screen and the importer agree on what is still unknown. Slots the organization
    can already answer (``skip_slots``) are not reopened.
    """
    source = pack_source(pack)
    skip_slots = skip_slots or set()
    types = {label_key(entity["name"]): entity["type"] for entity in pack["entities"]}
    entities = [Entity(name=entity["name"], type=entity["type"], synonyms=list(entity.get("synonyms") or []), source=source)
                for entity in pack["entities"]]
    claims = [Claim(subject=entity["name"], value=entity["definition"].strip(), predicate="정의", trust=DOCUMENT,
                    type=entity["type"], source=source)
              for entity in pack["entities"] if entity["definition"].strip()]
    relations = [Relation(s=item["from"], p=item["type"], o=item["to"], source=source,
                           s_type=types.get(label_key(item["from"]), ""), o_type=types.get(label_key(item["to"]), ""))
                 for item in pack["relations"]]
    relations += [Relation(s=slot["subject"], p=slot["relation"], o=f"?{slot['value_type']}", source=source,
                           s_type=types.get(label_key(slot["subject"]), ""), missing=True)
                  for slot in pack["slots"]
                  if (label_key(slot["subject"]), slot["relation"]) not in skip_slots]
    return claims, entities, relations


def _same_pack(left: dict, right: dict) -> bool:
    return ({key: value for key, value in left.items() if key not in ("saved_at", "imported_at")}
            == {key: value for key, value in right.items() if key not in ("saved_at", "imported_at")})


def _filled_slots(data: dict, pack: dict) -> list[dict]:
    """Slots the organization can already answer (from its documents or earlier answers)."""
    term_by_key: dict[str, dict] = {}
    for term in data.get("terms", {}).values():
        term_by_key.setdefault(label_key(term.get("label", "")), term)
    for term in data.get("terms", {}).values():
        for synonym in term.get("synonyms") or []:
            term_by_key.setdefault(label_key(synonym), term)
    filled = []
    for slot in pack["slots"]:
        term = term_by_key.get(label_key(slot["subject"]))
        value = ""
        if term:
            value = next((str(claim.get("value") or "") for claim in term.get("claims") or []
                          if claim.get("slot") == slot["relation"] and claim.get("status") in (None, "current")), "")
        filled.append({**slot, "filled": bool(value), "value": value})
    return filled


def _summary(pack: dict, organization_id: str, changes: Any, data: dict, action: str, dry_run: bool) -> dict:
    counts = changes.counts()
    slots = _filled_slots(data, pack)
    terms = [{"label": item["label"], "type": item.get("type", ""), "definition": item.get("definition", "")}
             for item in changes.added]
    relations = [{"from": item["s"], "type": item["p"], "to": item["o"]}
                 for item in changes.relations if item.get("status") != "missing"]
    new_slots = [item for item in changes.relations if item.get("status") == "missing"]
    open_slots = [{"subject": item["subject"], "relation": item["relation"], "value_type": item["value_type"],
                   "filled": item["filled"], "value": item["value"]} for item in slots]
    stored = data.get("relations") or []
    return {
        "pack": {"id": pack["id"], "name": pack["name"], "version": pack.get("version", "")},
        "organization_id": organization_id,
        "dry_run": dry_run,
        "action": action,
        "counts": counts,
        "changes": changes.count(),
        "terms": terms,
        "relations": relations,
        "new_slots": [{"subject": item["s"], "relation": item["p"], "to": item["o"]} for item in new_slots],
        "slots": open_slots,
        "open_slots": sum(1 for slot in open_slots if not slot["filled"]),
        "term_count": len(data.get("terms") or {}),
        "relation_count": sum(1 for item in stored if item.get("status") != "missing"),
        "slot_count": sum(1 for item in stored if item.get("status") == "missing"),
        "pack_count": len(data.get("packs") or {}),
    }


def import_pack(source: str | Path | dict, organization_id: str, *, dry_run: bool = False, store: Any = None) -> dict:
    """Merge a starter pack into one organization's ontology, idempotently.

    ``source`` is a pack file path or an already-validated pack dict. With ``dry_run`` the diff is
    computed on an in-memory copy and nothing is written. Returns a summary for the CLI and tests:
    counts by change kind, the added entities and relations, and each slot with whether the
    organization can already answer it.
    """
    pack = load_pack(source) if isinstance(source, (str, Path)) else validate_pack(source)
    organization = _tenant_check(organization_id)
    from .loop import LoopStore

    store = store or LoopStore()
    outcome: dict[str, Any] = {}

    def change(data: dict) -> dict:
        from .ontology_merge import apply

        known = {(label_key(slot["subject"]), slot["relation"])
                 for slot in _filled_slots(data, pack) if slot["filled"]}
        claims, entities, relations = pack_items(pack, known)
        changes = merge(data, claims, relations, entities, write=False)
        stored = {**pack, "organization_id": organization}
        existing = data["packs"].get(pack["id"])
        if existing is not None and _same_pack(existing, stored):
            action = "unchanged"
        else:
            action = "updated" if existing is not None else "added"
            stamp = _stamp()
            stored.update(saved_at=stamp, imported_at=stamp)
            changes.packs[pack["id"]] = stored
        apply(data, changes)  # in memory; only store.update writes it to disk
        outcome.update(_summary(pack, organization, changes, data, action, dry_run))
        return changes

    if dry_run:
        change(store.load(organization))  # the loaded document is a copy; nothing is saved
    else:
        store.update(organization, change)
    return outcome


def _stamp() -> str:
    from .ontology_merge import now

    return now()


# --------------------------------------------------------------------------- CLI


def _print_summary(result: dict) -> None:
    pack = result["pack"]
    mode = "dry-run" if result["dry_run"] else "import"
    label = {"added": "새로 추가", "updated": "갱신", "unchanged": "변경 없음"}[result["action"]]
    print(f"{mode} · {pack['name']} {pack['version']} ({pack['id']}) → 조직 {result['organization_id']}")
    print(f"  꾸러미: {label}")
    if result["changes"] == 0:
        print("  변경 없음: 이미 같은 내용이 들어 있습니다 (멱등).")
    else:
        print(f"  개념 +{len(result['terms'])}, 관계 +{len(result['relations'])}, 슬롯 +{len(result['new_slots'])} "
              f"(합계 {result['changes']}건)")
        for term in result["terms"][:20]:
            print(f"  + 개념 {term['label']} ({term['type']})")
        for relation in result["relations"][:30]:
            print(f"  + 관계 {relation['from']} —{relation['type']}→ {relation['to']}")
        for slot in result["new_slots"]:
            print(f"  + 슬롯 {slot['subject']} —{slot['relation']}→ {slot['to']}")
        for slot in result["slots"]:
            mark = "이미 답 있음" if slot["filled"] else "열림"
            value = f" = {slot['value']}" if slot["filled"] else f" ?{slot['value_type']}"
            print(f"  · 슬롯 {slot['subject']} —{slot['relation']}→{value} ({mark})")
    print(f"  결과: 개념 {result['term_count']} · 관계 {result['relation_count']} · 슬롯 {result['slot_count']} · "
          f"꾸러미 {result['pack_count']}")
    if result["dry_run"]:
        print("  (dry-run: 아무것도 쓰지 않았습니다)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="스타터 온톨로지 꾸러미 검증·가져오기 (merge v2, schema 2)")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="꾸러미 파일이 strict schema를 지키는지 확인")
    validate.add_argument("--pack", required=True)
    importer = sub.add_parser("import", help="한 조직의 온톨로지로 가져오기 (멱등)")
    importer.add_argument("--pack", required=True)
    importer.add_argument("--org", required=True, help="조직 id (pooled 모드는 등록된 활성 테넌트)")
    importer.add_argument("--dry-run", action="store_true", help="무엇이 들어갈지만 출력하고 쓰지 않는다")
    importer.add_argument("--store", default="", help="ONTOLOGY_STORE 디렉터리 (기본: 환경변수)")
    args = parser.parse_args(argv)

    try:
        path = resolve_pack_path(args.pack)
        if args.command == "validate":
            pack = load_pack(path)
            types = len({item["type"] for item in pack["relations"]} | {slot["relation"] for slot in pack["slots"]})
            print(f"OK · {pack['name']} ({pack['id']}) · 개념 {len(pack['entities'])}, 관계 {len(pack['relations'])}, "
                  f"슬롯 {len(pack['slots'])}, 관계 타입 {types}/{len(RELATION_TYPES)}")
            return 0
        store = None
        if args.store:
            from .loop import LoopStore

            store = LoopStore(Path(args.store) / "loop")
        result = import_pack(path, args.org, dry_run=args.dry_run, store=store)
        _print_summary(result)
        return 0
    except PackError as error:
        print(f"error: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
