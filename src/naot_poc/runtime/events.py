from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol


@dataclass(frozen=True)
class RuntimeEvent:
    name: str
    run_id: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    attributes: dict[str, Any] = field(default_factory=dict)


class EventSink(Protocol):
    def emit(self, event: RuntimeEvent) -> None: ...


class NoOpEventSink:
    def emit(self, event: RuntimeEvent) -> None:
        pass


NO_OP_SINK = NoOpEventSink()
