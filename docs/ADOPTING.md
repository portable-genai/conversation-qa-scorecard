# Adopting this repo as your base

This repository (E3, the Conversation QA and Compliance Scorecard) is a **common base** that a
bank, insurer or other regulated institution forks to build its own **post-contact compliance
QA**: a service that scores every contact rather than a two per cent sample, decides which
mandated disclosures were made, in which order and inside which timing window, and cites the
turn and the exact characters behind every finding. It ships a reusable hexagonal core (a
pure-stdlib domain, typed ports, three swappable adapter families, a green offline gate) plus a
fully worked QA vertical with Singapore, Australia and Japan configured, which you keep, retune,
or replace with your own market packs.

This guide is the step-by-step for making it yours. It has two halves: a **mechanical rebrand**
(one script) and the **human decisions** the script cannot make for you.

> Related reading: [`../ARCHITECTURE.md`](../ARCHITECTURE.md) (the port table and the topology),
> [`../CONTRIBUTING.md`](../CONTRIBUTING.md) (the file-by-file touch list for a new port or
> adapter), [`model-card.md`](model-card.md) (the model boundary), and the [`faq/`](faq/)
> directory.

---

## 1. What you keep vs what you rewrite

The boundary between reusable machinery and your QA policy is deliberate and physical: the
engine knows no market and no wording, and every wording lives in a YAML pack a compliance
officer can read and diff.

| Layer | Where | For a new market or policy |
|---|---|---|
| **Vertical-neutral machinery** | `domain/kernel.py`, `domain/serialization.py`, `domain/pii.py`, every Protocol in `ports/` (`ports/speech.py` is a pure re-export of the shared `speech-lexicon-kit` kernel), the container wiring in `config.py`, and the commons | keep untouched |
| **Policy (your wordings and numbers)** | the score pack: `scorepacks/reference_packs.yaml` or your own file behind `score_pack_path` (`CONVQA_SCORE_PACK`). Mandated wordings, accepted paraphrases, required speaker roles, ordering constraints, timing windows, severities, regulator citations and per-market cue lexicons | change by configuration, never by editing the engine |
| **Vertical (the QA artifacts)** | `domain/models.py` (`Scorecard`, the finding types, `AdvisoryNote`), `domain/scoring_engine.py`, `domain/ingestion.py`, `domain/narration.py`, `domain/scorecard_service.py`, the transcript fixtures, the eval golden set and the UI views | rewrite or reseed only if your artifact shape genuinely differs |

Most forks change the pack and nothing else. That is the design: `domain/scoring_engine.py` has
no market in it, and the loader is fail-closed, so a requirement with no citation, no role, no
steps or an undefined step refuses at load rather than reporting an unconfigured obligation as
a pass.

## 2. Core-vs-adopter-owned files (so upstream merges stay mechanical)

- **Upstream-owned** (take our changes): `domain/kernel.py`, `domain/scoring_engine.py`,
  `domain/narration.py`, `ports/`, `tests/contract/`, the eval harness mechanics
  (`eval/run_eval.py`), the CI workflows, and the `Container` wiring in `config.py`.
- **Adopter-owned** (yours; expect to edit): your score pack, the transcript fixtures,
  `adapters/onprem/*`, UI theming and branding, the golden eval dataset in `eval/datasets/`, and
  the regulator crosswalk section of [`../COMPLIANCE.md`](../COMPLIANCE.md).

Track upstream by git tag, and rebase your adopter-owned changes onto each release rather than
merging `main` continuously.

## 3. The mechanical rebrand (one script)

`scripts/rename_fork.py` rewrites the package name (`conversation_qa_scorecard`, which is also
the console-script name), the `CONVQA` environment prefix, the distribution and resource id
(`conversation-qa-scorecard`) and the Terraform `name_prefix` default, in one pass. Preview
first, then apply:

```bash
# Preview (writes nothing):
python scripts/rename_fork.py --package acme_conversation_qa \
    --env-prefix ACMECONVQA --resource acme-conversation-qa \
    --name-prefix acme-convqa --dry-run

# Apply:
python scripts/rename_fork.py --package acme_conversation_qa \
    --env-prefix ACMECONVQA --resource acme-conversation-qa \
    --name-prefix acme-convqa --yes

# Then recreate the environment (the distribution name changed) and prove it is green:
python3.12 -m venv .venv && source .venv/bin/activate
make install
make gate
```

There is deliberately no `--cli` flag: `[project.scripts]` names the console script after the
package, so `--package` renames both and a second flag could only drift out of step. There is no
`--dist` flag either: the distribution name, the GitHub id in `[project.urls]`, the A2A
agent-card name and the Hrz4 eval bundle id are the same one literal, and `--resource` renames
it. Add `--include-docs` to sweep Markdown prose too. The script deliberately does NOT touch the
human decisions below.

## 4. The human decisions (the script can't make these)

1. **Region and residency.** The build pins `asia-southeast1`. Change it in BOTH places: `region`
   in `config/settings.yaml`, and the Terraform `region` and `allowed_regions` pair in
   `infra/terraform/variables.tf`, which are validated against each other at plan time so an
   unapproved region fails before any recording, transcript or scorecard leaves jurisdiction.
   Every regional resource follows it: the CMEK key ring, the WORM log bucket, Firestore, the
   BigQuery dataset, the recordings bucket and the Cloud Run service. Prove the change with
   `make tf-check`, which runs against a mocked provider and needs no project and no
   credentials. See [`runbook.md`](runbook.md).
