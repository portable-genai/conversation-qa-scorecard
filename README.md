# Conversation QA and Compliance Scorecard (E3)

Post-contact deterministic conversation QA and compliance scorecard with cited disposition.

Score every contact, not a two per cent sample. Given a call recording (or a transcript that
already exists), this service decides which mandated disclosures were made, in which order,
inside which timing window, which deterministic sentiment and vulnerability cues the
conversation carries, and what the whole contact's disposition is. Every finding cites the turn
and the exact characters that produced it, so a reviewer is shown the words rather than a
verdict.

Four properties, in the order they matter:

- **The engine owns every number and every verdict.** Pure stdlib, an explicit `as_of`, no clock
  and no I/O, so an auditor can re-run last quarter's assessment and get last quarter's answer
  byte for byte. A model may restate the result for a QA manager; it never classifies a
  requirement, produces a score or decides a disposition, and a draft that mentions a figure the
  engine did not publish is discarded.
- **What a market requires is CONFIGURATION.** The mandated wordings, the approved paraphrases,
  the ordering constraints, the timing windows, the severities, the citations and the per-market
  cue lexicons live in a YAML score pack a compliance officer can read and diff. The engine knows
  no market and no wording.
- **It fails closed.** An unmatched disclosure is ABSENT; an out-of-order one scores exactly as
  absent does; a timing window that cannot be checked is UNVERIFIABLE, never a pass; and an
  obligation the contact owes that the pack does not configure is a GAP at the top severity,
  because "we did not check" looks exactly like "nothing was wrong" on every dashboard.
- **Nothing auto-executes.** Personal data is masked before the engine, the model and the audit
  write; any scorecard that is not compliant is ROUTED to a human reviewer (rule R8) rather than
  left in a flag nobody reads; and a scorecard belonging to another tenant answers 403.

## Commands

```bash
python3.12 -m venv .venv && source .venv/bin/activate
make install          # locked install from requirements-dev.lock, then the project --no-deps
make gate             # the full offline gate: lint + type + test + eval
make audit            # pip-audit over both lockfiles (needs network; a HARD gate in CI)
make lock             # re-resolve uv.lock and re-export both lockfiles after a dependency change
make test-integration # tests/integration only; needs a live project (the gate deselects it)
make run-api          # uvicorn (loopback for the no-auth local profile)
conversation_qa_scorecard contacts               # the synthetic contacts the offline profile ships
conversation_qa_scorecard score CT-SG-0002        # score one, printing the findings and citations
```

The offline gate is SDK-free and is what CI runs (via the shared reusable hard-gate workflow):

```bash
ruff check src tests && ruff format --check src tests && mypy src && \
  pytest -m 'not integration' && python eval/run_eval.py
```

The demo surface sits OUTSIDE that gate, because the gate proves the service and the demo proves
the story it is presented with. It is enforced inside the offline gate by
`tests/unit/test_demo_surface.py`, which the hosted GitHub Actions check runs, so it cannot rot
quietly:

```bash
make demo             # the presenter-paced walkthrough (see DEMO.md)
make demo-selftest    # the same walkthrough, headless and unattended, asserting every step
make demo-static      # static audit-first HTML for screenshots
make portability      # the executable portability claim, pass or fail per named check
make docs-check       # relative links resolve, fences close, no em-dash in shipped prose
make ui-install ui-check   # the micro-frontend: tsc, node tests, production build, npm audit
```

## Profiles

One env var, `CONVQA_PROFILE`, selects the adapter family:

- `local` (default) : SDK-free offline stack (seeded dev personas, hash-chained SQLite WORM audit
  from the commons). No cloud SDK. The default for dev/test/CI.
- `gcp` : managed cloud (Cloud Logging WORM, IAP identity). SDK imports are lazy.
- `onprem` : fail-fast `NotImplementedError` placeholders (the reversibility proof, P-12).

Unset means `local` adapters bind but nobody chose them. A value that is set but unknown, `Local`
and `GCP` included, raises at import: a typo must not silently pick a family. And because the
local profile's seeded personas authenticate nobody, the loopback exposure guard is registered on
the app object itself, so serving it off loopback returns 503 unless
`CONVQA_ALLOW_INSECURE_DEMO=1` says otherwise. The guard reads the identity
BINDING to decide that, never a service credential: setting
`CONVQA_S2S_TOKEN` closes the S2S routes and does not open anything else.
See `docs/runbook.md`.

## What comes from the commons

