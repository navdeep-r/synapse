"""In-memory stage counters for the pipeline funnel.

`ObservabilityScreen` polls `/observability/pipeline` once per second, so reads
must never touch the database or the graph. Everything here is process-local.

The seven stage names are fixed by the UI: it renders a static funnel and
highlights the stage whose `name` equals `active_stage`, with 'Idle' as the
not-running sentinel. Renaming a stage here silently breaks the highlight.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

STAGE_NAMES: tuple[str, ...] = (
    "Ingestion",
    "Chunking",
    "Extraction",
    "Resolution",
    "Consolidation",
    "Community Detection",
    "Export",
)

IDLE = "Idle"


@dataclass
class _Stage:
    processed: int = 0
    errors: int = 0


@dataclass
class StageTracker:
    """Thread-safe counters for the seven pipeline stages."""

    _stages: dict[str, _Stage] = field(
        default_factory=lambda: {name: _Stage() for name in STAGE_NAMES}
    )
    _active: str = IDLE
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def active_stage(self) -> str:
        return self._active

    def begin(self, stage: str) -> None:
        with self._lock:
            self._active = stage

    def idle(self) -> None:
        with self._lock:
            self._active = IDLE

    def record(self, stage: str, processed: int = 1, errors: int = 0) -> None:
        with self._lock:
            entry = self._stages.setdefault(stage, _Stage())
            entry.processed += processed
            entry.errors += errors

    def reset(self) -> None:
        with self._lock:
            for entry in self._stages.values():
                entry.processed = 0
                entry.errors = 0
            self._active = IDLE

    def snapshot(self) -> dict[str, object]:
        """The exact payload `/observability/pipeline` returns.

        `processed_count` is always an int and never None: the console calls
        `.toString()` on it without a guard, so a null would blank the screen.
        `error_rate` is a pre-formatted string like '0.0%' because the console
        renders it verbatim.
        """
        with self._lock:
            stages = []
            for name in STAGE_NAMES:
                entry = self._stages.get(name, _Stage())
                total = entry.processed + entry.errors
                rate = (entry.errors / total * 100) if total else 0.0
                stages.append(
                    {
                        "name": name,
                        "processed_count": int(entry.processed),
                        "error_rate": f"{rate:.1f}%",
                    }
                )
            return {"active_stage": self._active, "stages": stages}

    def hydrate(self, counters: dict[str, int]) -> None:
        """Restore counts persisted by a previous process.

        The funnel is cumulative, so without this a restart makes an already
        populated graph look like nothing was ever processed.
        """
        with self._lock:
            for name in STAGE_NAMES:
                entry = self._stages.setdefault(name, _Stage())
                entry.processed = int(counters.get(f"stage:{name}:processed", entry.processed))
                entry.errors = int(counters.get(f"stage:{name}:errors", entry.errors))

    def as_counters(self) -> dict[str, int]:
        """Flatten to the key/value shape the counters table stores."""
        with self._lock:
            flat: dict[str, int] = {}
            for name, entry in self._stages.items():
                flat[f"stage:{name}:processed"] = entry.processed
                flat[f"stage:{name}:errors"] = entry.errors
            return flat

    def totals(self) -> dict[str, int]:
        with self._lock:
            return {name: entry.processed for name, entry in self._stages.items()}

    def error_total(self) -> int:
        with self._lock:
            return sum(entry.errors for entry in self._stages.values())
