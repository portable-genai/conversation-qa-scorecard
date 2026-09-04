# FAQ: security review

For AppSec and second-line security architecture. Everything below names the file that is the
evidence, so a reviewer can read the control rather than the claim.

## Who does the service think the caller is, and can the caller influence that?

Identity is resolved server-side and nothing the client writes contributes to it. Under `gcp`,
`adapters/gcp/identity.py` verifies the Cloud IAP-injected assertion with an explicit
`audience=` (the configured `CONVQA_IAP_AUDIENCE`) and IAP's own `certs_url=`, and checks the
issuer itself because `verify_token` does not. That audience is read in three states: unset or
deliberately emptied both REFUSE, because `audience=None` means the audience is not verified and
would accept any Google-signed token from any project. Caller faults answer 401 with the reason
kept in the log; deployment faults (no audience, no verifier installed) answer 503 naming the
fix, so a misconfiguration never reads as a rejected user.

`tests/unit/test_iap_identity.py` runs in every `make gate`, and
`tests/unit/test_iap_crypto_matrix.py` runs the real verifier over locally minted assertions in
its own CI job, which fails if that test skips.

## What stops an unauthenticated peer reaching the service in the offline profile?

`add_loopback_exposure_guard` is bound at MODULE scope in `api/app.py`, because the Dockerfile
`CMD` and `make run-api` serve the app object and a bound that only exists inside `main()` never
runs in a shipped process. `tests/unit/test_serving_path_exposure.py` is the standing gate.

The guard's posture is derived from the IDENTITY BINDING and from nothing else: a route is
authenticated when the bound adapter can produce a verified principal without trusting a header
the client wrote, and the adapter declares that (`ports/identity.py`: `VERIFIED` /
`CLIENT_ASSERTED` / `UNIMPLEMENTED`). `CONVQA_S2S_TOKEN` may never enter that decision. It
authenticates a calling SERVICE and no end user, and setting it closes the S2S routes without
opening anything else. `tests/unit/test_end_user_auth_posture.py` walks the guard's argument
through the constants it names and fails the build if a credential reappears at any depth.

Interactive docs (`/docs`, `/redoc`, `/openapi.json`) are ABSENT rather than guarded under `gcp`,
because a guard the profile has switched off is no guard.

## Can one tenant read another tenant's scorecard?

No, and the failure mode is deliberate: a scorecard belonging to another tenant answers 403,
never 404. Authorisation is derived server-side from the verified principal's tenant, not from a
tenant field in the request. `tests/unit/test_scorecard_store_and_tenancy.py` is the gate.

## What reaches a model?

Redacted text only, and only after every decision has been made. `domain/ingestion.py`
(`redaction_spans`, `redact_for_scoring`) masks personal data with the `pii-kit` jurisdiction
rows before the scoring engine, before the narrator and before the audit write, so no raw
identifier reaches a WORM record, the `human-review-console` or a model. Agent tool results are masked
again on the way out, because a tool result becomes a model's context and the API response does
not. See [`../model-card.md`](../model-card.md) for the full boundary.

## Is there PII in this repository?

Only synthetic. Every shipped transcript, contact id and eval case uses obviously fictional
parties and `.example` domains. The national identifiers in the fixtures exist so a redaction
check has an independent literal to look for.

## How are secrets handled?

No secret value is committed. `config/settings.yaml` and `.env.example` carry names and
non-secret defaults; `.env.secrets.example` carries the NAMES with placeholder values, and
`tests/unit/test_repo_artifacts.py` fails the build if a real-looking value appears in either.
Inbound and outbound credentials are deliberately distinct variables: this service's own
`CONVQA_S2S_TOKEN` is not the `HUMAN_REVIEW_S2S_TOKEN` it presents to the review console.

Every security-relevant environment read resolves three states. Unset, set-and-empty and
set-and-valid are different, and a value an operator deliberately emptied never inherits the
more permissive unset default. `tests/unit/test_three_state_env_reads.py` walks the AST of
`src/`, `scripts/` and `eval/` and fails the build on any two-state read that ships;
`ui/tests/three-state-env-reads.test.mjs` does the same for every shipped `.mjs`, `.ts` and
`.tsx` in the micro-frontend.

## What is the supply-chain posture?

Both lockfiles are committed and installed with `--no-deps` by `make install`, by CI and by the
Dockerfile, with the catalog commons pinned to 40-character COMMIT shas rather than tags,
because a tag can be moved and a moved tag changes what installs with no diff. The base image is
digest-pinned, Actions are SHA-pinned, dependabot covers every ecosystem the repo actually has,
and `pip-audit` is a hard CI failure rather than an advisory. `tests/unit/test_repo_artifacts.py`
asserts each of those from inside the repo, including asking git whether each pinned sha is a
commit object rather than an annotated tag object.

## What does the browser boundary look like?

The browser never asserts who it is. In `ui/`, every client-supplied actor, tenant, role, ACL
and authorization header is discarded before forwarding (`ui/lib/embed-policy.mjs`), identity is
resolved server-side (`ui/lib/identity-policy.mjs`), and the service credential is read from the
server environment so it never reaches a bundle. Framing and CORS are per-tenant allowlists that
refuse a wildcard, and an unset tenant allowlist denies. If a deployment has no user-facing
surface, `make drop-ui` removes the UI, its npm dependabot ecosystem and its CI job in one
consistent step.

## What is explicitly NOT in scope here?

- **Injection defence and output filtering.** That is the `agent-guardrail-gateway`'s job. This repo
  redacts but does not screen, and `COMPLIANCE.md` R1 records it as an open binding rather than
  claiming it.
- **The review console.** Escalations are routed to `human-review-console`; the console itself is that system.
- **Trace collection.** Spans go to `agent-observability`; this repo has no observability backend of its own.
- **Network perimeter.** `infra/terraform/vpc_sc.tf` stands up a dry-run-first VPC-SC perimeter,
  but private endpoints and the egress rule that reaches the review console and nothing else are
  recorded as open in `COMPLIANCE.md` P-09.
