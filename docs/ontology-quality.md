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
