# FAQ: what it does, and where it stops

For product, QA operations and compliance operations. This is also the "what this repo owns vs
what it integrates" map.

## What does it actually decide?

Given a call recording, or a transcript that already exists, it produces one `Scorecard` per
contact carrying:

- **A status per mandated disclosure.** `present` is the only pass: said, in order, by the
  required speaker, inside the configured window. The others are `absent`, `out_of_order`,
  `late`, `pending`, `unverifiable` and `gap`.
- **Deterministic sentiment and vulnerability cues**, matched from the per-market cue lexicons
  in the score pack.
- **A disposition for the whole contact**, and `requires_human_review` when it is not compliant.
- **A citation on every finding**: the turn and the exact character range that produced it, so a
  reviewer is shown the words rather than a verdict.

## Why does `out_of_order` score the same as `absent`?

Because a fee disclosure made after the customer has already agreed is not a fee disclosure. The
ordering constraint is part of the obligation, not a stylistic preference, so an out-of-order
sequence fails rather than partially passing.

## What is `gap`, and why is it the top severity?

`gap` means the contact declares an obligation and the active score pack configures none.
Nothing was measured, so nothing is claimed. It is scored at the top severity because "we did not
check" and "nothing was wrong" look identical on every dashboard, and a QA system whose blind
spots read as passes is worse than no QA system.

`unverifiable` is the same instinct one level down: a disclosure said in the right order, but on
a transcript with no word timings, so a declared deadline cannot be checked. It is never quietly
rounded up to a pass.

## What is deterministic and what is not?

Every number and every verdict is deterministic. `domain/scoring_engine.py` is pure stdlib with
an explicit `as_of`, no clock and no I/O, so an auditor can re-run last quarter's assessment and
get last quarter's answer byte for byte.

Models appear in exactly three places, none of them consequential: batch speech-to-text before
the domain, a narration paragraph that restates an already-decided scorecard, and one advisory
sentence that lands in a field the engine never reads.
`tests/unit/test_model_free_scorecard.py` proves the last of those by scoring the same contact
with the classifier bound and stubbed out and requiring every other field to be identical.
[`../model-card.md`](../model-card.md) has the full boundary.

## Does a QA policy change need an engineer?

No, and that is the design. The engine knows no market and no wording. Everything a market
requires lives in a YAML score pack a compliance officer can read and diff: the mandated
wordings, the paraphrases you accept, the required speaker role, the ordering constraints, the
timing windows, the severities, the regulator instrument each requirement cites, and the cue
lexicons. Point `score_pack_path` (`CONVQA_SCORE_PACK`) at your file.

The loader is fail-closed: a requirement with no citation, no role, no steps or an undefined step
refuses at load rather than producing a scorecard that reports an unconfigured obligation as a
pass.

## Why does Japanese get a mention in the reference pack?

Because Japanese has no word separators, so phrase matching has to work over a character run
rather than over tokens. That is a property of the shared `speech-lexicon-kit` kernel, not a
special case bolted on here, which is why the reference pack ships SG, AU and JP: the third one
proves the matcher rather than decorating it.

## How does this relate to the contact-centre copilot (E1)?

They are the live half and the review half of the same market obligations, and neither owns the
other's surface.

- **E1 `contact-centre-conversations`** reminds a live agent, in the moment, from a `kind: disclosure`
  pack.
- **This service** grades the finished contact.

Point `sibling_disclosure_pack_path` (`CONVQA_SIBLING_DISCLOSURE_PACK`) at the same file E1 reads
and a market's wording is authored once, so a bank cannot tighten a wording in one system and
grade against the other. It supplements your own pack and never replaces it, because it carries
no ordering constraint and no named regulator instrument; an obligation configured in both
refuses rather than picking a winner.
`tests/unit/test_sibling_disclosure_pack.py` is the gate.

## Which sibling systems does this repo integrate rather than rebuild?

| Concern | Owner | How it appears here |
|---|---|---|
| Live agent assist and disclosure reminders | **E1** contact-centre copilot | the sibling disclosure pack, above. This repo has no live surface |
| Human review and maker-checker | **Hrz7** review console | `ports/review_router.py`, bound in all three families over the shared `review-kit`. Rule R8 |
| Model and agent promotion | **Hrz4** AI-quality gate | `eval/run_eval.py --mode gate` is the client half and refuses off the managed profile |
| Tracing and immutable WORM audit | **Hrz5** observability | `ports/observability.py`; the local audit half is tamper-evident today, the shared sink is an open binding |
| Agent discovery and entitlements | **Hrz3** agent registry | the A2A card at `/.well-known/agent-card.json`, built from the same tool table the runtime binds |
| Injection defence and output filtering | **Hrz1** guardrail gateway | NOT bound today. `COMPLIANCE.md` R1 records it as owed rather than claiming it |
| Grounded retrieval | **Hrz2** knowledge base | not applicable. This service retrieves nothing: findings come from a configured pack matched against a transcript |
| Consent and marketing screening | **Mkt6** | not applicable. This service produces no customer-facing output at all |

## How many surfaces are there, and do they agree?

Five: the FastAPI app, the argparse CLI, the agent tools, the embeddable micro-frontend and the
eval harness. They agree because they share `domain/scorecard_service.py` rather than
reimplementing it, and each routes an escalated result to human review in the same call that
produced it, so rule R8 does not hold on four surfaces out of five.

## Can we trust a demo of this?

The demo is code. Every step lives in `scripts/demo.py` and its assertion in
`scripts/walkthrough.py`, `tests/unit/test_demo_surface.py` holds the two sets equal, and
`make demo-selftest` runs the whole arc headless in CI. A claim the demo makes that nobody
verifies cannot exist, and the arc deliberately includes a step that shows a failure.