2. **Identity and your IdP.** This repo owns no login flow. Under `gcp` the identity adapter
   verifies the Cloud IAP-injected assertion and refuses when `CONVQA_IAP_AUDIENCE` is unset or
   emptied; under `local` it seeds dev personas that authenticate nobody; under `onprem` it is a
   client-IdP placeholder that raises. Configure IAP on the deployed service and set the
   audience, or implement the `onprem` adapter against your own issuer.
3. **The score pack, which is your compliance position.** This is the main act of adoption. Point
   `score_pack_path` at your own file and configure, per market: the mandated wordings and the
   paraphrases you accept, which speaker role owes each one, the ordering constraints, the
   timing windows, the severity of each miss, the regulator instrument each requirement cites,
   and the vulnerability and sentiment cue lexicons. An obligation a contact declares that your
   pack does not configure scores as a `gap` at the top severity, because "we did not check"
   looks exactly like "nothing was wrong" on every dashboard.
4. **The sibling disclosure pack.** If you also run the contact-centre copilot (E1,
   `contact-centre-conversations`), point `sibling_disclosure_pack_path`
   (`CONVQA_SIBLING_DISCLOSURE_PACK`) at the same `kind: disclosure` file it reminds agents
   from, so a market's wording is authored once and the QA answer does not depend on which
   system you ask. It supplements your pack and never replaces it; an obligation configured in
   both refuses rather than picking a winner.
5. **Storage and retention.** `scorecard_path` (`CONVQA_SCORECARD_PATH`) defaults to `:memory:`,
   which is right for the gate and wrong for anything durable. `warehouse_table`
   (`CONVQA_WAREHOUSE_TABLE`) must name your `project.dataset.table` or the managed export
   REFUSES rather than sending an analytics feed nowhere. Decide the retention schedule for
   recordings, transcripts and scorecards, and the legal basis for each.
6. **Reference data is fictional.** Every shipped transcript, contact id and eval case uses
   obviously fake parties and `.example` domains. Replace them with your own synthetic data.
   **Do not run against real recorded contacts without your own legal, privacy and model-risk
   sign-off.**
7. **Eval golden set.** Rebuild `eval/datasets/` and the thresholds for your pack: a fork
   inherits a green gate that measures the WRONG market until you do. The eight metrics and the
   not-falsely-green harness (`tests/unit/test_not_falsely_green.py`) are generic; the golden
   scorecards are yours.
8. **Deployment posture.** Review the Dockerfile (digest-pinned base, non-root),
   `infra/terraform/` (Org Policy, CMEK, the VPC-SC perimeter, the locked WORM log bucket) and
   the loopback-by-default API binding before you expose anything.

## 5. Do not duplicate the platform

This repo is one system in a catalog of composable GRC systems. Several concerns it *touches*
are owned by sibling services; integrate rather than rebuild them. See
[`faq/features-faq.md`](faq/features-faq.md) for the full boundary map.

- **E1** contact-centre copilot (`contact-centre-conversations`): the live half of the same market
  obligations. It reminds an agent from the disclosure pack; this service grades against it.
  Neither owns the other's surface.
- **Hrz7** human-review console: every non-compliant scorecard is routed there in the same call
  that produced it, over the shared `review-kit` (rule R8). You wire your endpoint; you do
  not re-implement the console.
- **Hrz4** AI-quality and model-risk gate: owns promotion. `eval/run_eval.py --mode gate`
  delegates the verdict to it and refuses to run off the managed profile.
- **Hrz5** observability and immutable WORM audit: trace spans and audit events go there.
- **Hrz3** agent registry: this agent publishes its A2A card at
  `/.well-known/agent-card.json` for discovery.
- **Hrz1** guardrail gateway: the injection-defence hop for the narration brief. Not bound in
  this repo today; see [`model-card.md`](model-card.md).
- **Hrz2** enterprise knowledge base: **not** integrated, and should not be. This service
  retrieves nothing. Its findings come from a configured pack matched against a transcript, so
  there is no retrieval path to ground.

## 6. Adoption checklist

- [ ] Ran `scripts/rename_fork.py`, recreated the venv, `make gate` green.
- [ ] Set the region in `config/settings.yaml` AND the Terraform `region` / `allowed_regions`
      pair, and `make tf-check` still passes.
- [ ] Configured IAP on the deployed service and set `CONVQA_IAP_AUDIENCE`, or implemented the
      `onprem` identity adapter.
- [ ] Authored your own score pack and pointed `score_pack_path` at it, with your compliance
      function signing off every wording, window and severity.
- [ ] Decided whether the E1 sibling disclosure pack applies and wired it if so.
- [ ] Pointed `scorecard_path` at durable storage and `warehouse_table` at your dataset.
- [ ] Set the retention schedule and legal basis for recordings, transcripts and scorecards.
- [ ] Replaced every synthetic transcript and fixture.
- [ ] Rebuilt the eval golden set and thresholds for your pack.
- [ ] Reviewed the deploy posture (Dockerfile, Terraform, bind address) before exposing anything.
- [ ] Wired your Hrz7 endpoint and decided which sibling services you integrate vs stub.
- [ ] Recorded your baseline upstream tag so you can take future fixes.
