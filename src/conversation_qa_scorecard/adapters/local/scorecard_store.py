"""Local ScorecardStorePort: SDK-free SQLite store of produced scorecards.

The ``local`` profile's stand-in for the managed store (Firestore in the residency region): a
``sqlite3`` table keyed by the scorecard's deterministic id, with the tenant and contact id
lifted into indexed columns and the whole record kept as plain JSON.

Plain JSON rather than a normalised schema on purpose. A compliance record has to be readable
by somebody who does not have this service, so an auditor can open the column in a text editor
and a migration off this platform is a file copy (P-12). The typed values come back through
``domain/serialization.py``, which refuses an unknown status or disposition rather than
defaulting, so a record cannot silently reload with a different verdict than it was written
with.

Tenant isolation is enforced IN THE QUERY for :meth:`list_for_contact`, which filters on
``tenant`` in SQL so a listing can never span tenants. :meth:`get` is DELIBERATELY unfiltered:
the domain service compares the record's tenant against the verified principal's and raises a
403. That split is what makes the cross-tenant denial test meaningful, because the store hands
the record over and the DOMAIN refuses to serve it.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from hex_service_kit.serialization import to_jsonable

from ...config import Settings
from ...domain.models import Scorecard
from ...domain.serialization import scorecard_from_jsonable

_DEFAULT_DB_DIR = Path.home() / ".conversation_qa_scorecard"
_DEFAULT_DB_PATH = _DEFAULT_DB_DIR / "scorecards.db"


class LocalScorecardStore:
    """Serve tenant-scoped scorecards from a local SQLite store."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        db_path = (settings.scorecard_path or "").strip() or str(_DEFAULT_DB_PATH)
        self._db_path = db_path
        # check_same_thread=False plus an RLock: the container is process-wide while the sync
        # API endpoints run in Starlette's worker threadpool.
        self._lock = threading.RLock()
        self._conn = self._connect(db_path)
        self._init_schema()

    @property
    def db_path(self) -> str:
        return self._db_path

    # ------------------------------------------------------------------ #
    # Connection / schema
    # ------------------------------------------------------------------ #
    @staticmethod
    def _connect(db_path: str) -> sqlite3.Connection:
        if db_path not in (":memory:", "") and not db_path.startswith("file:"):
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scorecards (
                    id TEXT PRIMARY KEY,
                    tenant TEXT NOT NULL,
                    contact_id TEXT NOT NULL,
                    as_of TEXT NOT NULL DEFAULT '',
                    disposition TEXT NOT NULL DEFAULT '',
                    document TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS scorecards_tenant_contact "
                "ON scorecards (tenant, contact_id)"
            )
            self._conn.commit()

    # ------------------------------------------------------------------ #
    # ScorecardStorePort
    # ------------------------------------------------------------------ #
    def list_for_contact(self, tenant: str, contact_id: str) -> tuple[Scorecard, ...]:
        """Scorecards held by ``tenant`` for ``contact_id``; the tenant filter is in the query."""
        if not tenant:
            # Fail closed: an unresolved tenant reads nothing rather than everything.
            return ()
        with self._lock:
            rows = self._conn.execute(
                "SELECT document FROM scorecards WHERE tenant = ? AND contact_id = ? "
                "ORDER BY as_of, id",
                (tenant, contact_id),
            ).fetchall()
        return tuple(scorecard_from_jsonable(json.loads(row["document"])) for row in rows)

    def get(self, scorecard_id: str) -> Scorecard | None:
        """Raw fetch by id: the DOMAIN authorizes the tenant, never this adapter."""
        with self._lock:
            row = self._conn.execute(
                "SELECT document FROM scorecards WHERE id = ?", (scorecard_id,)
            ).fetchone()
        return None if row is None else scorecard_from_jsonable(json.loads(row["document"]))

    def put(self, scorecard: Scorecard) -> str:
        """Upsert one scorecard. The id is a digest, so a re-score updates rather than piles up."""
        document = json.dumps(to_jsonable(scorecard), sort_keys=True)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO scorecards "
                "(id, tenant, contact_id, as_of, disposition, document) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    scorecard.scorecard_id,
                    scorecard.tenant,
                    scorecard.contact_id,
                    scorecard.as_of.isoformat(),
                    scorecard.disposition.value,
                    document,
                ),
            )
            self._conn.commit()
        return scorecard.scorecard_id

    # ------------------------------------------------------------------ #
    # Inspection (tests, demo)
    # ------------------------------------------------------------------ #
    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT count(*) AS n FROM scorecards").fetchone()
        return int(row["n"])
