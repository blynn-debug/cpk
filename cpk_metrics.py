"""Request timings and one monotonic deadline; never record credentials or cookies."""

from __future__ import annotations

import contextlib
import json
import threading
import time
import uuid
from pathlib import Path

__all__ = ["Trace", "DeadlineExceeded"]


class DeadlineExceeded(TimeoutError):
    """The end-to-end request deadline has expired."""


class Trace:
    """Collect safe stage timings, including concurrent stages, for one request."""

    def __init__(self, request_id: str | None = None, timeout: float = 60, *, clock=time.monotonic):
        self.request_id = str(uuid.UUID(request_id)) if request_id else str(uuid.uuid4())
        self.clock = clock
        self.started = clock()
        self.deadline = self.started + timeout
        self.spans: list[dict] = []
        self.marks: dict[str, float] = {}
        self.observations: list[dict] = []
        self.lock = threading.Lock()

    def remaining(self, limit: float | None = None) -> float:
        """Return the remaining time, optionally capped for an individual operation."""
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise DeadlineExceeded("request deadline exceeded")
        return min(limit, remaining) if limit is not None else remaining

    @contextlib.contextmanager
    def stage(self, name: str, *, attempt: int = 0):
        """Record duration and exception type only; exception messages can contain secrets."""
        started = self.clock()
        status = "ok"
        try:
            yield
        except Exception as exc:
            status = type(exc).__name__
            raise
        finally:
            with self.lock:
                self.spans.append(
                    {
                        "stage": name,
                        "attempt": attempt,
                        "status": status,
                        "start_ms": round((started - self.started) * 1000, 1),
                        "duration_ms": round((self.clock() - started) * 1000, 1),
                    }
                )

    def mark(self, name: str) -> None:
        """Record the first occurrence of a user-visible milestone."""
        with self.lock:
            self.marks.setdefault(name, round((self.clock() - self.started) * 1000, 1))

    def observe(self, stage: str, *, attempt: int, **fields) -> None:
        """Record caller-selected numeric/status diagnostics; never pass URLs or page contents."""
        with self.lock:
            self.observations.append({"stage": stage, "attempt": attempt, **fields})

    def snapshot(self) -> dict:
        """Return a detached, JSON-serializable timing record."""
        with self.lock:
            return {
                "request_id": self.request_id,
                "elapsed_ms": round((self.clock() - self.started) * 1000, 1),
                "stages": [dict(s) for s in self.spans],
                "milestones": dict(self.marks),
                "observations": [dict(item) for item in self.observations],
            }

    def save(self, path: Path, *, backend: str, outcome: str) -> None:
        """Append only explicit safe fields. Timing logging must not fail the search."""
        import cpk_session as cs

        record = self.snapshot()
        record.update(backend=backend, outcome=outcome, timestamp=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        try:
            cs.append_line(path, json.dumps(record, ensure_ascii=False), timeout=0.1)
        except (OSError, TimeoutError):
            pass
