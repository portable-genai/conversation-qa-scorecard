"""Persistence, the tenant boundary, and the warehouse row that carries no speech.

Three claims, and the middle one is the one with a real attacker behind it:

1. **The record outlives this codebase.** A scorecard round-trips through plain JSON with plain
   enum values, so an auditor can read it in a text editor and a migration off this platform is
   a file copy. The reload is strict: an unknown status refuses rather than defaulting, because
   a record that silently reloads with a different verdict is worse than one that fails to load.
2. **The tenant boundary is enforced in the DOMAIN and answers 403.** The store hands the
   record over unfiltered and the domain refuses to serve it, which is what makes the denial
   test meaningful: it is testing the rule, not testing a query. The control case below proves
   the test goes red when the check is removed, so it cannot be quietly deleted later.
3. **The analytics feed carries the shape of an answer and never an utterance.** Warehouse rows
   are joined, copied into notebooks and exported by people who never saw the retention policy,
   so the evidence stays behind the 403.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hex_service_kit.serialization import to_jsonable

from conversation_qa_scorecard.adapters.local.scorecard_store import (
    LocalScorecardStore,
)
from conversation_qa_scorecard.adapters.local.transcription import (
    FixtureTranscriptSource,
)
from conversation_qa_scorecard.adapters.local.warehouse import (
    LocalWarehouseExport,
)
from conversation_qa_scorecard.config import (
    Settings,
    build_container,
)
from conversation_qa_scorecard.domain.errors import (
    ScorecardNotFoundError,
    TenantAccessDeniedError,
)
from conversation_qa_scorecard.domain.ingestion import (
    redact_for_scoring,
)
from conversation_qa_scorecard.domain.models import (
    Scorecard,
    ScoringRequest,
)
from conversation_qa_scorecard.domain.scorecard_service import (
    ScorecardService,
)
from conversation_qa_scorecard.domain.scoring_engine import (
    ScoringEngine,
)
from conversation_qa_scorecard.domain.serialization import (
    scorecard_from_jsonable,
    scorecard_to_row,
)
from conversation_qa_scorecard.score_pack import (
    pack_for_market,
)
from conversation_qa_scorecard.service import (
    build_service,
)

from tests.fixtures import sample_cases

_AS_OF = datetime(2026, 7, 20, 4, 0, tzinfo=UTC)


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "profile": "local",
        "audit_path": ":memory:",
        "scorecard_path": ":memory:",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _scorecard(contact_id: str, settings: Settings | None = None) -> Scorecard:
    resolved = settings or _settings()
    source = FixtureTranscriptSource(resolved)
    contact = next(c for c in source.contacts() if c.contact_id == contact_id)
    redacted = redact_for_scoring(
        source.fetch(contact.contact_id, locale=contact.locale, audio_uri=contact.audio_uri)
    )
    return ScoringEngine().score(
        ScoringRequest(
            contact=contact,
            pack=pack_for_market(resolved, contact.market, contact.product),
            as_of=_AS_OF,
            redaction_count=redacted.count,
        ),
        redacted.transcript,
    )


# --------------------------------------------------------------------------------------- #
# The record
# --------------------------------------------------------------------------------------- #
def test_a_scorecard_round_trips_through_plain_json_without_loss() -> None:
    original = _scorecard(sample_cases.BREACH_CONTACT)
    restored = scorecard_from_jsonable(to_jsonable(original))
    assert restored == original


def test_the_store_reads_back_exactly_what_it_wrote() -> None:
    store = LocalScorecardStore(_settings())
    original = _scorecard(sample_cases.BREACH_CONTACT)
    assert store.put(original) == original.scorecard_id
    assert store.get(original.scorecard_id) == original


def test_a_re_score_updates_in_place_rather_than_accumulating_duplicates() -> None:
    """The id is a digest of the inputs and the outcome, so an unchanged re-run is idempotent."""
    store = LocalScorecardStore(_settings())
    store.put(_scorecard(sample_cases.BREACH_CONTACT))
    store.put(_scorecard(sample_cases.BREACH_CONTACT))
    assert store.count() == 1


def test_a_listing_filters_on_tenant_in_the_query() -> None:
    """A listing must not be able to span tenants even with the wrong contact id."""
    store = LocalScorecardStore(_settings())
    mine = _scorecard(sample_cases.BREACH_CONTACT)
    theirs = _scorecard(sample_cases.OTHER_TENANT_CONTACT)
    store.put(mine)
    store.put(theirs)
    assert len(store.list_for_contact(mine.tenant, mine.contact_id)) == 1
    assert store.list_for_contact(mine.tenant, theirs.contact_id) == ()
    assert store.list_for_contact("", mine.contact_id) == (), "an unresolved tenant reads nothing"


def test_an_unknown_scorecard_reads_as_none_rather_than_raising() -> None:
    assert LocalScorecardStore(_settings()).get("sc-nope") is None


# --------------------------------------------------------------------------------------- #
# The tenant boundary, in the domain, with its control case
# --------------------------------------------------------------------------------------- #
def test_another_tenants_scorecard_is_denied_with_403_not_404() -> None:
    container = build_container(_settings())
    service = build_service(container)
    theirs = _scorecard(sample_cases.OTHER_TENANT_CONTACT)
    container.scorecard_store.put(theirs)

    with pytest.raises(TenantAccessDeniedError) as caught:
        service.fetch(theirs.scorecard_id, tenant=sample_cases.TENANT)
    assert caught.value.http_status == 403, (
        "404 would make the store probeable with an id generator, and would tell the operator "
        "the wrong story about what went wrong"
    )


def test_a_scorecard_that_does_not_exist_is_404_so_403_means_something() -> None:
    service = build_service(build_container(_settings()))
    with pytest.raises(ScorecardNotFoundError) as caught:
        service.fetch("sc-nope", tenant=sample_cases.TENANT)
    assert caught.value.http_status == 404


def test_the_owning_tenant_reads_its_own_scorecard() -> None:
    """The positive control. Without it, a service that denied everything would pass above."""
    container = build_container(_settings())
    service = build_service(container)
    mine = _scorecard(sample_cases.BREACH_CONTACT)
    container.scorecard_store.put(mine)
    assert service.fetch(mine.scorecard_id, tenant=mine.tenant) == mine


def test_the_denial_test_goes_RED_when_the_domain_check_is_removed() -> None:
    """The mutant, so the assertion above cannot be quietly deleted with a green build.

    A service whose authorisation step does nothing is exactly what a refactor produces when
    somebody "moves the check into the adapter" and forgets half of it. This reintroduces that
    defect on a subclass and asserts the cross-tenant read succeeds, which is the failure the
    real test detects.
    """

    class _UnauthorisedService(ScorecardService):
        @staticmethod
        def _authorise(owner_tenant: str, principal_tenant: str, subject: str) -> None:
            return None

    container = build_container(_settings())
    theirs = _scorecard(sample_cases.OTHER_TENANT_CONTACT)
    container.scorecard_store.put(theirs)
    leaky = _UnauthorisedService(
        audit=container.audit,
        transcripts=container.transcription,
        store=container.scorecard_store,
        review_router=container.review_router,
        tracer=container.tracer,
    )
    assert leaky.fetch(theirs.scorecard_id, tenant=sample_cases.TENANT) == theirs, (
        "the mutant did not leak, so the real test is not testing what it claims to"
    )


def test_an_empty_principal_tenant_never_matches_an_empty_record_tenant() -> None:
    """Two unknowns are not a match; fail closed rather than pairing blanks."""
    service = build_service(build_container(_settings()))
    with pytest.raises(TenantAccessDeniedError):
        service._authorise("", "", "anything")  # noqa: SLF001


def test_scoring_another_tenants_contact_is_refused_before_the_transcript_is_fetched() -> None:
    """A caller must not be able to make this service transcribe another tenant's recording."""
    container = build_container(_settings())
    contact = next(
        c
        for c in container.transcription.contacts()
        if c.contact_id == sample_cases.OTHER_TENANT_CONTACT
    )
    with pytest.raises(TenantAccessDeniedError):
        build_service(container).score_contact(
            contact,
            pack_for_market(container.settings, contact.market, contact.product),
            actor=sample_cases.ACTOR,
            tenant=sample_cases.TENANT,
            as_of=_AS_OF,
        )


