# Golden threads

Synthetic Slack threads for `eval/thread_eval.py`. Every name, user id and channel id here is invented.
The file layout and the `expected.json` schema are described at the top of `thread_eval.py`.

| fixture | what it checks |
| --- | --- |
| `explicit-decision` | a decision said in words (견적 유효기간 30일), its reason, and an owner assigning work |
| `reaction-consensus` | a decision agreed only with 👍 / ✅ reactions |
| `edit-delete` | a decision edited after posting (3층 → 5층) and another one deleted: old values must not come back |
| `existing-term` | words for terms the ontology already has (견적, quote → 견적서): merge, no new term, no false [쟁점] |
| `multi-bot` | three bots: conflicting bot claims, a bot post that mentions us, a message meant for another bot |
| `multi-bot-confirm` | the owner presses 응 on our confirmation card in the same second another bot posts a conflicting claim: the press answers that card only, the bot's claim needs verification |
| `route-target` | one router for every message (S6): a question about another bot answered from the channel, the owner's unfinished message held, "여기 내용이다" for another bot left alone, other bots' summaries and status posts not answered, no knowledge gap |

Real threads are private. Put them under `$THREAD_FIXTURES` (default `~/.baryon-ax/thread-fixtures`), never here:
this repository is public. `eval/orca_thread_convert.py` turns a thread copied from Orca into `replies.json`,
`users.json` and `meta.json`; `expected.json` is written by hand.

The rule arm's scores for these fixtures are in `eval/thread_baseline.json`, the understand arm's (S3, thread
understanding, rules) in `eval/thread_baseline_understand.json`; `tests/test_thread_eval.py` fails when either gets
worse, and when the understand arm is worse than the rule arm on any metric. After an improvement, refresh them:

    uv run python eval/thread_eval.py --no-private --write-baseline
    uv run python eval/thread_eval.py --no-private --arm understand --write-baseline
