"""Local TranscriptSourcePort: the offline FIXTURE reader. SDK-free, network-free, replayable.

The ``local`` profile's stand-in for managed batch transcription. It reads obviously synthetic
transcripts from JSON files (the set shipped inside the package, or an adopter's own directory
via ``transcript_fixture_path``) and performs the same TURN ASSEMBLY the managed adapter does,
so everything downstream is identical whichever profile is bound.

No model is involved here, in either profile. Turn assembly, channel-to-role binding and word
offsets are mechanical; redaction happens next, in pure domain code; a model is not called
until both have finished.

Word timings, and why the fixture has to declare its intent
-----------------------------------------------------------
A requirement with a timing window can only be checked against per-word timings, and a
transcript with none reports ``UNVERIFIABLE`` rather than a pass. That means a fixture has to
be able to express BOTH situations, so the format is explicit about it:

* ``"word_timing": "declared"`` (the default) uses only the word offsets a turn actually
  carries. A turn with none has none, and any timed requirement over it is unverifiable. This
  is the honest default and it is what a real recogniser without word-level output produces.
* ``"word_timing": "even"`` spreads the turn's own start and end evenly across its whitespace
  tokens. It is a property the SYNTHETIC FIXTURE declares about itself, not an inference this
  adapter makes about real audio, which is why it has to be written in the file. The managed
  adapter has no equivalent and never invents a timing: a made-up timestamp checked against a
  regulatory deadline is worse than one reported as unverifiable.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from speech_lexicon_kit import ChannelRole, SpeakerTurn, Transcript, WordOffset

from ...config import Settings
from ...domain.models import ContactRecord, Market

#: The obviously synthetic transcripts shipped with the package. Every party is fictional and
#: every identifier is invented; see the module docstring in each file's ``notes`` field.
BUNDLED_FIXTURES = Path(__file__).resolve().parents[1].parent / "transcripts"

_EVEN = "even"


class TranscriptFixtureError(LookupError):
    """No fixture exists for the requested contact. Raised rather than answering emptily.

    An empty transcript scores every disclosure as absent, which is a confident and completely
    wrong verdict about a contact nobody actually measured, so the offline source refuses.
    """


class FixtureTranscriptSource:
    """Serve deterministic synthetic transcripts for the SDK-free ``local`` profile."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        configured = (settings.transcript_fixture_path or "").strip()
        self._root = Path(configured) if configured else BUNDLED_FIXTURES

    @property
    def root(self) -> Path:
        return self._root

    def contact_ids(self) -> tuple[str, ...]:
        """Every contact this source can answer for, sorted. Used by the demo and the eval."""
        return tuple(sorted(self._index()))

    def contacts(self) -> tuple[ContactRecord, ...]:
        """The synthetic contact catalogue this fixture set describes, sorted by contact id.

        A real deployment gets contacts from the contact-centre platform, which is why this is
        an EXTRA method on the offline adapter rather than part of ``TranscriptSourcePort``:
        the port answers "what was said", and no managed profile has a catalogue of synthetic
        contacts to offer. The demo, the eval and the UI's contact picker read it.
        """
        found: list[ContactRecord] = []
        for path in sorted(self._index().values()):
            document = json.loads(path.read_text(encoding="utf-8"))
            block = document.get("contact") or {}
            found.append(
                ContactRecord(
                    contact_id=str(block.get("contact_id") or document.get("contact_id") or ""),
                    tenant=str(block.get("tenant") or ""),
                    market=Market(str(block.get("market") or "SG")),
                    product=str(block.get("product") or ""),
                    declared_requirement_ids=tuple(
                        str(item) for item in block.get("declared_requirement_ids") or ()
                    ),
                    agent_id=str(block.get("agent_id") or ""),
                    channel=str(block.get("channel") or "voice"),
                    audio_uri=str(block.get("audio_uri") or ""),
                    locale=str(block.get("locale") or document.get("locale") or ""),
                )
            )
        return tuple(sorted(found, key=lambda contact: contact.contact_id))

    def headline(self, contact_id: str) -> str:
        """The one-line description of what a fixture demonstrates (demo and docs only)."""
        path = self._index().get(contact_id)
        if path is None:
            return ""
        return str(json.loads(path.read_text(encoding="utf-8")).get("headline") or "")

    def fetch(self, contact_id: str, *, locale: str = "", audio_uri: str = "") -> Transcript:
        """Return the fixture transcript for ``contact_id``.

        ``locale`` and ``audio_uri`` are accepted so the port is one shape in every profile.
        The fixture carries its own locale; a caller-supplied one is used only when the file
        declares none, because a fixture is its own ground truth.
        """
        path = self._index().get(contact_id)
        if path is None:
            known = ", ".join(sorted(self._index())) or "none"
            raise TranscriptFixtureError(
                f"no offline transcript fixture for contact {contact_id!r} "
                f"(fixtures in {self._root}: {known})"
            )
        return load_fixture(path, fallback_locale=locale)

    def _index(self) -> dict[str, Path]:
        found: dict[str, Path] = {}
        if not self._root.is_dir():
            return found
        for path in sorted(self._root.glob("*.json")):
            document = json.loads(path.read_text(encoding="utf-8"))
            contact_id = str(document.get("contact_id") or "")
            if contact_id:
                found[contact_id] = path
        return found


