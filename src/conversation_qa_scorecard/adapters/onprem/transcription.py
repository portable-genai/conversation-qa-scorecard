"""On-prem TranscriptSourcePort: fail-fast portability placeholder (the exit proof, P-12)."""

from __future__ import annotations

from ...config import Settings
from ...ports.speech import Transcript


class OnPremTranscriptSource:
    """Satisfies TranscriptSourcePort but refuses: the client wires its own recogniser."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def fetch(self, contact_id: str, *, locale: str = "", audio_uri: str = "") -> Transcript:
        raise NotImplementedError(
            "on-prem transcription is a portability placeholder: bind the client's own "
            "speech-to-text service or transcript archive (see docs/onprem-migration.md). "
            "Returning an empty transcript instead would score every disclosure as absent."
        )
