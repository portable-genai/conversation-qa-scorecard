# FAQ: compliance, conduct, privacy and model risk

For second line, conduct, privacy and model risk. [`../../COMPLIANCE.md`](../../COMPLIANCE.md) is
the authoritative mapping from every catalog principle (P-01 to P-13) and platform rule (R1 to
R8) to a control and an evidence file. This page answers the questions that come before it.

## Can we rely on a scorecard as evidence?

That is the design intent, and the property it rests on is replayability.
`domain/scoring_engine.py` is pure stdlib with an explicit `as_of`, no clock and no I/O, so
re-running last quarter's assessment produces last quarter's answer byte for byte. Every finding
carries the turn and the character range that produced it, so the evidence is the words rather
than a verdict. No model contributes to any status, score or disposition.

What you cannot rely on without your own work: that the score pack encodes YOUR obligations
correctly. The shipped pack is a synthetic reference set for Singapore, Australia and Japan. The
mapping from an instrument to a required wording is an adopter judgement, and this repo does not
make it for you.

## Does the model see customer personal data?

No. `domain/ingestion.py` (`redaction_spans`, `redact_for_scoring`) masks personal data with the
`pii-kit` jurisdiction rows before the scoring engine, before any model call and before the
audit write. The narration brief and the advisory classifier's input are built from the
already-redacted transcript and already-redacted evidence spans. Agent tool results are masked
again on the way out, because a tool result becomes a model's context.
[`../model-card.md`](../model-card.md) states the boundary in full.

## What happens to a contact that fails?

It is HELD and ROUTED, not flagged. A scorecard that is not compliant sets
`requires_human_review` and is submitted to the `human-review-console` in the same call that
produced it (rule R8), on the API, the CLI and the agent surface alike. The managed router
REFUSES when no console is configured rather than swallowing the escalation, so a deployment
cannot ship with R8 unwired and look green. `tests/unit/test_review_routing.py` asserts the
routing rather than the flag.

## Where does the data live, and is residency enforced or merely described?

Enforced. The region is chosen once (`asia-southeast1`), carried by `config/settings.yaml`,
reported by `/healthz` and printed on the agent card, and then held at deploy time:
`infra/terraform/variables.tf` validates the effective region against the residency allowlist at
plan time, `org_policy.tf` pins `constraints/gcp.resourceLocations` to that region's location
group, and every regional resource is created in it: the CMEK key ring, the WORM log bucket,
Firestore, the BigQuery dataset, the recordings bucket and the Cloud Run service.

`infra/terraform/production_edge.tftest.hcl` is the standing gate:
`reject_region_outside_the_residency_allowlist` fails if the allowlist stops refusing, and
`residency_defaults_are_in_country` fails if any of those resources drifts off region. It runs
against a mocked provider, so `make tf-check` proves it with no project and no credentials.

To change the region you change the Terraform `region` and `allowed_regions` pair together; they
validate against each other, so an operator who moves one and forgets the other fails at plan
rather than moving recorded conversations out of jurisdiction.

## Is the audit trail admissible?

It is append-only and hash-chained, and the chain head is anchored to a file on a separate
volume, so an edit, a deletion, a reorder AND a truncated tail are all detectable.
`tests/unit/test_audit_anchor.py` proves each, including the control case that fails without the
anchor. The audit actor is the verified principal, never a field in the request body. Personal
data is masked before the write, so the WORM record carries no raw identifier.

Under `gcp` the trail is routed to a locked Cloud Logging bucket at a six-month retention floor
that the Terraform test refuses to lower.

## What about the people being recorded and scored?

Two separate populations, and it is worth being explicit about both.

- **Customers.** Their words are transcribed, redacted and matched against a pack. Retention of
  the recording, the transcript and the scorecard is an adopter decision this repo does not make;
  the storage locations are named in [`../ADOPTING.md`](../ADOPTING.md) section 5.
- **Agents.** Scoring every contact rather than a sample is a change in the surveillance of
  employees, and in several jurisdictions that carries its own consultation, notice and works
  council obligations. This is a real consequence of the product's headline feature, and it is a
  conduct and employment-law question for the adopter, not a control this repo can ship.

## Which controls are NOT covered, and who owns them?

`COMPLIANCE.md` marks these honestly rather than claiming them.

- **R1, injection defence and output filtering.** Redaction ships; the `agent-guardrail-gateway` binding
  does not. Customer and agent utterances reach the narration brief as evidence text.
- **R2, the shared observability sink.** The immutable audit half is local and tamper-evident;
  binding an observability client to `agent-observability` is open.
- **R4 and R5, the registry and the promotion gate.** The A2A card and the `--mode gate` client
  half both exist; registering with `agent-registry` and `model-quality-gate` is a deployment act.
- **P-09, network perimeter.** CMEK, least-privilege IAM, no service-account keys and a
  dry-run-first VPC-SC perimeter all ship in `infra/terraform/`. Private endpoints and a distinct
  agent identity are recorded as open.
- **R6, intake validation.** Record the `architecture-validator` reference when the project passes it.

## Who owns the regulator crosswalk?

You do. `COMPLIANCE.md` maps to the catalog's own principles and rules. The mapping from those to
a MAS TRM, CPS 234, CPS 230, HKMA or PDPA control id, and the judgement that a control is
SUFFICIENT for that regulation, depends on your risk appetite, your regulator and your existing
control library. No row in that file should be quoted as regulatory assurance.

## What model-risk evidence exists today?

The offline eval runs eight metrics in every gate, and each is proved able to fail:
`tests/unit/test_not_falsely_green.py` plants a mutant per metric and fails the build if the
metric still passes. `narration_groundedness` runs at a threshold of 1.000.

What does not exist yet is a managed-profile evaluation: the offline eval scores the deterministic
pipeline and the local narrator, not live Gemini output. Registering the bundle with `model-quality-gate` and
running a managed evaluation is the open item in [`../model-card.md`](../model-card.md), and
until it is done the managed model path is not production-cleared.
