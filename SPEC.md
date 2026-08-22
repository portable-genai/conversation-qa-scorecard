# SPEC: Conversation QA and Compliance Scorecard (E3)

Locked decisions, pinned stack, contracts. This document is the deepest authority on intent.

## Pinned stack
- Python `>=3.12`; ruff pinned exactly (`0.15.18`); mypy strict; deploy region `asia-southeast1`.
- Commons declared by tag in `pyproject.toml` (`pii-kit@v0.0.1`, `hex-service-kit@v0.0.1`, `agent-eval-kit@v0.0.1`, `review-kit@v0.0.1`, `speech-lexicon-kit@v0.0.1`) and pinned in the lockfiles to the 40-character COMMIT each tag resolved to. A tag can be moved; a commit cannot, so a lockfile that pinned the tag would let what installs change with no diff. `tests/unit/test_repo_artifacts.py` asserts the three-way agreement offline.
- The `hex-service-kit` pin is a security floor, not a preference: the kit checks the
  service-identity policy before the token, gates the zero-secret local opening on an exact
  profile match, and binds the loopback exposure guard over both HTTP and WebSocket scopes; it
  resolves every environment read in three states, so a variable set to empty fails closed
  instead of inheriting the unset default. Never move this pin backwards.
- Installs are LOCKED: `requirements-dev.lock` and `requirements-gcp.lock` are committed and are
  what `make install`, CI and the container image install. Nothing ships from an uncommitted
  resolve.

## Contracts
- **Identity**: a request's actor is a server-verified `Principal`; the client-supplied actor is
  discarded. Local profile resolves a seeded dev persona from `X-Dev-Persona`.
- **The speech kernel is PINNED, never vendored.** `speech-lexicon-kit` owns the transcript,
  speaker-turn, word-offset, channel-role and redaction-span types, the speech ports, the
  locale-sensitive normalisation, the phrase matching and the ordered-phrase adherence
  primitives. This repo re-exports them once from `ports/speech.py` and adds only its own
  `TranscriptSourcePort`. Three sibling systems have to mean the same thing by "the agent read
  the disclosure at turn 4, characters 12 to 34"; a copy-pasted kernel is how they stop.
- **Redaction before EVERYTHING**: the order is fetch, compute spans, mask, match, score,
  narrate. The engine matches masked text, so every citation indexes text that already had its
  identifiers removed, and no model is reachable before the mask. The audit write is masked
  again on the way in, because the record is immutable.
- **Score packs are DATA, per market.** The mandated wording, the accepted paraphrases, the
  ordering constraint, the timing window, the jurisdiction, the severity, the remediation, the
  citation and the sentiment and vulnerability cue lexicons live in
  `scorepacks/reference_packs.yaml`. The engine knows no market and no wording; a bank tightens
  its QA policy by pointing `score_pack_path` at its own file. Loading is fail-closed: a missing
  citation, an undefined step, an absent role, a duplicate id, an empty wording, a non-positive
  window or an unsupported locale refuses at load rather than producing a half-parsed pack.
- **A market's disclosure wording is reviewed ONCE across the two systems that use it.** The
  contact-centre copilot (E1) publishes a `kind: disclosure` pack and reminds an agent from it;
  this scorecard READS that same artifact through `sibling_disclosure_pack_path` and grades
  against it, so a bank cannot tighten a wording in one place and be graded on the other. It
  SUPPLEMENTS this repo's own pack and never replaces it, because the sibling shape carries no
  ordering constraint and no named regulator instrument; the citation records the artifact and
  its maintaining regulator rather than inventing a publication. An obligation configured in
  both artifacts refuses rather than picking a winner, because that is the drift the shared
  shape exists to prevent.
- **Determinism**: every number and every verdict on a scorecard is pure stdlib with an explicit
  `as_of`, no clock and no I/O. A model may restate the result; it never classifies a
  requirement, produces a score or decides a disposition. `as_of` being a parameter is what makes
  a replay of last quarter's assessment reproduce last quarter's verdict.
- **Fail-closed, in six places.** An unmatched sequence is `ABSENT`; an out-of-order one is
  `OUT_OF_ORDER` and scores exactly as absent does; a declared timing window over a transcript
  with no word timings is `UNVERIFIABLE`, never a pass; an obligation the CONTACT declares that
  the ACTIVE PACK does not configure is a `GAP` at the top severity; a contact with no declared
  obligations is `INDETERMINATE` at 0.0 rather than vacuously compliant; and a wording said by
  the wrong speaker does not count.
- **Every finding cites its evidence.** A satisfied finding carries the turn index and the
  half-open character span in that turn's (redacted) text, so a reviewer is shown the words
  rather than a verdict. An absence carries no span, which is the honest rendering of one.
- **Narration is validated, not trusted.** A model draft is admitted only when every numeric
  token it contains is in the closed set of figures the engine published and every citation it
  names is one the engine emitted. A failing draft is discarded for deterministic prose; a
  scorecard is never blocked on a model.
- **The scorecard identifier is a digest** of the tenant, the contact, the pack, the transcript,
  the `as_of` and the outcome, so a re-score that changed nothing updates in place instead of
  opening a second review at the console.
- **Maker-checker (P-06) and routing (R8)**: any scorecard whose disposition is not `compliant`
  sets `requires_human_review=True` AND is routed through `ReviewRouterPort` to the Hrz7 console
  inside the same call, in the DOMAIN service, so every surface inherits it. The flag alone is
  not the escalation. The response carries `review_ref`, so a caller can tell a routed
  escalation from one that stopped here. The managed adapter refuses to run with no console
  configured rather than swallowing the escalation.
