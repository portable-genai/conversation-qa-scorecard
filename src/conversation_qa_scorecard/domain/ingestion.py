"""Turn assembly and redaction: the deterministic half of transcript ingestion.

Everything in this module is pure stdlib plus the pinned speech kernel and the shared PII pack.
No model is involved at any point, which is the whole design: the model is downstream of
redaction, and it can only be downstream of redaction if the redaction itself is code.

The order matters and it is the one thing to remember about this file:

    fetch the transcript  ->  compute redaction spans  ->  mask  ->  match  ->  score  ->  narrate

Every step after "mask" sees masked text only. The scoring engine matches against the REDACTED
turns, so every citation a scorecard carries indexes text that has already had its identifiers
removed. That is why a reviewer can be shown the evidence span verbatim, and why the outbound
review payload and the narration brief need no second scrubbing pass to be safe.

Two behaviours worth knowing before reading a scorecard:

* **Masking shifts offsets, so word timings are dropped from masked turns.** The kernel refuses
  to keep word offsets that index text that no longer exists, and refuses to restate the spans
  against coordinates they no longer fit. A requirement with a declared timing window whose
  evidence lands in a masked turn therefore reports ``UNVERIFIABLE`` rather than a pass. That is
  the fail-closed answer: an unchecked deadline is not a met one.
* **Overlapping detections are resolved by ORDER, not by merging.** ``pii-kit`` exposes rows
  and leaves precedence to the consumer, because a bare-digit account catch-all subsumes the
  national-id shapes. The first row in ``domain/pii.py``'s order that claims a run keeps it and
  any later overlapping match is dropped, so the pack's documented precedence is what decides.
"""

from __future__ import annotations

from dataclasses import dataclass

from pii_kit import Pattern
from speech_lexicon_kit import RedactionSpan, Transcript, redact_transcript

from .pii import PII_PATTERNS

#: The mask written over a detected identifier. It carries the INFO TYPE, so a reviewer reading
#: a redacted transcript can see that a national id was removed, not merely that something was.
MASK_TEMPLATE = "[{info_type}]"


@dataclass(frozen=True, slots=True)
class RedactedTranscript:
    """A masked transcript plus the spans that produced it.

    The two travel together because the kernel deliberately does not attach the spans to the
    masked copy: a replacement of a different length shifts every later offset, so the spans
    describe the ORIGINAL. Keeping them here means the scorecard can still say how many
    identifiers were removed without anybody being tempted to index the masked text with them.
    """

    transcript: Transcript
    spans: tuple[RedactionSpan, ...]

    @property
    def count(self) -> int:
        return len(self.spans)


def _turn_spans(
    turn_index: int,
    text: str,
    patterns: tuple[Pattern, ...],
) -> list[RedactionSpan]:
    """Every identifier span in one turn, de-overlapped by position then by pattern order.

    ``order`` is the row's index in the configured pattern list, and it is carried into the
    sort key so two rows claiming the same character are resolved by the precedence the
    vertical declared in ``domain/pii.py`` rather than by which one the regex engine reached
    first. The whole point of ``pii-kit`` leaving row ORDER to the consumer is that this
    decision belongs to the vertical.
    """
    found: list[tuple[int, int, int, str]] = []
    for order, (info_type, pattern, validator) in enumerate(patterns):
        for match in pattern.finditer(text):
            value = match.group(0)
            if validator is not None and not validator(value):
                continue
            if match.end() > match.start():
                found.append((match.start(), order, match.end(), info_type))
    kept: list[RedactionSpan] = []
    last_end = -1
    for start, _order, end, info_type in sorted(found):
        if start < last_end:
            continue  # an earlier, higher-precedence row already claimed this run
        kept.append(
            RedactionSpan(
                turn_index=turn_index,
                char_start=start,
                char_end=end,
                info_type=info_type,
                replacement=MASK_TEMPLATE.format(info_type=info_type),
            )
        )
        last_end = end
    return kept


def redaction_spans(
    transcript: Transcript,
    patterns: tuple[Pattern, ...] = PII_PATTERNS,
) -> tuple[RedactionSpan, ...]:
    """Every identifier span in ``transcript``, in turn then character order. Pure."""
    spans: list[RedactionSpan] = []
    for turn in transcript.turns:
        spans.extend(_turn_spans(turn.index, turn.text, patterns))
    return tuple(spans)


def redact_for_scoring(
    transcript: Transcript,
    patterns: tuple[Pattern, ...] = PII_PATTERNS,
) -> RedactedTranscript:
    """Mask every identifier in ``transcript`` BEFORE anything else touches it.

    This is the single call every driving surface makes on the way in. Nothing downstream of
    it, including the scoring engine, the narrator, the advisory classifier, the audit write,
    the outbound review payload and the warehouse row, ever sees the original text.
    """
    spans = redaction_spans(transcript, patterns)
    return RedactedTranscript(transcript=redact_transcript(transcript, spans), spans=spans)
