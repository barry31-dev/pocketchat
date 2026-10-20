"""Append-only recorder for Agent Trace Doctor compatible trace events.

Two rules come from the reverse-engineered references and both are load bearing:

- ``event_id`` is allocated monotonically inside one recorder and is never
  regenerated. A recorder that mints fresh ids per export makes two exports of
  the same session compare as different sessions.
- every event carries its own timestamp. Reusing one base timestamp for a whole
  file passes a "never decreasing" check for the wrong reason.

The schema in ``schemas/trace.schema.json`` is the published Agent Trace Doctor
contract and is a byte-identical copy of it: this package does not invent a
second trace format.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

SCHEMA_VERSION = "1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


class TraceRecorder:
    """Writes one JSONL line per event, flushing each line before returning.

    The recorder is deliberately the only place that knows the on-disk shape.
    Callers pass a payload and get back the event that was written, so a caller
    can reference a real ``event_id`` instead of guessing one.
    """

    def __init__(self, path: Path, run_id: str) -> None:
        self.path = Path(path)
        self.run_id = run_id
        self.sequence = self._high_water_mark(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _high_water_mark(path: Path) -> int:
        """The largest event id already in this file, or zero.

        Appending to an existing trace resumes the numbering rather than
        restarting it. A second run in the same file that began again at
        ``evt_000001`` would produce ids the validator rejects as duplicates,
        and the file would then look corrupted rather than continued. The ids
        also stay stable: an event written once keeps the id it was given, so
        two exports of one session still compare line for line.
        """
        highest = 0
        if not path.is_file():
            return highest
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        event = json.loads(stripped)
                    except json.JSONDecodeError:
                        continue
                    event_id = event.get("event_id")
                    if isinstance(event_id, str) and event_id.startswith("evt_"):
                        digits = event_id[len("evt_"):]
                        if digits.isdigit():
                            highest = max(highest, int(digits))
        except OSError:
            return highest
        return highest

    def append(self, event_type: str, payload: Optional[Dict[str, Any]] = None,
               **fields: Any) -> Dict[str, Any]:
        self.sequence += 1
        event: Dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "event_id": "evt_%06d" % self.sequence,
            "timestamp": utc_now(),
            "run_id": self.run_id,
            "type": event_type,
        }
        if payload is not None:
            event["payload"] = payload
        event.update(fields)
        line = json.dumps(event, sort_keys=True, ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return event

    def read_events(self) -> list:
        """Return the events already in this file, oldest first.

        Used by ``export`` and ``show``, which read the trace back rather than
        keeping a second in-memory copy that could drift from what was written.
        """
        if not self.path.exists():
            return []
        events = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events
