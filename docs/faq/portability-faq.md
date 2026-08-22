# FAQ: portability and exit

For architecture, cloud and procurement. The question behind all of these is the same one: if we
adopt this, how do we leave?

## What exactly is cloud-specific here?

The `adapters/gcp/` directory and nothing else. The domain (`src/conversation_qa_scorecard/domain/`)
is pure stdlib: no web framework, no cloud SDK, no HTTP. Every boundary is a
`@runtime_checkable` Protocol in `ports/`, and which implementation binds is a line in
`config/settings.yaml`, so switching a port is configuration rather than a code edit.

Ten ports carry the whole boundary: `audit`, `identity`, `narration`, `observability`,
`review_router`, `scorecard_store`, `signals`, `speech`, `warehouse` and the evaluation gate.
`ports/speech.py` declares nothing of its own: it re-exports the shared `speech-lexicon-kit`
kernel, pinned by tag rather than vendored, so this scorecard, the contact-centre copilot and
the comms-surveillance investigator mean the same thing by "turn 4, characters 12 to 34".

## What are the three profiles?

One environment variable, `CONVQA_PROFILE`, selects the family.

| Profile | What it is | Cloud SDK |
|---|---|---|
| `local` | The SDK-free offline stack: fixture transcripts, seeded dev personas, a hash-chained SQLite WORM audit log from the commons, a deterministic narrator with no model. This is what the gate, the demo and CI run. | none |
| `gcp` | Managed: batch speech-to-text, Gemini narration, Firestore, BigQuery, Cloud Logging WORM, IAP identity. Every SDK import is LAZY, inside the method. | lazy |
| `onprem` | Fail-fast placeholders that RAISE. The client wires its own speech stack, model gateway and stores. | none |

`onprem` raising rather than silently succeeding is the point. A placeholder that returned a
plausible empty answer would make the portability claim unfalsifiable;
`tests/contract/test_behavioral_parity.py` proves the offline family ANSWERS and the exit family
REFUSES, and `make portability` runs the same tour as named checks with a pass or fail each and
prints what it does NOT prove.

## Prove the offline profile does not need the cloud SDK.

`tests/contract/_sdk_free_probe.py` imports every module with the cloud SDKs unimportable. If a
`google.` import ever escapes a method body into module scope, the offline gate goes red on the
next run rather than in somebody's air-gapped environment six months later. The whole
`make gate` is offline, credential-free and network-free by design.

## How do we get our data out?

The audit trail exports to and restores from JSON Lines, so the exit is a file copy, and
`make portability` performs an export plus a foreign reload as one of its named checks. Scorecards
are plain records in the scorecard store; the warehouse export is JSON Lines under `local` and a
BigQuery table under `gcp`, selected by `warehouse_path` and `warehouse_table`.

Your score pack is a YAML file you own and can take with you. It is the only thing that encodes
your compliance policy, and it is deliberately not embedded in code.

## What would an on-premises deployment actually take?

[`../onprem-migration.md`](../onprem-migration.md) is the written path. The short version: implement
the `onprem` adapters against your own speech service, model gateway, object store and audit
sink. The domain, every port, the score pack and the whole test suite come across unchanged,
because none of them knows what is behind a port.

The two model-dependent surfaces degrade gracefully rather than blocking: a narrator that refuses
falls back to `domain/narration.deterministic_narration`, and the advisory classifier's absence
leaves `Scorecard.advisory` empty and every other field identical
(`tests/unit/test_model_free_scorecard.py`). A compliance assessment is never blocked on a model
being reachable.

## Is the audit trail portable, and is it tamper-evident?

Both. It is append-only and hash-chained, and the chain head is anchored to a file on another
volume. The chain catches an edit, a deletion or a reorder; only the anchor catches a truncated
tail. `tests/unit/test_audit_anchor.py` proves both, including the control case that fails
without the anchor. The format is the commons' own, so an exported trail verifies outside this
process.

## Where is our data, physically?

`asia-southeast1`, pinned once and enforced at deploy time rather than described.
`infra/terraform/variables.tf` validates the effective region against the residency allowlist at
plan time, `org_policy.tf` pins `constraints/gcp.resourceLocations` to that region's location
group, and every regional resource is created in it: the CMEK key ring, the WORM log bucket,
Firestore, the BigQuery dataset, the recordings bucket and the Cloud Run service.
`make tf-check` runs `terraform test` against a mocked provider, so the refusals are proved with
no project and no credentials. See [`compliance-faq.md`](compliance-faq.md).

## What are we locked into that is not GCP?

Five pinned commons packages: `hex-service-kit`, `agent-eval-kit`, `pii-kit`,
`review-kit` and `speech-lexicon-kit`. They are ordinary Python packages pinned to commit
shas, they contain no cloud SDK, and a fork can vendor any of them. The one that is hardest to
replace is `speech-lexicon-kit`, and that is deliberate: it is the shared transcript vocabulary,
and a local copy is how two systems start disagreeing about what was said.
