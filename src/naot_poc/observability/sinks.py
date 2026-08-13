import logging

from naot_poc.runtime.events import RuntimeEvent


class InMemoryEventSink:
    """EventSink that records events in a list.

    Intended for tests and local development. Not bounded; do not wire into
    long-running production paths.
    """

    def __init__(self) -> None:
        self.events: list[RuntimeEvent] = []

    def emit(self, event: RuntimeEvent) -> None:
        self.events.append(event)

    def clear(self) -> None:
        self.events.clear()

    def filter(
        self,
        name: str | None = None,
        run_id: str | None = None,
    ) -> list[RuntimeEvent]:
        return [
            event
            for event in self.events
            if (name is None or event.name == name)
            and (run_id is None or event.run_id == run_id)
        ]


class LoggingEventSink:
    """EventSink that writes one human-readable log line per event.

    Format: ``run_id=<id> <event.name> key=value key=value``. When the event
    has no attributes, no trailing space is emitted.
    """

    def __init__(
        self,
        logger: logging.Logger | None = None,
        level: int = logging.INFO,
    ) -> None:
        self._logger = logger or logging.getLogger("naot_poc.observability")
        self._level = level

    def emit(self, event: RuntimeEvent) -> None:
        attrs = " ".join(f"{key}={value}" for key, value in event.attributes.items())
        if attrs:
            self._logger.log(
                self._level,
                "run_id=%s %s %s",
                event.run_id,
                event.name,
                attrs,
            )
        else:
            self._logger.log(
                self._level,
                "run_id=%s %s",
                event.run_id,
                event.name,
            )