| Package | Used for |
|---|---|
| `hex-service-kit` | `Principal` / `IdentityPort` / seeded personas, fail-closed bind + CORS, `make_require_service_caller` / the app-object exposure guard / security headers (the end-user dependency is this repo's own, so a deployment that can authenticate nobody answers with a status and a reason rather than a blanket 401), the hash-chained WORM audit log, `StrEnum` taxonomies |
| `agent-eval-kit` | the `--mode smoke\|gate` scaffold, the Hrz4 gate client, the not-falsely-green harness |
| `pii-kit` | the jurisdiction PII pattern rows the ingestion step masks with, before anything else runs |
| `review-kit` | the rule R8 producer path: the review payload, the submission client and the outbox |
| `speech-lexicon-kit` | the shared speech kernel: the speech ports, the transcript / speaker-turn / word-offset / channel-role / redaction-span types, locale-sensitive normalisation, phrase matching and ordered-phrase adherence. PINNED by tag, never vendored: three sibling systems have to mean the same thing by "turn 4, characters 12 to 34". The PHRASES stay here, in the score packs |

## Surfaces

The same capability is reachable five ways, and they behave the same because they share the
domain service rather than reimplementing it: the FastAPI app (`api/`), the argparse CLI
(`cli/`), the agent tools (`agent/`, advertised on the A2A card at
`/.well-known/agent-card.json`), the embeddable micro-frontend (`ui/`) and the eval harness.
Each of them routes an escalated result to human review in the same call that produced it, so
rule R8 does not hold on four surfaces out of five.

`ui/` is a Next.js micro-frontend that runs standalone or embeds in a client application. Its
security value is that the browser never asserts who the user is: every client-supplied actor,
tenant, role and authorization header is discarded, identity is resolved server-side, the
service credential never leaves the server, and framing and CORS are per-tenant allowlists that
refuse a wildcard. **If this repo has no user-facing surface, run `make drop-ui`** rather than
leaving it half-wired; `tests/unit/test_ui_surface.py` holds the repo consistent in both
directions. See `ui/README.md`.

The tool results are masked for personal data before they return, which the API response is not:
a tool result becomes a model's context, and P-04 is about what reaches the model.

## The score pack

`src/conversation_qa_scorecard/scorepacks/reference_packs.yaml` is the reference pack, and it is
the only place that knows what a recording notice sounds like, who has to say it, how soon, in
what order, what an unmet one costs and which words evidence customer vulnerability. It ships
with Singapore, Australia and Japan configured; the Japanese pack is there because Japanese has
no word separators, and matching over a character run is a property of the shared kernel rather
than of a special case here.

Point `score_pack_path` (`CONVQA_SCORE_PACK`) at your own file to run a bank's own policy. The
loader is fail-closed: a requirement with no citation, no role, no steps or an undefined step
refuses at load rather than producing a scorecard that reports an unconfigured obligation as a
pass.

### The sibling pack

The contact-centre copilot (`contact-centre-conversations`, E1) publishes its own `kind: disclosure`
pack and reminds a live agent from it. Point `sibling_disclosure_pack_path`
(`CONVQA_SIBLING_DISCLOSURE_PACK`) at the same file and this scorecard grades against exactly
that wording set, so a market's disclosure is reviewed once rather than twice and the QA answer
does not depend on which system you ask. It supplements the pack above; an obligation configured
in both refuses rather than picking a winner.

## What a scorecard says

| Status | Meaning |
|---|---|
| `present` | said, in order, by the required speaker, inside the configured window. The ONLY pass |
| `absent` | never said on the required speaker's turns |
| `out_of_order` | every step was said, in the wrong order. A fee disclosure after the customer agreed is not a fee disclosure |
| `late` | said in order, outside the configured timing window |
| `pending` | not said yet, and the conversation is still open as of the assessment time |
| `unverifiable` | said and in order, but the transcript carries no word timings, so a declared deadline cannot be checked |
| `gap` | the contact declares this obligation and the active pack configures none. Nothing was measured, so nothing is claimed |

## Configuration

`config/settings.yaml` holds the per-port adapter map plus non-secret defaults, and it is the only
place a binding lives. `.env.example` documents every non-secret variable;
`.env.secrets.example` documents the secret NAMES with placeholder values. Every security-relevant
read resolves three states: unset, set-and-empty and set-and-valid are different, and a value an
operator deliberately emptied never inherits the more permissive unset default.
`tests/unit/test_three_state_env_reads.py` fails the build on any two-state read that ships, so
the rule is enforced rather than remembered.

**Name the profile.** `CONVQA_PROFILE` has no default. Leaving it unset is
its own state: the offline adapters still bind, but the seeded dev personas are refused, no
service-to-service scheme is selected, the dev CORS allowlist and the `X-Dev-Persona` header are
withdrawn, and the exposure guard refuses every route to any non-loopback peer. A deployment that
loses the variable fails visibly instead of serving a stranger.

Deepest authority on intent, in order: `SPEC.md` -> `ARCHITECTURE.md` -> `COMPLIANCE.md` -> this
file. `docs/practices-audit.md` records the per-check verdict. Region pinned to
`asia-southeast1`.

## License

Apache-2.0. Synthetic, obviously fictional data only.
