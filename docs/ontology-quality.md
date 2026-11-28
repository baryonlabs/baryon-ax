# Ontology pack quality

Decision record, 2026-11-23. Owner request: find out why ontology packs built from knowledge gaps are weak, and
borrow what NVIDIA's open-source Auto Ontology projects do well.

## What was wrong

A live test turned two unanswered questions into a pack:

- "법인카드 해외 결제 한도는 얼마야?"
- "법인카드 분실하면 누구에게 신고해?"

| Pack output | Why it happened (`apps/api/src/baryon/gaps.py`) |
| --- | --- |
| Entities `분실하면`, `신고해` | Terms come from a regex plus a short particle list. Verb endings (`-하면`, `-해`) are not particles, so predicates became entities |
| `해외`, `결제`, `한도` as three entities | Spacing splits one concept ("해외 결제 한도") |
| Relations `법인카드 → 분실하면 → 신고해 → 해외 …`, all `related_to` | Relations link neighbouring terms in order; they carry no meaning |
| Every entity is `concept` | No types |
| What is unknown is not shown | The pack lists terms; it does not say "the limit is missing" or "who to report to is missing" |
| Nobody noticed | Tests check that a pack has fields, not that its entities are right |

## What we borrow

[NVIDIA/auto-ontology](https://github.com/NVIDIA/auto-ontology) builds ontologies from database metadata, and
[NVIDIA/auto-ontology-eval](https://github.com/NVIDIA/auto-ontology-eval) measures how much they help. Both are
Apache-2.0. Their input is databases, not documents or questions, so we take methods, not the stack (Postgres,
pgvector, Next.js, NeMo Gym).

| Borrowed idea | Where it comes from | Our use |
| --- | --- | --- |
| Model output in a strict schema: fixed fields, the field descriptions are the instructions, anything else is rejected | `auto_ontology/semantic/term_extractor.py`, `semantic/models.py` | Typed entities and typed relations from gap questions; output that does not validate is dropped |
| Check answerability: entities in a question, then whether each has evidence | `docs/architecture.md` → Check answerability | A pack names its **slots**: the values nobody has answered yet (`법인카드 —한도→ [?금액]`) |
| Merge decisions: are these two the same thing, under one name | `semantic/concepts.py` (`ConceptMergeDecision`) | Next step: merge synonyms across packs (법인카드 / 회사카드) |
| Control vs treatment on the same questions, deterministic scoring, lists of what got fixed and what broke | auto-ontology-eval | `apps/api/eval/ontology_eval.py`: the old extractor and the new ones on one Korean gold set |
| Apache Ossie as the interchange format | both | Already supported here; later, align our export with their `ModelConcept` |

No source file is copied. If one is, it keeps its Apache-2.0 header and goes into `THIRD_PARTY_NOTICES`.

## Plan

1. **Evaluation first.** A Korean gold set (`apps/api/eval/ontology_gold_ko.json`, 20 gap questions with the entities
   and slots a reviewer expects) and a scorer that runs every extractor on it: entity precision and recall, noise (verb
   forms and question words taken as entities), slot recall and precision. Deterministic, no model judge.
2. **Extraction.** `apps/api/src/baryon/ontology.py`:
   - a rule-based extractor that is the default: noun phrases only, and slots from Korean question patterns (얼마 →
     한도, 누구에게 신고 → 신고처, 언제·며칠 → 기한, 누가 승인 → 승인자 …);
   - a model extractor used when a model is configured, validated against the schema, falling back to the rules.
   Packs keep their shape (entities and relations) so the web UI and verification still work, and gain entity types
   and `slots`.
3. **Slots in the review flow.** Reviewers and experts fill the slots; `verify_pack` re-checks with the acceptance
   questions.
4. **Merge** synonyms across packs.
5. **Ossie alignment** with Auto Ontology, so a company's database ontology (theirs) and its document and tacit
   ontology (ours) can be combined.

Success: on the gold set, entity precision ≥ 0.8, noise near 0, slot recall ≥ 0.8, and no question that the old
extractor got right and the new one gets wrong.

## Results

Run from `apps/api`:

```bash
uv run python eval/ontology_eval.py                                   # legacy vs rules, no model needed
uv run python eval/ontology_eval.py --model-url http://127.0.0.1:8020/v1 --model baryon-ax-qwen --no-thinking
uv run python eval/ontology_eval.py --gold eval/ontology_holdout_ko.json ...   # held-out set
```

### 2026-11-23: first round

Model arm: `baryon-ax-qwen` (Qwen3.5-27B on vLLM, the B300 GPU server), thinking off. The model names the
entities and the slot subject; the question patterns name the relation (`model+rules`).

**Tuning set** (20 questions; the extractor and the prompt were adjusted while looking at these):

| metric | legacy | rules | model+rules |
| --- | --- | --- | --- |
| entity precision | 0.50 | 0.86 | 1.00 |
| entity recall | 0.65 | 0.90 | 1.00 |
| noise (verb forms, question words) | 0.26 | 0.00 | 0.00 |
| slot recall / precision | 0 / 0 | 0.90 / 0.90 | 1.00 / 1.00 |
| questions fully right | 0/20 | 16/20 | 20/20 |

**Held-out set** (10 questions written after tuning, `eval/ontology_holdout_ko.json`):

| metric | legacy | rules | model+rules |
| --- | --- | --- | --- |
| entity precision | 0.52 | 0.93 | 1.00 |
| entity recall | 0.60 | 0.70 | 1.00 |
| noise | 0.29 | 0.00 | 0.00 |
| slot recall / precision | 0 / 0 | 0.90 / 0.90 | 1.00 / 1.00 |
| questions fully right | 0/10 | 7/10 | 10/10 |

Honest notes:

- The first held-out run scored **9/10** (slots 0.9). It exposed a pattern bug: "보고서" (a report) matched the
  신고처 pattern "보고". After fixing it (`보고(?!서)`) the held-out set is no longer unseen; the next held-out set
  must be new questions.
- The prompt's examples are deliberately not from either set; with examples copied from the tuning set the score was
  20/20 too, which would have been leakage.
- qwen2.5:7b on Ollama: 19/20 on the tuning set once bare-string entities were accepted. qwen2.5:14b loaded on the
  same 24 GB Mac made Ollama quit; use the B300 for the model arm.
- 30 questions is a smoke test, not a benchmark. Grow both sets from real gaps before quoting numbers outside the team.
- Old packs and their gaps stay as they were; a gap converted into a pack does not reopen when asked again.

Live check (API on `baryon-ax-qwen`): two new gaps, "출장 중 렌터카 비용 한도는 얼마야?" and "렌터카 사고가 나면 누구에게
연락해?", became a pack with `렌터카 사고 —신고처→ ?사람·조직` and `출장 중 렌터카 비용 —한도→ ?금액`, both slots open.
Still to improve: entity types (the cost phrase was typed 금액 instead of the entity being 렌터카 비용), and the web
pack view does not show slots yet.

### 2026-11-23: second round

- **Slots in the web pack view.** Each pack lists "아직 모르는 값" (`회사카드 —한도→ ?금액`) and shows entity types.
- **Entity types.** Amounts and durations are values a slot asks for, not entities, so they left the entity types;
  an unknown type from the model becomes 개념 instead of dropping the entity.
- **One model call per question**, as the evaluation scores it. With several questions in one prompt the model kept
  whole phrases ("회사카드 해외 결제") as entities.
- **Synonyms across packs** (step 4). A new pack names its entities the way earlier packs already do. Spacing variants
  merge without a model (연차휴가 → 연차 휴가). With a model, other words for the same thing merge too: the model
  only picks from the listed names, following Auto Ontology's merge decisions. Live: a pack on "법인카드는 누가
  승인해?", made after one on "회사카드", recorded 회사카드 with the other name 법인카드. The first name used
  becomes the canonical one.
- **Ossie export** carries the open slots in `ai_context.instructions` and synonyms in field descriptions, so whoever
  completes the draft knows what is missing.
- **Bug found on the way:** the default `docker-compose.yml` set no `ONTOLOGY_STORE` or `FEEDBACK_STORE`, so packs and
  answer feedback were written to `/tmp` inside the container and lost whenever it was recreated (four packs from
  the first round were lost this way). Both now have volumes, as `docker-compose.parsers.yml` and the AWS module
  already did.
- Evaluation after these changes: tuning set 20/20, held-out set 10/10 (model+rules), nothing broke.

Still open: aligning the Ossie export with Auto Ontology's `ModelConcept` / `ModelTerm` (their interchange is built
around database columns and SQL; ours has no columns), and growing both question sets from real gaps.

### 2026-11-23: third round (re-evaluation after deploying main)

A second held-out set (`eval/ontology_holdout2_ko.json`, 10 new questions) was written after the second round and
scored once, untouched. The scorer now also reports time per question.

| set | legacy | rules | model+rules (first run) |
| --- | --- | --- | --- |
| tuning, 20 | 0/20 | 16/20 | 20/20 |
| held-out 1, 10 (seen: used to find a bug in round one) | 0/10 | 7/10 | 10/10 |
| **held-out 2, 10 (unseen)** | 0/10 | 7/10 | **9/10** |

Held-out 2 detail (model+rules, first run): entity precision 1.00, recall 1.00, noise 0, slot recall/precision 0.90.
The miss: "노트북 분실하면 누구한테 **알려야** 해?" was read as 담당 (who handles) instead of 신고처 (whom to notify),
because 알리다 was not in the 신고처 pattern. After adding it, held-out 2 scores 10/10 (rules 8/10), and held-out 2 is
no longer unseen either; the next re-evaluation needs new questions again.

Time per question (model+rules on the B300, one call each): median about 1.1 s, max about 1.3 s. A pack from N gaps
runs four calls at a time. The rules take well under a millisecond.

Headline to quote: on questions it had not seen, model+rules got 9 of 10 fully right with no noise; the old extractor
got 0 of 10 and a quarter of its entities were verb forms or question words.

### 2026-11-28: starter pack on merge v2 (import path)

A new organization starts with nothing. The pack it is meant to start from is now a curated **starter pack** built on
the merge v2 model (schema 2: typed entities, typed relations, and slots for values nobody has answered), not a pack
extracted from gap questions with the old `related_to` chain. An operator imports it for the organization with the
CLI below (a first-login step can call the same function).

- File: `examples/ontology/starter-pack.ko.json` — a **fictional** company (데모상사): 15 concepts over all six
  entity types, 12 typed relations, and 5 open slots. Public repository, so no real organization's material.
- Builder: `apps/api/src/baryon/ontology_pack.py`. `build_pack` asks the configured model for a draft and accepts
  it only if **the whole draft** matches the strict schema; one unknown field, one relation type outside the closed
  list, one relation end that is not an entity, or one verb form as an entity rejects the whole answer. There is no
  partial acceptance and no repair. `validate_pack` is the same gate for a hand-written file.
- Import: `python -m baryon.ontology_pack import --pack <file> --org <id> [--dry-run]`. It merges through
  `ontology_merge` (one organization file, claim-level same/refine/contest, source-keyed claims), so importing the
  same pack again changes nothing. `--dry-run` prints the diff and writes nothing. The pack never leaves the
  organization's own store; `TENANCY_MODE=pooled` only accepts a registered, active tenant. A slot the organization
  can already answer is not reopened.
- Eval: `apps/api/eval/ontology_eval.py` scores the starter pack with a deterministic gate and fails loudly.

| structural metric | old pack #10 (`ont_goldfish_core`) | starter pack (`starter-pack.ko.json`) |
| --- | --- | --- |
| entities that are verb forms or question words | yes (`분실하면`, `신고해`) | **0** |
| `related_to` share of relations | 1.0 (the only relation type) | **0.0** |
| slot fill rate (subject is an entity, right value type, question written) | 0 (no slots) | **1.0 (5/5)** |
| distinct relation types from the closed list | 1 / 7 | **7 / 7** |
| typed entities | none (all `개념`) | 15 over 6 types |

These are structural metrics — they say the pack is shaped like an ontology, not that a model answers better.
The model arms above are still the quality evidence for extraction. Run the gate from `apps/api`:

```bash
uv run python eval/ontology_eval.py          # the starter pack table is printed after the question arms
uv run python eval/ontology_eval.py --json /tmp/ontology.json
```

The thresholds are pinned in `apps/api/tests/test_ontology_pack.py` (`STARTER_THRESHOLDS`): noise = 0,
`related_to` = 0, slot fill = 1.0, at least 5 of the 7 relation types. Loosening one of them breaks a test.