# --------------------------------------------------------------------------------------- #
# The warehouse row
# --------------------------------------------------------------------------------------- #
def test_a_warehouse_row_carries_the_shape_of_the_answer_and_no_speech() -> None:
    scorecard = _scorecard(sample_cases.PII_CONTACT)
    row = scorecard_to_row(scorecard)
    rendered = repr(to_jsonable(row))
    assert sample_cases.PLANTED_NRIC not in rendered
    for finding in scorecard.findings:
        for span in finding.evidence:
            assert span.text not in rendered, "an utterance reached the analytics table"
    assert row.disposition == scorecard.disposition.value
    assert row.disclosure_score == scorecard.disclosure_score


def test_the_offline_export_is_inspectable_so_the_claim_can_be_checked(tmp_path: object) -> None:
    """A claim about what leaves a service is worth nothing if nobody can read what left."""
    path = tmp_path / "warehouse.jsonl"  # type: ignore[operator]
    export = LocalWarehouseExport(_settings(warehouse_path=str(path)))
    assert export.export([scorecard_to_row(_scorecard(sample_cases.BREACH_CONTACT))]) == 1
    written = path.read_text(encoding="utf-8").strip().splitlines()  # type: ignore[attr-defined]
    assert len(written) == 1
    assert "turns" not in written[0] and "evidence" not in written[0]


def test_the_scoring_path_exports_exactly_one_row_per_scored_contact() -> None:
    container = build_container(_settings())
    service = build_service(container)
    contact = next(
        c for c in container.transcription.contacts() if c.contact_id == sample_cases.BREACH_CONTACT
    )
    service.score_contact(
        contact,
        pack_for_market(container.settings, contact.market, contact.product),
        actor=sample_cases.ACTOR,
        tenant=contact.tenant,
        as_of=_AS_OF,
    )
    assert len(container.warehouse.rows) == 1
