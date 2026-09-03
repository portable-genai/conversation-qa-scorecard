# DEMO: Conversation QA and Compliance Scorecard (E3)

Everything here runs **offline**: no cloud project, no credentials, no API key, no browser
engine, no bundler. That is the first thing to say out loud, because it is the claim the rest of
the demo rests on.

```bash
make install          # locked install from requirements-dev.lock
make demo             # the presenter-paced walkthrough (starts its own server)
```

## The eight-step walkthrough

`make demo` starts a loopback server, opens the page, and then waits for you at every step. The
narration is printed on **your terminal**, never on the page, so the audience sees only the clean
output view. At a prompt: **Enter** runs the step, a **number** jumps to that step, **r** restarts
the run, **q** quits.

Every step drives the real services. Nothing is pre-recorded, and every step is ASSERTED: if the
service does not actually reach the state the narration just claimed, the walkthrough says so and
exits non-zero.

| # | Step | The point to make |
|---|---|---|
| 1 | Service bound on the offline profile | One variable binds every port. No SDK is even imported. |
| 2 | A compliant contact | Every mandated disclosure met, in order, inside its window, each finding citing the turn and characters that satisfied it. Nothing is routed: manufacturing a review for a clean contact trains reviewers to rubber-stamp. |
| 3 | A breach | The capital-at-risk warning was never given, so the engine reports ABSENT rather than guessing, the disposition is non-compliant at critical, and the scorecard is routed to the human-review console in the same call (rule R8). Setting the flag is not the escalation; routing is. |
| 4 | A contact carrying personal data | The identifier is masked BEFORE the engine matches, before any model, and before the audit write. The engine therefore scores masked text, so every citation indexes text the identifier has already left. A deterministic hardship cue fires from the pack's lexicon, not from a model. |
| 5 | The reviewer's queue | Redacted on the wire too, against every configured jurisdiction, because the console is a shared sink. |
| 6 | The audit trail | Hash-chained, externally anchored, and exportable to JSON Lines that reload elsewhere with the chain intact. |
| 7 | A tampered record | An attacker with file access rewrites a record; the chain names exactly which one. Tamper-EVIDENT, not tamper-proof. |
| 8 | The exit profile | The same calls on `onprem`, no code edited: every unimplemented seam refuses loudly rather than dropping the work. |

Step 7 is the one to linger on. A demo where nothing ever goes wrong is a sales deck; this one
shows a failure and shows that the system detects it.

## The other three ways to run it

```bash
make demo-selftest    # unattended and headless, asserts every step, non-zero on failure
make demo-static      # demo.json plus out/index.html and out/step-*.html, for screenshots
make portability      # the executable portability claim: named checks, pass or fail each
```

`tests/unit/test_demo_surface.py` drives the whole arc inside the offline gate, and the
hosted GitHub Actions check runs that gate on every pull request and every push to main, so the
demo cannot rot silently between showings. `scripts/README.md` documents each script and the
environment overrides.

## The claims, and their bounds

State the bounds yourself. An unbounded claim is the one an auditor disproves for you.

| Claimed | Proved by | NOT claimed |
|---|---|---|
| Runs with no cloud, credentials or network | the whole demo, plus `make gate` | that the managed profile works: that needs a project and lives in `tests/integration/` |
| Consequential decisions are deterministic and replayable | step 2, step 3, `make gate` | that a model's narration is deterministic; it is not, and it never decides |
| Escalations reach a human | step 3, step 5 | that a reviewer acted; the queue shows submitted, not reviewed |
| The audit record is tamper-evident and portable | step 6, step 7, `make portability` | tamper-PROOF: file access beats any store |
| Every port is swappable and every seam is named | step 8, `make portability` | that an on-premises deployment exists, or model or infrastructure portability |

## The UI

```bash
make ui-install && make ui-dev     # http://localhost:3000, proxying to the service
```

Worth showing only if the audience cares about embedding. The point is not the screen: it is that
the browser never asserts who the user is, the service credential never leaves the server, and
framing and CORS are per-tenant allowlists that refuse a wildcard. See `ui/README.md`.

## Managed profile (gcp)

Set `CONVQA_PROFILE=gcp` and install the `[gcp]` extra; identity becomes
the platform's signed assertion and audit becomes the Cloud Logging WORM sink. This is NOT part
of the offline demo and needs a real project. See `docs/runbook.md`.
