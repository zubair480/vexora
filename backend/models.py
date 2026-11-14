"""Shared event model. Shared file: owned by the integration session (see CONTRACT.md)."""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

EVENT_TYPES = (
    "device_connected",
    "device_disconnected",
    "sensor_quality",
    "activity_changed",
    "tool_session_started",
    "tool_session_ended",
    "inspection_recorded",
    "defect_reported",
    "work_order_created",
    "exposure_updated",
    "planning_recommendation",
)
SOURCES = ("band", "replay", "synthetic", "manual", "ai", "derived")
ACTIVITIES = ("stationary", "walking", "gesture", "tool_use", "unknown")
LABELS = ("stationary", "walking", "gesture", "tool_idle", "tool_active")
NOT_PERSISTED = ("sensor_quality",)   # sent live, too chatty to store every second


@dataclass
class Event:
    type: str
    payload: dict
    source: str
    shift_id: str
    worker_id: str
    station_id: Optional[str] = None
    asset_id: Optional[str] = None
    ts: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __post_init__(self) -> None:
        if self.type not in EVENT_TYPES:
            raise ValueError(f"unknown event type {self.type!r}")
        if self.source not in SOURCES:
            raise ValueError(f"unknown event source {self.source!r}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ImuSample:
    n: Optional[int]   # band sample counter; a jump means lost samples
    t: float           # band counter, milliseconds (the SDK's `m.t`, unchanged)
    time: float        # real time of the reading, epoch seconds
    ax: float          # g
    ay: float
    az: float
    gx: float          # degrees/second
    gy: float
    gz: float


@dataclass
class ImuBatch:
    samples: list[ImuSample]
    configured_hz: float
    effective_hz: float
    source: str = "band"   # band | replay | synthetic
