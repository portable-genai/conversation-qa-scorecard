"""The hexagon's boundaries, re-exported once so there is a single import site.

Every port is a ``@runtime_checkable`` Protocol and every port has a binding in every profile
(``config.DEFAULT_BINDINGS``); ``tests/contract/test_port_parity.py`` asserts both, plus set
equality in the reverse direction so a port added here without a binding fails the build.

Two ports are not redeclared here, because redeclaring a shared type is how a fleet stops
agreeing with itself:

* ``IdentityPort`` comes from ``hex-service-kit``. What an identity adapter DECLARES about the
  authentication it provides is this service's own vocabulary and lives in :mod:`.identity`.
* the speech vocabulary comes from ``speech-lexicon-kit`` and is re-exported by :mod:`.speech`,
  which also declares this repo's own ``TranscriptSourcePort``: the seam that turns a contact
  reference into a transcript, whether by calling a recogniser or by reading a stored one.
"""

from __future__ import annotations

from hex_service_kit.identity import IdentityPort

from .audit import AuditSinkPort
from .identity import (
    CLIENT_ASSERTED,
    END_USER_AUTH_ATTR,
    END_USER_AUTH_KINDS,
    UNIMPLEMENTED,
    VERIFIED,
    EndUserAuthUnavailableError,
    declared_end_user_auth,
)
from .narration import NarrationBrief, NarrationPort
from .observability import (
    EvaluationGatePort,
    ObservabilityTracerPort,
    TokenUsage,
)
from .review_router import ReviewRouterPort
from .scorecard_store import ScorecardStorePort
from .signals import SignalClassifierPort, SignalRequest
from .speech import TranscriptSourcePort
from .warehouse import WarehouseExportPort

#: port name (the key in the settings ``adapters:`` block) -> the Protocol it must satisfy.
PORT_PROTOCOLS: dict[str, type] = {
    "audit": AuditSinkPort,
    "identity": IdentityPort,
    "narration": NarrationPort,
    "review_router": ReviewRouterPort,
    "scorecard_store": ScorecardStorePort,
    "signal_classifier": SignalClassifierPort,
    "transcription": TranscriptSourcePort,
    "warehouse": WarehouseExportPort,
    "tracer": ObservabilityTracerPort,
    "evaluation": EvaluationGatePort,
}

__all__ = [
    "TokenUsage",
    "ObservabilityTracerPort",
    "EvaluationGatePort",
    "CLIENT_ASSERTED",
    "END_USER_AUTH_ATTR",
    "END_USER_AUTH_KINDS",
    "PORT_PROTOCOLS",
    "UNIMPLEMENTED",
    "VERIFIED",
    "AuditSinkPort",
    "EndUserAuthUnavailableError",
    "IdentityPort",
    "NarrationBrief",
    "NarrationPort",
    "ReviewRouterPort",
    "ScorecardStorePort",
    "SignalClassifierPort",
    "SignalRequest",
    "TranscriptSourcePort",
    "WarehouseExportPort",
    "declared_end_user_auth",
]
