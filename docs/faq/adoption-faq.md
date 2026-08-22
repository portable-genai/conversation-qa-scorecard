# FAQ: adopting and forking this repo

For the engineering lead who has been handed this repository. The long form is
[`../ADOPTING.md`](../ADOPTING.md); this page answers the questions that come first.

## How much of this do we have to change?

Most forks change one file: the score pack. `domain/scoring_engine.py` has no market and no
wording in it, so a bank's QA policy is `score_pack_path` (`CONVQA_SCORE_PACK`) pointed at your
own YAML. If your artifact shape genuinely differs (a status this taxonomy cannot express, a
finding type that is not a disclosure) you are into `domain/models.py`, and that is a bigger
conversation than an adoption.

## How do we rebrand it?

`scripts/rename_fork.py`, in one pass, preview first:

```bash
python scripts/rename_fork.py --package acme_conversation_qa \
    --env-prefix ACMECONVQA --resource acme-conversation-qa \
    --name-prefix acme-convqa --dry-run
```

It rewrites the package name, the `CONVQA` environment prefix, the distribution and resource id
and the Terraform `name_prefix` default, then renames `src/conversation_qa_scorecard/`. It writes
nothing without `--yes`. There is no `--cli` flag because `[project.scripts]` names the console
script after the package, and no `--dist` flag because the distribution name, the GitHub id, the
A2A agent-card name and the Hrz4 eval bundle id are the same one literal that `--resource`
renames.

Recreate the venv afterwards: the distribution name changed, so an existing editable install
points at a package that no longer exists.

## Which files will conflict when we pull upstream fixes?

[`../ADOPTING.md`](../ADOPTING.md) section 2 has the list. The short version: take our changes to
`domain/kernel.py`, `domain/scoring_engine.py`, `domain/narration.py`, `ports/`,
`tests/contract/`, `eval/run_eval.py` and the CI workflows; expect to own your score pack, your
fixtures, `adapters/onprem/*`, UI theming, the golden eval dataset and the regulator crosswalk
in `COMPLIANCE.md`. Track upstream by tag and rebase rather than merging `main` continuously.

## What are the real extension points?

- **A new market**: a block in your score pack. No code.
- **A new adapter**: the class under `adapters/<family>/`, the same `module:Class` target in
  `config.DEFAULT_BINDINGS` AND `config/settings.yaml`, and any new variable in `.env.example`.
  `tests/unit/test_settings_file.py` fails if the two binding tables disagree.
- **A new port**: five places, or it runs with no enforcement at all. `ports/__init__.py`
  (`PORT_PROTOCOLS`), `config.DEFAULT_BINDINGS`, a `Container` accessor, `config/settings.yaml`,
  and a `PortCase` in `tests/contract/canonical.py`, then bound in all three families.
  `tests/contract/test_port_parity.py` asserts set equality across the five.
- **A new agent tool**: the callable plus its skill in `agent/agent_card.py`. The card and the
  tool table are compared for set equality, so an advertised tool that does not exist, or a tool
  nobody advertises, fails the gate.

[`../../CONTRIBUTING.md`](../../CONTRIBUTING.md) carries the file-by-file walkthrough with the
test that enforces each row.

## How do we know a change did not break anything?

```sh
make gate            # ruff, ruff format, mypy strict, the suite except integration, eval
make tf-check        # terraform validate, fmt and test against a MOCKED provider
make demo-selftest   # the whole demo arc, headless, asserting every narrated claim
make portability     # the exit tour, pass or fail per named check
make docs-check      # relative links, code fences, no em-dash in shipped prose
make audit           # pip-audit over both lockfiles (the one step that needs the network)
```

`make gate` is deliberately offline, credential-free and network-free. If a change makes the gate
need a cloud project, the change is wrong, not the gate.

## The evals all report 1.000. Should we believe them?

Only because each one is proved able to report something else.
`tests/unit/test_not_falsely_green.py` hands every metric a planted mutant and fails the build if
the metric still passes. The eight are `pack_schema_validity`, `disclosure_presence`,
`script_adherence`, `citation_accuracy`, `vulnerability_recall`, `narration_groundedness`,
`review_safety` and `pii_safety`. A metric that cannot go red is not a metric.

Note the corollary for a fork: you inherit a green gate that measures the WRONG market until you
rebuild `eval/datasets/` for your pack.

## How do we track upstream releases?

By git tag, plus the version in `pyproject.toml` (`0.2.0` at the time of writing). Record your
baseline tag when you fork, so you can see exactly which upstream fixes you have and have not
taken.

## What do we have to supply that is not in the repo?

1. **A score pack**, signed off by compliance. Everything else is downstream of it.
2. **Transcripts or recordings**, and a decision about which. If you feed recordings, the managed
   batch recogniser runs and you own its accuracy per locale.
3. **Durable storage**: `scorecard_path` for the scorecard store, `warehouse_table` for the
   analytics export. The defaults are ephemeral on purpose.
4. **An IdP**, configured on the deployed service, plus `CONVQA_IAP_AUDIENCE`.
5. **An Hrz7 endpoint**, or nothing that is non-compliant reaches a human.

## What is still open?

[`../practices-audit.md`](../practices-audit.md) carries the honest per-check verdict and names
the work list. The items that need your network and your project rather than a code change are
the Hrz1 guardrail binding, the Hrz5 observability binding, registering the metric bundle with
Hrz4, and the private-egress rule. The Terraform in this repo is validated and tested offline
and has never been applied.