def _words(text: str, start_ms: int | None, end_ms: int | None) -> tuple[WordOffset, ...]:
    """Evenly spread the turn's own bounds across its whitespace tokens (fixtures only)."""
    if start_ms is None or end_ms is None or not text.strip():
        return ()
    tokens: list[tuple[str, int, int]] = []
    position = 0
    for token in text.split():
        index = text.index(token, position)
        tokens.append((token, index, index + len(token)))
        position = index + len(token)
    if not tokens:
        return ()
    span = max(end_ms - start_ms, 0)
    step = span / len(tokens)
    return tuple(
        WordOffset(
            text=token,
            char_start=char_start,
            char_end=char_end,
            start_ms=int(start_ms + round(step * order)),
            end_ms=int(start_ms + round(step * (order + 1))),
        )
        for order, (token, char_start, char_end) in enumerate(tokens)
    )


def _declared_words(raw: list[dict[str, object]], text: str) -> tuple[WordOffset, ...]:
    return tuple(
        WordOffset(
            text=text[int(str(item["char_start"])) : int(str(item["char_end"]))],
            char_start=int(str(item["char_start"])),
            char_end=int(str(item["char_end"])),
            start_ms=None if item.get("start_ms") is None else int(str(item["start_ms"])),
            end_ms=None if item.get("end_ms") is None else int(str(item["end_ms"])),
        )
        for item in raw
    )


def _time(value: object) -> datetime | None:
    return None if value in (None, "") else datetime.fromisoformat(str(value))


def load_fixture(path: Path, *, fallback_locale: str = "") -> Transcript:
    """Parse one fixture file into a :class:`Transcript`, performing turn assembly."""
    document = json.loads(path.read_text(encoding="utf-8"))
    timing = str(document.get("word_timing") or "declared")
    turns: list[SpeakerTurn] = []
    for index, raw in enumerate(document.get("turns") or []):
        text = str(raw.get("text") or "")
        start_ms = None if raw.get("start_ms") is None else int(raw["start_ms"])
        end_ms = None if raw.get("end_ms") is None else int(raw["end_ms"])
        declared = raw.get("words")
        if declared:
            words = _declared_words(list(declared), text)
        elif timing == _EVEN:
            words = _words(text, start_ms, end_ms)
        else:
            words = ()
        turns.append(
            SpeakerTurn(
                index=index,
                speaker_id=str(raw.get("speaker_id") or f"speaker-{index}"),
                role=ChannelRole(str(raw.get("role") or ChannelRole.UNKNOWN.value)),
                text=text,
                start_ms=start_ms,
                end_ms=end_ms,
                channel=None if raw.get("channel") is None else int(raw["channel"]),
                words=words,
            )
        )
    return Transcript(
        transcript_id=str(document.get("transcript_id") or path.stem),
        locale=str(document.get("locale") or fallback_locale or "en-SG"),
        turns=tuple(turns),
        started_at=_time(document.get("started_at")),
        ended_at=_time(document.get("ended_at")),
        audio_duration_ms=(
            None
            if document.get("audio_duration_ms") is None
            else int(document["audio_duration_ms"])
        ),
        engine=str(document.get("engine") or "offline-fixture"),
    )
