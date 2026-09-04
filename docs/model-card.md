# Model card: Conversation QA and Compliance Scorecard (E3)

This is a STARTER model card. It records the model boundary as built and the controls that must be
completed before a managed deployment. The deterministic engine is the system of record; the model
is a bounded, replaceable component that restates a scorecard somebody else already decided.

## What the model does, and does not do

Three model-shaped things are in this service's path, and none of them decides anything.

- **Speech to text, before the domain.** Under `gcp`, `adapters/gcp/transcription.py` calls the
  managed batch recogniser in the residency region and assembles the result into the shared
  kernel's `Transcript`. It is the only place raw customer audio is processed. Turn assembly and
  channel-to-role binding are mechanical, not inferred, and a word timing the recogniser did not
  return is never interpolated from turn bounds: a made-up timestamp checked against a
  regulatory deadline is worse than one reported as `unverifiable`.
- **Narration, after the domain.** `ports/narration.py` hands a `NarrationBrief` in which the
  disposition, the scores and every finding are already settled, and asks for one paragraph a QA
  manager can read.
- **Advisory colour, beside the domain.** `ports/signals.py` shows a model redacted CUSTOMER
  utterances only and asks for one advisory sentence. It is never asked whether the customer is
  vulnerable: the deterministic cue lexicon in the score pack has already answered that and the
  answer is on the scorecard before this runs. The reply is wrapped in an `AdvisoryNote` with no
  severity, no polarity and no requirement id, so there is nothing in its shape a later caller
  could promote into a decision by accident, and it lands in `Scorecard.advisory`, a field the
  engine never reads.
- **Does NOT**: classify a requirement, produce a score, or decide a disposition. Whether a
  mandated disclosure was `present`, `absent`, `out_of_order`, `late`, `pending`, `unverifiable`
  or a `gap`, every score, and the contact's disposition come from `domain/scoring_engine.py`:
  pure stdlib, an explicit `as_of`, no clock and no I/O, so last quarter's assessment re-runs
  byte for byte. The wordings and windows it applies are configuration
  (`scorepacks/reference_packs.yaml` or the adopter's own file), not model judgement.

## Boundary and validation

- **Redaction happens before the model, in pure code.** `domain/ingestion.py`
  (`redaction_spans`, `redact_for_scoring`) masks personal data with the `pii-kit` jurisdiction
  rows before the engine, before the narrator and before the audit write. The brief is built from
  the already-redacted transcript and already-redacted evidence spans, so no raw identifier
  reaches a model. Agent tool results are masked again on the way out, because a tool result
  becomes a model's context.
- **The brief carries a closed figure set, and the validator uses the same one.**
  `domain/narration.allowed_figures` computes exactly the numeric strings the engine published,
  and `domain/narration.validate_narration` discards any draft that mentions a figure outside
  that set or cites an instrument the findings did not emit. Prompting and validating are two
  controls and this service uses both, because a prompt is a request and a validator is a rule.
- **A discarded draft is replaced, never repaired.** `domain/narration.grounded_or_fallback` and
  `domain/scorecard_service.py` are the single place any narrator failure (a rejected draft, a
  `None`, or a raise) becomes `deterministic_narration`, so a compliance assessment is never
  blocked on a model being reachable and a broken managed binding is never invisible.
- **The scorecard is provably model-free.** `tests/unit/test_model_free_scorecard.py` scores the
  same contact with the signal classifier bound and with it stubbed out, and requires every field
  except `advisory` to be identical. The offline classifier reports its confidence as `0.0`
  deliberately: a stand-in with no evidence for a confidence figure that invented one would put a
  number nobody computed onto a compliance artifact.
- **Escalation is routed, not flagged.** A scorecard that is not compliant sets
  `requires_human_review` and is submitted to the `human-review-console` in the same call that produced it
  (rule R8), on the API, the CLI and the agent surface alike. Reads are authorised against the
  verified principal's tenant and answer 403, never 404.
- **The eval can go red.** `narration_groundedness` runs at a threshold of 1.000 in every
  `make gate`, alongside `pack_schema_validity`, `disclosure_presence`, `script_adherence`,
  `citation_accuracy`, `vulnerability_recall`, `review_safety` and `pii_safety`. The
  not-falsely-green harness plants a mutant for each so a metric that cannot fail cannot pass.

## Adapters and profiles

| Profile | Adapters | Behaviour |
|---|---|---|
| `local` | `adapters/local/{narration,signals,transcription}.py` | No model at all. The narrator composes the same sentences the domain fallback would, drawing every figure from `brief.allowed_figures`, so it passes the grounding gate by construction and stands as proof the gate is not so strict it rejects a correct narration. The classifier returns one plainly-labelled advisory sentence at confidence `0.0`. The transcript source reads obviously synthetic JSON fixtures and performs the same turn assembly the managed adapter does. SDK-free. |
| `gcp` | `adapters/gcp/{narration,signals,transcription}.py` | Gemini for narration and advisory colour, named by `CONVQA_NARRATION_MODEL` (`narration_model` in `config/settings.yaml`), with lazy SDK imports and short constant instructions that ask the model to restate, never to assess. With no model configured or no SDK present both REFUSE loudly rather than returning an empty answer, because an adapter that quietly reports "nothing to add" is indistinguishable from a working one. The batch recogniser runs in the residency region. |
| `onprem` | `adapters/onprem/{narration,signals,transcription}.py` | Fail-fast placeholders. The client wires its own model gateway and its own speech stack; the refusal is the reversibility proof (P-12) and costs a paragraph, never a scorecard. |

## Remaining controls (TODO, repo owner)

- **Model version pinning.** `narration_model` defaults to a model family alias rather than an
  immutable version, so what runs can change under the service without a diff here. Pin the exact
  version for a managed deployment and record it in this file.
- **Budget, rate control and a kill switch.** There is no per-tenant token budget, no request rate
  limit and no switch that forces deterministic-only narration. The switch is cheap here, because
  the deterministic narration path already exists and is exercised in every offline run.
- **Evaluation of the live model.** The offline eval scores the validator and the local narrator.
  Add a managed-profile run through the `model-quality-gate` promotion gate that scores real Gemini drafts for
  groundedness and readability against the same golden scorecards.
- **Prompt-injection screening.** Customer and agent utterances reach the brief as evidence text.
  Screen the brief through the `agent-guardrail-gateway` before generation, failing closed to the
  deterministic narration when the screen is unavailable. That port is not bound in this repo.
- **Recogniser accuracy as a measured property.** Word error rate, per locale and per channel,
  is not measured here. A disclosure the recogniser mis-transcribes scores as `absent`, which is
  the safe direction but still a false finding a reviewer has to overturn. Record your measured
  rate per market before this drives an enforcement outcome.
- **Audio retention.** Recordings live in the bucket the Terraform creates. Record who may listen,
  for how long they are kept, and how a subject-access request reaches them.

Until these are complete the system is safe to run offline (deterministic engine, fixture
transcripts, no model) and the managed model path is not production-cleared.
