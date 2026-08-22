# ARCHITECTURE: Conversation QA and Compliance Scorecard (E3)

Hexagonal ports-and-adapters. A pure-stdlib domain core speaks only to ports (`typing.Protocol`s);
adapter families implement them; one env var (`CONVQA_PROFILE`) swaps the
whole stack with no domain edits.

Profile selection is an exact lookup. Every declared profile has an entry for every port; when
two profiles intentionally reuse one adapter, both entries name it. A missing local or exit
binding never inherits `gcp`, so it cannot import a managed SDK or change data custody silently.

`local` runs the real API, orchestration and deterministic domain with local or synthetic edges.
It may reduce transcription/narration quality, throughput, durability, enterprise identity,
managed safety and telemetry, but it does not change figures, evidence links, escalation rules
or schemas. `make portability` executes this boundary. A construction-only primary managed
operation must block API startup and Terraform serving authorization until its integration test
exists.

## Layout (`src/conversation_qa_scorecard/`)
- `domain/` : pure stdlib plus the pinned speech kernel, no cloud or framework imports.
  `kernel.py` (vertical-neutral types, `StrEnum` taxonomies from the commons), `models.py` (this
  vertical's artifacts: requirements, findings, evidence spans, signals, the scorecard),
  `pii.py` (the jurisdiction pattern selection and order), `ingestion.py` (turn assembly and
  redaction, both deterministic, both before any model), `scoring_engine.py` (the engine that
  owns every number and every verdict), `narration.py` (the grounding gate a model draft has to
  pass), `scorecard_service.py` (the one path a contact takes, where the tenant boundary and
  rule R8 are enforced), `serialization.py` (the record, in plain JSON, both ways) and
  `errors.py` (the typed refusals, each carrying its HTTP status).
- `score_pack.py` and `scorepacks/` : the pack loader and the reference pack. OUTSIDE `domain/`
  because it reads YAML from disk, and the domain core stays pure.
- `service.py` : the one place the domain service is wired from the container, so no surface can
  build a narrower one and quietly stop honouring rule R8.
- `contacts.py` : resolving a contact id, and saying so honestly when this profile has no
  contact catalogue bound rather than inventing a record with no obligations.
- `ports/` : `@runtime_checkable` Protocols, re-exported once with the `PORT_PROTOCOLS` map.
  Two vocabularies are NOT redeclared: the commons owns `IdentityPort`, and
  `speech-lexicon-kit` owns the transcript types and the speech ports, re-exported by
  `ports/speech.py` alongside this repo's own `TranscriptSourcePort`. `identity.py` adds
  this service's own identity vocabulary: what an adapter DECLARES about the end-user
  authentication it provides (`VERIFIED` / `CLIENT_ASSERTED` / `UNIMPLEMENTED`), which is what the
  loopback exposure guard reads, plus the refusal type that carries a status and a reason when no
  end user can be authenticated at all.
- `adapters/{local,gcp,onprem}/` : one adapter per port per profile. GCP imports are lazy.
  `adapters/_review_payload.py` is the shared, redacted conversion to the review kit's wire shape.
- `config.py` : `Settings` + `Container` (lazy DI, dotted `module:Class` bindings loaded from
  `config/settings.yaml`).
- `api/` : FastAPI app wired with the commons identity / S2S / fail-closed helpers.
- `cli/` : a stdlib argparse CLI.
- `agent/` : the optional-but-scaffolded agent surface. `tools.py` holds plain Python callables
  that delegate to the domain services (no business logic of their own) and route escalations
  like every other surface; `agent_card.py` builds the A2A discovery card served at
  `/.well-known/agent-card.json`. Nothing here needs ADK or a cloud SDK to import or test:
  `build_function_tools()` is the single lazily-imported runtime seam.

## Surfaces outside `src/`
- `scripts/` : the demo surface. `demo.py` holds the scripted arc and drives the REAL services;
  `render_ui.py` paints its panels as dependency-free static HTML; `demo_server.py` serves the
  same panels live, one real step per click; `walkthrough.py` drives that server over loopback
  HTTP and asserts every step, which is what lets the presenter tool double as the unattended
  self-test. `portability_demo.py` and `check_docs_links.py` are standalone checks. Nothing here
  is imported by `src/`, and `.dockerignore` keeps all of it out of the serving image.
- `ui/` : the embeddable Next.js micro-frontend. Its security boundary is one policy module
  (`lib/embed-policy.mjs`) shared by the document-layer `proxy.ts` and the same-origin API route,
  plus one server-side identity module (`lib/server/identity.ts`). The browser never asserts an
  actor and never holds the service credential. Delete it with `make drop-ui` if this repo has no
  user-facing surface; the gate checks that decision for consistency in both directions.

## Test layout (`tests/`)
`unit/` (one module or service, driven by the REAL local adapters), `contract/` (the boundary
claims: conformance, the five-way port drift guard, behavioural parity), `integration/` (needs a
live service; marked so the offline gate deselects the whole directory) and `fixtures/` (shared
data only). `contract/canonical.py` holds ONE canonical request per port, so the structural and
behavioural suites cannot quietly assert different things.

## Request pipeline (`ScorecardService.score_contact`)

    authorise the tenant   (403 before anything is fetched)
      -> fetch the transcript
      -> compute redaction spans and MASK          (pure code, P-04)
      -> match the pack's wordings on masked text  (speech kernel)
      -> score                                     (the engine: every number, every verdict)
      -> advisory colour + narration               (optional, validated, never consequential)
      -> already-redacted WORM audit write         (R2)
      -> persist, and ROUTE a failing scorecard to Hrz7   (R8)
      -> export one flat row to the warehouse      (no utterance leaves)

The order is the design. Everything after the mask sees masked text, which is why a citation can
be shown to a reviewer verbatim and why the narration brief is safe to hand to a model at all.
The audit actor and the review maker are both the verified `Principal`, never the request body,
and the routing happens inside the same call that produced the result, in the DOMAIN service, so
every surface inherits it rather than each remembering.

## The port table
| Port | local | gcp | onprem |
|---|---|---|---|
| `AuditSinkPort` | hash-chained SQLite WORM (commons) | Cloud Logging WORM (lazy) | placeholder |
| `IdentityPort` | seeded personas (commons) | IAP assertion (lazy) | placeholder |
| `NarrationPort` | deterministic offline drafter | Gemini, schema-validated downstream (lazy) | placeholder |
| `ReviewRouterPort` | review-kit outbox (offline, inspectable) | Hrz7 service intake over S2S | placeholder |
| `ScorecardStorePort` | tenant-scoped SQLite, JSON documents | Firestore in the residency region (lazy) | placeholder |
| `SignalClassifierPort` | offline advisory note (colour only) | Gemini advisory note (lazy) | placeholder |
| `TranscriptSourcePort` | synthetic fixture reader | managed batch speech-to-text (lazy) | placeholder |
| `WarehouseExportPort` | JSON Lines, inspectable | BigQuery in the residency region (lazy) | placeholder |

The on-prem placeholders RAISE. A review router that silently returned would convert every
consequential result into an unreviewed one, which is worse than a missing feature.

A port is registered in FIVE places: `ports/__init__.py` (`PORT_PROTOCOLS`), `config.py`
(`DEFAULT_BINDINGS` and a `Container` accessor), `config/settings.yaml` and
`tests/contract/canonical.py`. `tests/contract/test_port_parity.py` asserts set equality across
all five, so a port that is bound but unregistered (or registered but unbound) fails the build
instead of running with no enforcement. The full touch list is in `CONTRIBUTING.md`.

## Audit integrity
The local WORM log is hash-chained AND anchored: `audit_anchor_path` points at an external file,
on a different volume, that every append writes the chain head to. The chain alone catches an
edit, a deletion or a reorder; only the anchor catches a truncated tail, because a truncated
chain still verifies. `tests/unit/test_audit_anchor.py` proves both halves, including the
control case where the same truncation goes undetected without an anchor.