- **Tenant isolation**: a scorecard is tenant-owned data. The store filters on tenant in the
  query for a listing and is deliberately UNFILTERED for a fetch by id, because the comparison
  against the VERIFIED principal belongs in the domain, where every surface inherits it. The
  refusal is 403 and not 404: the record exists, and 404 would make the store probeable with an
  id generator. Scoring another tenant's contact is refused before a transcript is fetched.
- **The warehouse row carries no speech.** The analytics projection is flat and has no field
  that can hold an utterance; the evidence stays in the tenant-scoped store behind the 403.
- **Profile**: resolved ONCE, at import, into a `ProfileChoice` and never a bare string. Three
  states of `CONVQA_PROFILE`: UNSET is NO CHOICE (the SDK-free adapters
  still bind, but the seeded personas are refused, no service-to-service scheme is selected, every
  relaxation sees `unconfigured` and the exposure guard refuses every route to a non-loopback
  peer); SET AND EMPTY raises, so it can never inherit the unset behaviour; SET AND UNKNOWN,
  including a mis-capitalised value, raises. Only a deliberately named profile is honoured, and
  both raises happen before the process can serve anything.
- **Two derived postures, opposite directions**: `exposure_profile` drives every RELAXATION (CORS
  allowlist, the `X-Dev-Persona` allowed header, the HSTS baseline, the S2S scheme) and reads
  `unconfigured` when nobody chose; `bind_profile` drives the RESTRICTION (the loopback bound) and
  reads `local` when nobody chose. One string cannot do both without weakening one of them.
  Only `config.py` reads the variable.
- **End-user authentication is a property of the identity BINDING**, declared by the adapter
  (`VERIFIED` / `CLIENT_ASSERTED` / `UNIMPLEMENTED`) and read by the loopback exposure guard. The
  service-to-service secret authenticates a calling SERVICE and no end user, so it takes no part
  in that decision: setting it closes the S2S routes and relaxes nothing.
- **Audit integrity**: the trail is hash-chained AND externally anchored. `audit_anchor_path`
  points at a file on a different volume that every append writes the chain head to; without it
  a truncated tail is undetectable, because the shorter chain still verifies. Once store and
  anchor disagree the service refuses to append rather than re-anchoring, so an ordinary write
  cannot launder a divergence. Re-anchoring is a deliberate operator action.
- **Agent surface**: optional but scaffolded. The A2A card at `/.well-known/agent-card.json` is
  built from the same tool table the runtime binds, so advertised skills and implemented tools
  are the same set. Tool results are masked for personal data before they return, because a tool
  result becomes model context (P-04); an API response to the caller who supplied the text is
  not. Nothing in `agent/` needs a runtime to import; `build_function_tools()` is the only seam.
- **Ports**: a port is registered in five places (`PORT_PROTOCOLS`, `DEFAULT_BINDINGS`, the
  `Container` accessor, `config/settings.yaml`, and the canonical-call table) and the contract
  suite asserts set equality across all five, in both directions.
- **Demo**: the demo is code and it is asserted. `scripts/walkthrough.py` narrates eight steps
  and, at each one, checks that the service actually reached the state the narration claimed;
  `--auto --headless` runs the same steps unattended in CI. A step exists in exactly two places
  (`demo.STEPS` and `walkthrough.CHECKS`) and the two are held equal, so a narrated claim nobody
  verifies cannot exist. The demo needs no browser engine, no network and no cloud.
- **UI identity**: the browser never asserts who it is. Every client-supplied actor, tenant,
  role, ACL and authorization header is discarded before a request is forwarded; identity is
  resolved server-side and the resolved headers are attached afterwards. The service credential
  is read from the server environment only. Framing and CORS are allowlists that refuse a
  wildcard however it is written, and an empty allowlist denies rather than opening up.
- **Eval**: `--mode smoke` is the offline pre-merge check; `--mode gate` is the Hrz4 promotion
  authority. The gate fails closed.
- **Tests**: split into `unit`, `contract` and `integration`. The offline gate runs the first
  two; every integration module is marked, and that marking is itself enforced.

## Metrics and thresholds (smoke)

Every metric scores against `eval/datasets/golden_scorecards.jsonl`, an INDEPENDENT oracle whose
expectations were written by reading the synthetic transcripts and whose evidence is a quotation
resolved with `str.index`, a different algorithm from the engine's normalise-fold-and-map-back.
No metric reads the pipeline's own verdict. Each is proved able to go red against the real defect
it exists to catch (`tests/unit/test_eval_metrics.py`).

| Metric | Threshold | The defect it catches |
|---|---|---|
| `pack_schema_validity` | `1.0` | a pack with a requirement that binds no speaker, no steps or no instrument |
| `disclosure_presence` | `0.95` | an engine that reports every mandated disclosure as present |
| `script_adherence` | `0.95` | an ordering constraint that stopped being enforced |
| `citation_accuracy` | `0.99` | a span mapping that drifts, so a reviewer reads the wrong words |
| `vulnerability_recall` | `0.95` | a cue lexicon that stopped matching, so a hardship signal is lost |
| `narration_groundedness` | `1.0` | a narration gate that accepts whatever the model produced |
| `review_safety` | `1.0` | rule R8 unwired: a router that accepts an escalation and returns silently |
| `pii_safety` | `0.99` | the redactor switched off (pack scan plus a pack-independent planted literal) |
