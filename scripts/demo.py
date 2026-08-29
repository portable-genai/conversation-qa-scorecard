"""The scripted, offline demo: the REAL services, synthetic data, an audit-first output view.

This is the demo as CODE (practices check F1), not a slide deck and not a recording. Every step
below drives the actual scoring engine, the actual score pack, the actual hash-chained audit
store and the actual rule-R8 review router over the ``local`` profile, so a step that stops
being true stops passing rather than stops being mentioned.

Three properties make it worth running in front of somebody:

* **Nothing is faked.** No stub service, no pre-baked JSON. The findings, the scores, the
  dispositions, the audit records, the routing references and the tamper verdict are produced
  by the shipped code over the shipped synthetic transcripts.
* **It is bounded.** The demo proves an offline, single-process seam. It does not prove
  cross-host deployment, a live console, or the managed profile; those need a cloud project and
  live in ``tests/integration/``.
* **It is replayable.** Same inputs, same output, every time, because the consequential
  decision is deterministic and takes ``as_of`` as a parameter. That is what makes it safe to
  run live.

Run it directly to write the audit-view JSON, then render that JSON to static pages::

    make demo-static

or drive it one step at a time with ``demo_server.py`` and ``walkthrough.py`` (``make demo``).

Every party, address and identifier here is obviously fictional: ``.example`` domains, RFC 5737
and RFC 3849 literals, and a synthetic national id that exists only to prove redaction happened.

MAINTAINER NOTE: this file is rendered from a template, so no line may change length with the
package or service name. Every cookiecutter value is bound to a short module constant below and
referenced through it, and every import line is short enough that a long package name cannot
push it past the formatter's limit.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hex_service_kit.audit import HashChainedAuditLog
from hex_service_kit.identity import RequestContext
from hex_service_kit.serialization import to_jsonable

from conversation_qa_scorecard.config import (
    Settings,
    build_container,
)
from conversation_qa_scorecard.domain import (
    kernel,
    models,
)
from conversation_qa_scorecard.domain.ingestion import (
    redact_for_scoring,
)
from conversation_qa_scorecard.domain.narration import (
    narration_brief,
)
from conversation_qa_scorecard.domain.pii import (
    JURISDICTIONS,
)
from conversation_qa_scorecard.domain.scoring_engine import (
    ScoringEngine,
)
from conversation_qa_scorecard.domain.serialization import (
    scorecard_to_row,
)
from conversation_qa_scorecard.ports.signals import (
    SignalRequest,
)
from conversation_qa_scorecard.score_pack import (
    pack_for_market,
)
from conversation_qa_scorecard.service import (
    build_service,
    pack_for_contact,
)


def loaded_cloud_sdks() -> tuple[str, ...]:
    """Every managed-SDK module currently importable in THIS interpreter, sorted.

    Public because the demo, the walkthrough's checks and the test suite all ask the same
    question and must not each answer it slightly differently.
    """
    return tuple(sorted(name for name in sys.modules if name.split(".")[0] == "google"))


#: Rendered identity, bound once so no other line's length depends on how long a name is.
SERVICE_NAME = "Conversation QA and Compliance Scorecard"
CATALOG_ID = "E3"
REPOSITORY = "conversation-qa-scorecard"

# --------------------------------------------------------------------------------------- #
# Synthetic data. Fictional parties, .example domains, RFC 5737 / RFC 3849 literals only.
# --------------------------------------------------------------------------------------- #

#: The VERIFIED principal the demo attributes work to. A client never asserts this.
ACTOR = "analyst@bank.example"
TENANT = "demo-bank"

#: A planted identifier, so the redaction panel has an independent literal to look for rather
#: than trusting the pattern pack to agree with itself.
PLANTED_NRIC = "S1234567D"

#: The moment the demo assesses at. Explicit, because the engine takes ``as_of`` as a parameter
#: and a demo whose verdicts drifted with today's date would not be replayable in front of
#: anybody.
AS_OF = datetime(2026, 7, 20, 4, 0, tzinfo=UTC)

#: The shipped synthetic contacts each beat uses. Every one is a file under
#: ``src/conversation_qa_scorecard/transcripts/``, so the demo and the tests score the same
#: conversations and a change to one is visible in the other.
COMPLIANT_CONTACT = "CT-SG-0001"
BREACH_CONTACT = "CT-SG-0002"
PII_CONTACT = "CT-SG-0004"

# --------------------------------------------------------------------------------------- #
# The presenter arc
# --------------------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Step:
    """One presenter beat: what it shows, and the sentence the presenter reads aloud."""

    key: str
    label: str
    narration: str


#: The scripted arc, in order. ``walkthrough.py`` asserts the server reaches each key in turn
#: and carries an expectation per key, so a step added here without an expectation there fails
#: the self-test rather than silently extending the demo.
STEPS: tuple[Step, ...] = (
    Step(
        key="opened",
        label="Service bound on the offline profile",
        narration=(
            "The whole stack is bound from one settings file: no cloud project, no credentials, "
            "no SDK. Every port has an offline implementation, which is why this demo and the "
            "gate both run on a plane."
        ),
    ),
    Step(
        key="routine",
        label="A compliant contact: every disclosure cited, NOTHING escalated",
        narration=(
            "A Singapore advice call where every mandated disclosure was made, in the order "
            "the pack requires, inside its timing window. Each finding cites the turn and the "
            "exact characters that satisfied it. Nothing is routed for review, because "
            "manufacturing a review for a clean contact trains reviewers to rubber-stamp."
        ),
    ),
    Step(
        key="escalation",
        label="A breach: the risk warning was never given, so it is routed (rule R8)",
        narration=(
            "The same script with the capital-at-risk warning missing. The engine reports "
            "ABSENT rather than guessing, the disposition is non-compliant at critical, and "
            "the scorecard is handed to the human-review console in the same call that "
            "produced it. Setting the flag is not the escalation; routing is."
        ),
    ),
    Step(
        key="redaction",
        label="Personal data is masked BEFORE the engine, the model and the audit write",
        narration=(
            "A contact whose caller reads out a national id and describes losing their job. "
            "The identifier is masked on the way in, so the engine matches masked text and "
            "every citation indexes text that already had its identifiers removed. The "
            "vulnerability cue is found by a deterministic lexicon, not by a model."
        ),
    ),
    Step(
        key="review_queue",
        label="What the reviewer receives, already redacted on the wire",
        narration=(
            "The outbound review queue. The console is a SHARED sink, so payloads are redacted "
            "against every configured jurisdiction, not only the one this case came from."
        ),
    ),
    Step(
        key="audit",
        label="The audit trail verifies, and exports in an open format",
        narration=(
            "The trail is append-only and hash-chained, with an external head anchor on a "
            "separate volume. It exports to JSON Lines and reloads into a fresh store with "
            "every link intact: the record is yours, not this codebase's."
        ),
    ),
    Step(
        key="tamper",
        label="A rewritten record is DETECTED, not merely discouraged",
        narration=(
            "An attacker with file access drops the append-only triggers and rewrites one "
            "record. The store cannot prevent that. The hash chain names the exact record that "
            "broke, which is the honest guarantee: tamper-EVIDENT, not tamper-proof."
        ),
    ),
    Step(
        key="portability",
        label="The exit path fails fast instead of failing silently",
        narration=(
            "The same calls on the on-premises profile, with no code edited and no domain "
            "module touched. Every unimplemented seam refuses loudly. A placeholder that "
            "returned successfully would convert an escalation into an unreviewed decision."
        ),
    ),
)

STEP_KEYS: tuple[str, ...] = tuple(step.key for step in STEPS)


# --------------------------------------------------------------------------------------- #
# Panels: the audit-first output view (the result, its evidence, the findings, what is next)
# --------------------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Row:
    """One labelled fact in a panel. ``tone`` drives the colour, never the meaning."""

    label: str
    value: str
    tone: str = ""


@dataclass(frozen=True, slots=True)
class Panel:
    """One block of the output view: a title, labelled facts, and an interpretation."""

    title: str
    rows: tuple[Row, ...] = ()
    note: str = ""
    tone: str = ""


@dataclass(frozen=True, slots=True)
class StepResult:
    """Everything one step produced, ready to render or to assert against."""

    key: str
    label: str
    narration: str
    panels: tuple[Panel, ...] = ()
    facts: dict[str, Any] = field(default_factory=dict)


Produced = tuple[list[Panel], dict[str, Any]]


class DemoRun:
    """A live demo, advanced one step at a time over the real services.

    The run owns a working directory holding the durable audit store and its external anchor.
    They are separate directories on purpose: an anchor that lives beside the store it witnesses
    is rewritten by whatever rewrites the store.
    """

    def __init__(self, workdir: Path | None = None) -> None:
        # What was ALREADY loaded before this run began. The offline claim is that the demo
        # imports no cloud SDK, and in a live `python scripts/demo.py` nothing else has loaded
        # one, so the delta and the absolute set are the same list. In a shared pytest process
        # they are not: any other module in the suite may legitimately have imported google for
        # its own reasons (the IAP negative matrix does), and a claim measured as an absolute
        # would then be decided by test ordering rather than by the demo. The absolute form of
        # the claim is still made, in fresh interpreters, by `scripts/portability_demo.py`, by
        # the headless walkthrough and by `tests/unit/test_demo_surface.py`.
        self._cloud_sdk_before = frozenset(loaded_cloud_sdks())
        self._tempdir: tempfile.TemporaryDirectory[str] | None = None
        if workdir is None:
            self._tempdir = tempfile.TemporaryDirectory(prefix="demo-run-")
            workdir = Path(self._tempdir.name)
        self.workdir = workdir
        self.audit_path = workdir / "store" / "audit.sqlite3"
        self.anchor_path = workdir / "anchor" / "head.json"
        # The audit store creates its own parent; the ANCHOR does not, because it is meant to
        # live on a volume somebody provisioned deliberately rather than one a library invented.
        # An operator therefore has to create that directory too; the demo does it here so the
        # first run of `make demo` in a fresh checkout does not fail on a missing path.
        self.anchor_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings = Settings(
            profile="local",
            audit_path=str(self.audit_path),
            audit_anchor_path=str(self.anchor_path),
            tenant=TENANT,
        )
        self.container = build_container(self.settings)
        self.service = build_service(self.container)
        self.contacts = {
            contact.contact_id: contact for contact in self.container.transcription.contacts()
        }
        self.results: list[StepResult] = []
        self.cases = 0
        self.escalated = 0
        self.routed = 0
        self.chain_ok = True
        self._perform(STEPS[0])

    # -------------------------------------------------------------- control

    @property
    def index(self) -> int:
        """Index of the step most recently performed."""
        return len(self.results) - 1

    @property
    def done(self) -> bool:
        return len(self.results) >= len(STEPS)

    def advance(self) -> StepResult:
        """Perform the next step, or re-return the last one when the arc is finished."""
        if self.done:
            return self.results[-1]
        return self._perform(STEPS[len(self.results)])

    def run_to_end(self) -> None:
        while not self.done:
            self.advance()

    def _perform(self, step: Step) -> StepResult:
        handler: Callable[[], Produced] = getattr(self, "_step_" + step.key)
        panels, facts = handler()
        result = StepResult(
            key=step.key,
            label=step.label,
            narration=step.narration,
            panels=tuple(panels),
            facts=facts,
        )
        self.results.append(result)
        return result

    # -------------------------------------------------------------- steps

    def _step_opened(self) -> Produced:
        bindings = [
            Row(port, self.settings.adapters[port][self.settings.profile].split(":")[-1])
            for port in sorted(self.settings.adapters)
        ]
        profiles = sorted({name for table in self.settings.adapters.values() for name in table})
        sdk = [name for name in loaded_cloud_sdks() if name not in self._cloud_sdk_before]
        deployment = Panel(
            title="Deployment",
            rows=(
                Row("Service", SERVICE_NAME),
                Row("Catalog id", CATALOG_ID),
                Row("Profile", self.settings.profile, "ok"),
                Row("Profiles bound for every port", ", ".join(profiles)),
                Row("Residency region", self.settings.region),
                Row("Jurisdiction PII packs", ", ".join(JURISDICTIONS)),
            ),
            note=(
                "One environment variable selects the adapter family for every port. Nothing "
                "below was edited to make the service run offline."
            ),
        )
        adapters = Panel(
            title="Bound adapters",
            rows=tuple(bindings),
            note="The binding map lives in config/settings.yaml, not in the code.",
        )
        findings = Panel(
            title="Findings",
            rows=(
                Row("Cloud SDK modules imported", ", ".join(sdk) or "none", "bad" if sdk else "ok"),
                Row("Credentials required", "none", "ok"),
                Row("Network required", "none", "ok"),
            ),
            note=(
                "The managed adapters import their SDK lazily, so this profile runs with none "
                "installed at all."
            ),
            tone="bad" if sdk else "ok",
        )
        facts = {"profile": self.settings.profile, "sdk_modules": sdk, "profiles": profiles}
        return [deployment, adapters, findings], facts

    def _step_routine(self) -> Produced:
        return self._score_panels(COMPLIANT_CONTACT, expect_routing=False)

    def _step_escalation(self) -> Produced:
        return self._score_panels(BREACH_CONTACT, expect_routing=True)

    def _step_redaction(self) -> Produced:
        contact = self.contacts[PII_CONTACT]
        raw = self.container.transcription.fetch(
            contact.contact_id, locale=contact.locale, audio_uri=contact.audio_uri
        )
        redacted = redact_for_scoring(raw)
        panels, facts = self._score_panels(PII_CONTACT, expect_routing=True)
        recorded = str(self.container.audit.log.read_all()[-1]["redacted_summary"])
        masked_text = " ".join(turn.text for turn in redacted.transcript.turns)
        leaked = PLANTED_NRIC in recorded or PLANTED_NRIC in masked_text
        panels.append(
            Panel(
                title="Redact before anything else",
                rows=(
                    Row("Identifier in the recogniser output", PLANTED_NRIC, "warn"),
                    Row("Spans masked at ingestion", str(redacted.count), "ok"),
                    Row(
                        "Identifier in the scored transcript",
                        "PRESENT" if PLANTED_NRIC in masked_text else "absent",
                        "bad" if PLANTED_NRIC in masked_text else "ok",
                    ),
                    Row(
                        "Identifier in the immutable record",
                        "PRESENT" if PLANTED_NRIC in recorded else "absent",
                        "bad" if PLANTED_NRIC in recorded else "ok",
                    ),
                    Row("Stored summary", recorded),
                ),
                note=(
                    "Masking happens on the way in, so the engine matched masked text and no "
                    "model was ever handed an identifier. The record is immutable, so a "
                    "redaction pass after the write would be too late."
                ),
                tone="bad" if leaked else "ok",
            )
        )
        facts["planted_identifier_leaked"] = leaked
        facts["redaction_spans"] = redacted.count
        return panels, facts

    def _step_review_queue(self) -> Produced:
        pending = list(self.container.review_router.outbox.pending())
        rows: list[Row] = []
        leaked = False
        for item in pending:
            payload = to_jsonable(item)
            leaked = leaked or PLANTED_NRIC in json.dumps(payload, sort_keys=True)
            rows.append(Row(str(getattr(item, "source_key", "review")), _summarise(payload)))
        queue = Panel(
            title="Outbound review queue",
            rows=tuple(rows) or (Row("queue", "empty", "bad"),),
            note=(
                "Queued, not submitted. The reference the caller received says exactly that, so "
                "a buffered escalation is never mistaken for a reviewed one."
            ),
        )
        findings = Panel(
            title="Findings",
            rows=(
                Row("Escalated results", str(self.escalated)),
                Row(
                    "Routed to review",
                    str(self.routed),
                    "ok" if self.routed == self.escalated else "bad",
                ),
                Row(
                    "Personal data on the wire",
                    "LEAKED" if leaked else "none",
                    "bad" if leaked else "ok",
                ),
            ),
            note=(
                "Every escalation is accounted for. A flag with no routing reference is "
                "auto-execution with extra steps."
            ),
            tone="bad" if leaked or self.routed != self.escalated else "ok",
        )
        actions = Panel(
            title="Next actions",
            rows=(
                Row("Reviewer", "open the queued item and approve or reject it"),
                Row("Operator", "point HUMAN_REVIEW_URL at the console and flush the outbox"),
            ),
        )
        return [queue, findings, actions], {"pending": len(pending), "wire_leak": leaked}

    def _step_audit(self) -> Produced:
        log = self.container.audit.log
        report = self.container.audit.verify()
        self.chain_ok = report.ok
        export = self.workdir / "export" / "audit.jsonl"
        export.parent.mkdir(parents=True, exist_ok=True)
        written = log.export_jsonl(export)
        restored = HashChainedAuditLog(":memory:")
        reloaded = restored.import_jsonl(export)
        round_trip = restored.verify_chain()
        anchored = bool(self.settings.audit_anchor_path) and self.anchor_path.exists()
        trail = Panel(
            title="Audit trail",
            rows=(
                Row("Records", str(report.entries)),
                Row("Hash-chained", str(report.chained)),
                Row(
                    "Unverifiable (unchained)",
                    str(report.legacy),
                    "ok" if report.legacy == 0 else "bad",
                ),
                Row("Verdict", report.detail, "ok" if report.ok else "bad"),
                Row(
                    "External head anchor",
                    "configured" if anchored else "absent",
                    "ok" if anchored else "warn",
                ),
            ),
            note=(
                "The chain alone cannot detect a truncated tail: dropping the newest rows leaves "
                "a shorter chain that verifies perfectly. The anchor, kept on a different "
                "volume, is what closes that gap."
            ),
            tone="ok" if report.ok else "bad",
        )
        portable = Panel(
            title="Open-format round trip",
            rows=(
                Row("Exported records", str(written)),
                Row("Reloaded into a fresh store", str(reloaded)),
                Row(
                    "Chain after reload",
                    round_trip.detail,
                    "ok" if round_trip.ok else "bad",
                ),
            ),
            note=(
                "JSON Lines with the hashes included, so a consumer can re-verify the trail "
                "without this codebase. That is what makes the record portable."
            ),
            tone="ok" if round_trip.ok else "bad",
        )
        facts = {
            "chain_ok": report.ok,
            "entries": report.entries,
            "exported": written,
            "round_trip_ok": round_trip.ok,
            "anchored": anchored,
        }
        return [trail, portable], facts

    def _step_tamper(self) -> Produced:
        before = self.container.audit.verify()
        target = _rewrite_a_record(self.audit_path)
        after = self.container.audit.verify()
        self.chain_ok = after.ok
        detected = (not after.ok) and after.first_bad_seq == target
        attack = Panel(
            title="The tamper",
            rows=(
                Row("Append-only triggers", "dropped by the attacker", "warn"),
                Row("Record rewritten in place", "seq " + str(target), "warn"),
                Row("Verdict before the rewrite", before.detail, "ok"),
            ),
            note=(
                "File access beats a database trigger. A store that claims otherwise is "
                "describing a policy, not a control."
            ),
        )
        findings = Panel(
            title="Findings",
            rows=(
                Row("Chain intact", "YES" if after.ok else "no", "bad" if after.ok else "ok"),
                Row("First broken record", str(after.first_bad_seq), "ok"),
                Row("Detail", after.detail),
                Row(
                    "Named the exact rewritten record",
                    "yes" if detected else "no",
                    "ok" if detected else "bad",
                ),
            ),
            note=(
                "Tamper-EVIDENT, not tamper-proof. The guarantee is that a rewrite cannot pass "
                "unnoticed, and that the report names which record broke."
            ),
            tone="ok" if detected else "bad",
        )
        actions = Panel(
            title="Next actions",
            rows=(
                Row("Operator", "restore from the exported JSONL and re-anchor deliberately"),
                Row("Auditor", "treat every record from seq " + str(target) + " on as suspect"),
            ),
        )
        facts = {"tampered_seq": target, "detected": detected, "chain_ok": after.ok}
        return [attack, findings, actions], facts

    def _step_portability(self) -> Produced:
        onprem = build_container(Settings(profile="onprem", tenant=TENANT))
        rows: list[Row] = []
        refused: list[str] = []
        absent: list[str] = []
        for port, call in EXIT_CALLS.items():
            expected_absent = port in EXIT_ABSENT
            try:
                call(onprem)
            except NotImplementedError as exc:
                if expected_absent:
                    rows.append(Row(port, "REFUSED, but is meant to be absent", "bad"))
                else:
                    refused.append(port)
                    rows.append(Row(port, "refused: " + str(exc).split(":")[0], "ok"))
            else:
                if expected_absent:
                    absent.append(port)
                    rows.append(Row(port, "absent, by design (a diagnostic, not a control)", "ok"))
                else:
                    rows.append(Row(port, "SUCCEEDED SILENTLY", "bad"))
        exit_panel = Panel(
            title="Exit profile (onprem)",
            rows=tuple(rows),
            note=(
                "Selected by one environment variable. No domain module was edited and no "
                "import changed."
            ),
            tone="ok" if len(refused) + len(absent) == len(EXIT_CALLS) else "bad",
        )
        bounds = Panel(
            title="What this does and does not prove",
            rows=(
                Row("Proved", "every port is swappable and every seam is named"),
                Row("Proved", "an unimplemented seam refuses instead of dropping work"),
                Row("NOT proved", "a running on-premises deployment exists"),
                Row("NOT proved", "model, infrastructure or whole-system portability"),
            ),
            note=(
                "Bounded claims are the point. Run scripts/portability_demo.py for the full "
                "seam tour, with a pass or fail per named check."
            ),
        )
        return [exit_panel, bounds], {
            "refused": sorted(refused),
            "absent": sorted(absent),
        }

    # -------------------------------------------------------------- helpers

    @staticmethod
    def _cite(span: Any) -> str:
        """One evidence span, rendered the way a reviewer reads a citation."""
        return f"  turn {span.turn_index} [{span.char_start}:{span.char_end}] {span.text}"

    def _score_panels(self, contact_id: str, *, expect_routing: bool) -> Produced:
        contact = self.contacts[contact_id]
        pack = pack_for_contact(self.container, contact)
        scorecard = self.service.score_contact(
            contact, pack, actor=ACTOR, tenant=TENANT, as_of=AS_OF
        )
        self.cases += 1
        if scorecard.requires_human_review:
            self.escalated += 1
        if scorecard.review_ref:
            self.routed += 1
        consistent = bool(scorecard.review_ref) == expect_routing == scorecard.requires_human_review
        verdict = Panel(
            title="Scorecard: " + scorecard.contact_id,
            rows=(
                Row("Market / pack", contact.market.value + " / " + pack.pack_id),
                Row(
                    "Disposition",
                    scorecard.disposition.value,
                    "bad" if scorecard.requires_human_review else "ok",
                ),
                Row("Severity", scorecard.severity.value),
                Row("Disclosure coverage", f"{scorecard.disclosure_score:.2f}"),
                Row("Script adherence", f"{scorecard.adherence_score:.2f}"),
                Row("Requires human review", str(scorecard.requires_human_review)),
                Row(
                    "Routed to review",
                    scorecard.review_ref or "not routed (compliant)",
                    "ok" if consistent else "bad",
                ),
                Row("Attributed to", ACTOR),
            ),
            note=(
                "Every number and the disposition come from pure stdlib code with an explicit "
                "as_of, so this replays byte for byte. A model narrates the result; it never "
                "produces it."
            ),
            tone="ok" if consistent else "bad",
        )
        findings = Panel(
            title="Findings, each citing its turn and character span",
            rows=tuple(
                Row(
                    finding.requirement_id,
                    finding.status.value
                    + ("" if not finding.evidence else self._cite(finding.evidence[0])),
                    "ok" if finding.satisfied else "bad",
                )
                for finding in scorecard.findings
            )
            or (Row("findings", "NONE", "bad"),),
            note=(
                "An unmatched or out-of-order disclosure is ABSENT, and an obligation the pack "
                "does not configure is a GAP. Neither is ever a pass."
            ),
        )
        signals = Panel(
            title="Deterministic signals",
            rows=tuple(
                Row(
                    signal.label,
                    ("detected" if signal.detected else "not detected")
                    + ("" if not signal.evidence else f"  turn {signal.evidence[0].turn_index}"),
                    "warn" if signal.detected else "",
                )
                for signal in scorecard.signals
                if signal.detected
            )
            or (Row("cues", "none matched", "ok"),),
            note=(
                "Cue lexicons live in the score pack, per market. A model may add advisory "
                "colour next to these; it may not move one."
            ),
        )
        evidence = Panel(
            title="Instruments cited",
            rows=tuple(Row(citation.source_id, citation.title) for citation in scorecard.citations)
            or (Row("citations", "NONE", "bad"),),
            note="Every finding names the instrument it comes from. An uncited finding is not one.",
        )
        facts = {
            "contact_id": scorecard.contact_id,
            "disposition": scorecard.disposition.value,
            "severity": scorecard.severity.value,
            "disclosure_score": scorecard.disclosure_score,
            "adherence_score": scorecard.adherence_score,
            "requires_human_review": scorecard.requires_human_review,
            "review_ref": scorecard.review_ref,
            "consistent": consistent,
            "cited_findings": sum(1 for f in scorecard.findings if f.evidence),
            "findings": len(scorecard.findings),
        }
        return [verdict, findings, signals, evidence], facts

    # -------------------------------------------------------------- state

    def state(self) -> dict[str, Any]:
        """The whole run as JSON-safe data: what the UI renders and the walkthrough asserts."""
        current = self.results[-1]
        return {
            "service": SERVICE_NAME,
            "catalog_id": CATALOG_ID,
            "repository": REPOSITORY,
            "profile": self.settings.profile,
            "region": self.settings.region,
            "step": current.key,
            "step_index": self.index,
            "step_count": len(STEPS),
            "label": current.label,
            "next": "" if self.done else STEPS[len(self.results)].label,
            "done": self.done,
            "totals": {
                "cases": self.cases,
                "escalated": self.escalated,
                "routed": self.routed,
                "chain_ok": self.chain_ok,
            },
            "steps": [_step_to_dict(result) for result in self.results],
        }


def _step_to_dict(result: StepResult) -> dict[str, Any]:
    return {
        "key": result.key,
        "label": result.label,
        "narration": result.narration,
        "facts": result.facts,
        "panels": [
            {
                "title": panel.title,
                "note": panel.note,
                "tone": panel.tone,
                "rows": [
                    {"label": row.label, "value": row.value, "tone": row.tone} for row in panel.rows
                ],
            }
            for panel in result.panels
        ],
    }


def _summarise(payload: Any) -> str:
    """One readable line for a queued review, without dumping the whole payload."""
    if isinstance(payload, dict):
        parts = [
            str(payload[key])
            for key in ("title", "severity", "maker", "tenant")
            if payload.get(key)
        ]
        if parts:
            return " / ".join(parts)
    return json.dumps(payload, sort_keys=True)[:120]


def _rewrite_a_record(store: Path) -> int:
    """Drop the append-only triggers and rewrite one INTERIOR record, as an attacker would.

    Returns the ``seq`` that was rewritten. An interior row is chosen deliberately: rewriting
    the newest row is the easy case, and the chain has to catch a rewrite in the middle of the
    trail too.
    """
    conn = sqlite3.connect(store)
    try:
        conn.execute("DROP TRIGGER IF EXISTS audit_log_no_update")
        conn.execute("DROP TRIGGER IF EXISTS audit_log_no_delete")
        rows = conn.execute("SELECT seq, event_json FROM audit_log ORDER BY seq ASC").fetchall()
        if len(rows) < 3:
            raise RuntimeError("the tamper step needs an interior record to rewrite")
        middle = rows[len(rows) // 2]
        payload = json.loads(middle[1])
        payload["decision"] = "allowed"
        payload["severity"] = "low"
        conn.execute(
            "UPDATE audit_log SET event_json = ? WHERE seq = ?",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")), int(middle[0])),
        )
        conn.commit()
        return int(middle[0])
    finally:
        conn.close()


def _exit_audit(container: Any) -> Any:
    return container.audit.record(
        kernel.AuditEvent(
            action="score_contact",
            actor=ACTOR,
            decision=kernel.Decision.ESCALATED,
            severity=kernel.Severity.CRITICAL,
            redacted_summary=BREACH_CONTACT + ": non_compliant on SG",
        )
    )


def _exit_scorecard() -> models.Scorecard:
    """One real scorecard, produced offline, so the exit calls carry a genuine payload.

    Built from the offline profile deliberately: the point of this tour is that the EXIT
    profile refuses the call, not that it lacks an input. A placeholder that returned
    successfully for a well-formed scorecard would be worse than one that raises.
    """
    settings = Settings(profile="local", audit_path=":memory:", scorecard_path=":memory:")
    container = build_container(settings)
    contact = next(
        record
        for record in container.transcription.contacts()
        if record.contact_id == BREACH_CONTACT
    )
    pack = pack_for_market(settings, contact.market, contact.product)
    redacted = redact_for_scoring(
        container.transcription.fetch(
            contact.contact_id, locale=contact.locale, audio_uri=contact.audio_uri
        )
    )
    return ScoringEngine().score(
        models.ScoringRequest(
            contact=contact, pack=pack, as_of=AS_OF, redaction_count=redacted.count
        ),
        redacted.transcript,
    )


def _exit_review(container: Any) -> Any:
    return container.review_router.route(_exit_scorecard(), maker=ACTOR, tenant=TENANT)


def _exit_identity(container: Any) -> Any:
    # The persona header is deliberately present. It is what the OFFLINE family answers, so
    # sending it proves the exit family refuses the call itself rather than merely lacking an
    # input: a placeholder that returned a principal for a client-written header would be worse
    # than one that raises.
    return container.identity.resolve(RequestContext(headers={"x-dev-persona": "approver"}))


def _exit_transcription(container: Any) -> Any:
    return container.transcription.fetch(BREACH_CONTACT, locale="en-SG", audio_uri="synthetic://x")


def _exit_store(container: Any) -> Any:
    return container.scorecard_store.put(_exit_scorecard())


def _exit_narration(container: Any) -> Any:
    return container.narration.narrate(narration_brief(_exit_scorecard()))


def _exit_signals(container: Any) -> Any:
    return container.signal_classifier.classify(
        SignalRequest(contact_id=BREACH_CONTACT, locale="en-SG", customer_utterances=("hello",))
    )


def _exit_warehouse(container: Any) -> Any:
    return container.warehouse.export([scorecard_to_row(_exit_scorecard())])


def _exit_tracer(container: Any) -> Any:
    with container.tracer.span("exit.tour", action="portability"):
        return None


def _exit_evaluation(container: Any) -> Any:
    return container.evaluation.gate("eval/datasets/golden_cases.jsonl")


#: The calls the exit profile must REFUSE, one per port with an exit placeholder. Add a port,
#: add a row: a seam nobody calls is a seam nobody knows is unimplemented.
#:
#: IDENTITY is the load-bearing one and it was the one missing. What the bound identity adapter
#: DECLARES is the single flag the exposure guard reads before it stands down and lets the
#: process bind every interface, so a portability tour that toured every seam except that one
#: was skipping the seam whose exit behaviour matters most.
EXIT_CALLS: dict[str, Callable[[Any], Any]] = {
    "audit": _exit_audit,
    "identity": _exit_identity,
    "narration": _exit_narration,
    "review_router": _exit_review,
    "tracer": _exit_tracer,
    "evaluation": _exit_evaluation,
    "scorecard_store": _exit_store,
    "signal_classifier": _exit_signals,
    "transcription": _exit_transcription,
    "warehouse": _exit_warehouse,
}


#: Diagnostic seams that complete as an honest no-op under the exit profile.
EXIT_ABSENT: frozenset[str] = frozenset({"tracer"})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the scripted offline demo end to end.")
    parser.add_argument(
        "output",
        nargs="?",
        default="demo.json",
        help="where to write the audit-view JSON (default: demo.json)",
    )
    parser.add_argument("--quiet", action="store_true", help="write the JSON and print nothing")
    args = parser.parse_args(argv)

    run = DemoRun()
    run.run_to_end()
    state = run.state()
    Path(args.output).write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    if not args.quiet:
        for step in state["steps"]:
            print("[" + step["key"] + "] " + step["label"])
        totals = state["totals"]
        print(
            "cases="
            + str(totals["cases"])
            + " escalated="
            + str(totals["escalated"])
            + " routed="
            + str(totals["routed"])
        )
        print("wrote " + args.output)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
