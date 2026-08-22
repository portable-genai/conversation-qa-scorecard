"""Managed ScorecardStorePort: Firestore in the residency region (SDK imports stay lazy).

The tenant partition is part of the DOCUMENT, and :meth:`list_for_contact` filters on it in
the query, so a listing can never span tenants. :meth:`get` is deliberately an unfiltered fetch
by id, because the tenant comparison belongs in the domain service where every surface inherits
it and where the refusal is a 403 rather than a 404.

The ``google.cloud`` import lives inside each method, so the offline profiles import this
module with no cloud SDK installed.
"""

from __future__ import annotations

from typing import Any

from hex_service_kit.serialization import to_jsonable

from ...config import Settings
from ...domain.models import Scorecard
from ...domain.serialization import scorecard_from_jsonable

_COLLECTION = "conversation_qa_scorecards"


class FirestoreScorecardStore:
    """Persist scorecards to Firestore in the deployment's residency region."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _client(self) -> Any:  # pragma: no cover - needs live GCP
        from google.cloud import firestore

        return firestore.Client()

    def list_for_contact(
        self, tenant: str, contact_id: str
    ) -> tuple[Scorecard, ...]:  # pragma: no cover - needs live GCP
        if not tenant:
            return ()
        query = (
            self._client()
            .collection(_COLLECTION)
            .where("tenant", "==", tenant)
            .where("contact_id", "==", contact_id)
            .order_by("as_of")
        )
        return tuple(scorecard_from_jsonable(doc.to_dict()) for doc in query.stream())

    def get(self, scorecard_id: str) -> Scorecard | None:  # pragma: no cover - needs live GCP
        snapshot = self._client().collection(_COLLECTION).document(scorecard_id).get()
        if not snapshot.exists:
            return None
        return scorecard_from_jsonable(snapshot.to_dict())

    def put(self, scorecard: Scorecard) -> str:  # pragma: no cover - needs live GCP
        document = to_jsonable(scorecard)
        self._client().collection(_COLLECTION).document(scorecard.scorecard_id).set(document)
        return scorecard.scorecard_id
