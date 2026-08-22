"""Managed TranscriptSourcePort: batch speech-to-text, with the SDK import kept LAZY.

Calls the managed batch recogniser in the residency region and assembles the result into the
shared kernel's :class:`Transcript`. The ``google.cloud`` import lives inside the method, so
the ``local`` and ``onprem`` profiles import this module with no cloud SDK installed at all,
which is the portability proof the whole adapter layer exists to make.

Two rules this adapter follows that the offline fixture reader also follows, so nothing
downstream can tell which profile produced a transcript:

* **No model, ever, on this path.** Turn assembly and channel-to-role binding are mechanical.
  Redaction happens after this returns, in pure domain code, and only then may a model be
  called with anything.
* **A timing this adapter does not receive is a timing it does not report.** Word offsets are
  copied from the recogniser's own output and never interpolated from turn bounds. A made-up
  timestamp checked against a regulatory deadline is worse than one reported as unverifiable,
  which is what a missing timing produces downstream.

Channel-to-role binding comes from the deployment's own configuration, because which stereo
channel carries the agent is a telephony fact, not something to guess from what was said.
"""

from __future__ import annotations

from ...config import Settings
from ...ports.speech import ChannelRole, SpeakerTurn, Transcript, WordOffset

#: Channel 0 is the agent leg and channel 1 the customer leg. The default matches the usual
#: contact-centre stereo recording; a deployment whose recorder differs rebinds this adapter
#: rather than teaching the engine about channels.
_CHANNEL_ROLES: dict[int, ChannelRole] = {0: ChannelRole.AGENT, 1: ChannelRole.CUSTOMER}


class BatchTranscriptSource:
    """Fetch a transcript from managed batch speech-to-text in the residency region."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def fetch(
        self, contact_id: str, *, locale: str = "", audio_uri: str = ""
    ) -> Transcript:  # pragma: no cover - needs live GCP
        if not audio_uri:
            # Fail closed rather than returning an empty transcript: an empty transcript scores
            # every disclosure as ABSENT, which is a confident and wrong verdict about a
            # contact nobody measured.
            raise RuntimeError(
                f"contact {contact_id!r} carries no audio_uri, so the managed recogniser has "
                "nothing to transcribe. Refusing rather than scoring an empty conversation."
            )
        # Lazy import: absent in the offline profiles and in CI.
        from google.cloud import speech_v2

        client = speech_v2.SpeechClient()
        request = speech_v2.BatchRecognizeRequest(
            recognizer=(f"projects/-/locations/{self._settings.region}/recognizers/_"),
            config=speech_v2.RecognitionConfig(
                auto_decoding_config=speech_v2.AutoDetectDecodingConfig(),
                language_codes=[locale or "en-SG"],
                features=speech_v2.RecognitionFeatures(
                    enable_word_time_offsets=True,
                    enable_separate_recognition_per_channel=True,
                ),
            ),
            files=[speech_v2.BatchRecognizeFileMetadata(uri=audio_uri)],
        )
        response = client.batch_recognize(request=request).result()
        return self._assemble(contact_id, locale, response)

    def _assemble(
        self, contact_id: str, locale: str, response: object
    ) -> Transcript:  # pragma: no cover - needs live GCP
        """Mechanical turn assembly: one turn per recognised alternative, in emitted order."""
        turns: list[SpeakerTurn] = []
        for result_file in getattr(response, "results", {}).values():
            for result in getattr(result_file.transcript, "results", []):
                for alternative in getattr(result, "alternatives", []):
                    index = len(turns)
                    channel = int(getattr(result, "channel_tag", 0) or 0)
                    turns.append(
                        SpeakerTurn(
                            index=index,
                            speaker_id=f"channel-{channel}",
                            role=_CHANNEL_ROLES.get(channel, ChannelRole.UNKNOWN),
                            text=alternative.transcript,
                            channel=channel,
                            words=self._words(alternative),
                        )
                    )
        return Transcript(
            transcript_id=f"managed-{contact_id}",
            locale=locale or "en-SG",
            turns=tuple(turns),
            engine="managed-batch-stt",
        )

    @staticmethod
    def _words(alternative: object) -> tuple[WordOffset, ...]:  # pragma: no cover - needs GCP
        """Copy the recogniser's own word offsets. Never interpolate a timing it did not give."""
        offsets: list[WordOffset] = []
        cursor = 0
        text = str(getattr(alternative, "transcript", ""))
        for word in getattr(alternative, "words", []):
            token = str(getattr(word, "word", ""))
            start = text.find(token, cursor)
            if start < 0:
                continue
            cursor = start + len(token)
            offsets.append(
                WordOffset(
                    text=token,
                    char_start=start,
                    char_end=cursor,
                    start_ms=_millis(getattr(word, "start_offset", None)),
                    end_ms=_millis(getattr(word, "end_offset", None)),
                )
            )
        return tuple(offsets)


def _millis(duration: object) -> int | None:  # pragma: no cover - needs live GCP
    """Protobuf Duration to whole milliseconds, or ``None`` when the recogniser gave none."""
    if duration is None:
        return None
    total = getattr(duration, "total_seconds", None)
    return None if total is None else int(round(total() * 1000))
