"""Conservative session-scoped prompt for gameplay without a tracked run."""

from __future__ import annotations


class NoRunPromptObserver:
    def __init__(self) -> None:
        self._stream_identity = None
        self._last_frame: int | None = None
        self._valid_progress = 0
        self._suppressed = False

    def suppress_for_session(self) -> None:
        self._suppressed = True

    def observe(self, *, connected: bool, valid_snapshot: bool, party_size: int,
                active_run: bool, stream_identity=None, frame: int | None = None,
                enabled: bool = True) -> bool:
        if not enabled or active_run or self._suppressed:
            return False
        if not connected or not valid_snapshot or party_size < 1 or frame is None:
            self._valid_progress = 0
            self._last_frame = None
            return False
        if stream_identity != self._stream_identity or (
            self._last_frame is not None and frame < self._last_frame
        ):
            self._stream_identity = stream_identity
            self._last_frame = frame
            self._valid_progress = 0
            return False
        if self._last_frame is not None and frame > self._last_frame:
            self._valid_progress += 1
        self._last_frame = frame
        if self._valid_progress < 3:
            return False
        self._suppressed = True
        return True
