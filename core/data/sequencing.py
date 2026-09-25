"""Stream sequencing (owned by core.data).

Streams that support sequence numbers must carry them; explicit absence is
UNKNOWN. Sequence numbers are never invented to fill gaps (SECTION 23).
"""
from __future__ import annotations

from core.validation.engine import SequenceObservation

CONTRACT_VERSION = "1.0.0"


class SequenceMonitor:
    """Tracks the last sequence per stream and classifies new observations.

    In-memory per pipeline instance (documented limitation); deterministic
    deduplication is persisted via the event store's dedup keys instead.
    """

    def __init__(self) -> None:
        self._last: dict[str, int] = {}

    def check(self, stream_key: str, sequence_number: int | None) -> SequenceObservation:
        if sequence_number is None:
            return SequenceObservation(
                status="UNKNOWN",
                details={"explicit_absence": True, "stream": stream_key},
            )
        if not isinstance(sequence_number, int) or isinstance(sequence_number, bool):
            return SequenceObservation(
                status="UNKNOWN",
                details={"reason": "sequence not an integer", "stream": stream_key},
            )
        last = self._last.get(stream_key)
        if last is None:
            self._last[stream_key] = sequence_number
            return SequenceObservation(
                status="OK", expected=sequence_number, actual=sequence_number, difference=0
            )
        if sequence_number == last:
            return SequenceObservation(
                status="DUPLICATE", expected=last, actual=sequence_number, difference=0
            )
        if sequence_number < last:
            observation = SequenceObservation(
                status="REGRESSION",
                expected=last,
                actual=sequence_number,
                difference=last - sequence_number,
            )
            return observation
        expected_next = last + 1
        observation = SequenceObservation(
            status="OK" if sequence_number == expected_next else "GAP",
            expected=expected_next,
            actual=sequence_number,
            difference=sequence_number - expected_next,
        )
        if sequence_number > last:
            self._last[stream_key] = sequence_number
        return observation
