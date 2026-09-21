from __future__ import annotations

from threading import Lock


class AssuranceTracker:
    """Tracks the most recent Level-of-Assurance decision per subject token.

    Mirrors OboTokenService's own last-value-by-subject cache (identity.py)
    so /v1/agent/assurance can answer "what LoA did the last tool call use"
    the same way /v1/agent/tokens answers "what was the last OBO" - a
    separate HTTP request, after the streaming query has already finished.
    """

    def __init__(self):
        self._last_by_subject: dict[str, dict] = {}
        self._lock = Lock()

    def record(self, subject_token: str, assurance: dict) -> None:
        with self._lock:
            self._last_by_subject[subject_token] = assurance

    def get_last(self, subject_token: str) -> dict | None:
        with self._lock:
            return self._last_by_subject.get(subject_token)

    def discard(self, subject_token: str) -> None:
        with self._lock:
            self._last_by_subject.pop(subject_token, None)

    def clear(self) -> None:
        with self._lock:
            self._last_by_subject.clear()
