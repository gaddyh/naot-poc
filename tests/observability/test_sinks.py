import logging

from naot_poc.observability import InMemoryEventSink, LoggingEventSink
from naot_poc.runtime.events import RuntimeEvent


def _event(
    name: str = "operation.started",
    run_id: str = "run-1",
    attributes: dict | None = None,
) -> RuntimeEvent:
    return RuntimeEvent(
        name=name,
        run_id=run_id,
        attributes=attributes or {},
    )


def test_in_memory_sink_preserves_emit_order():
    sink = InMemoryEventSink()

    sink.emit(_event("operation.started", "run-1"))
    sink.emit(_event("operation.succeeded", "run-1"))

    assert [event.name for event in sink.events] == [
        "operation.started",
        "operation.succeeded",
    ]
    assert all(event.run_id == "run-1" for event in sink.events)


def test_in_memory_sink_clear_empties_events():
    sink = InMemoryEventSink()
    sink.emit(_event())
    assert len(sink.events) == 1

    sink.clear()

    assert sink.events == []


def test_in_memory_sink_filter_by_name_returns_list():
    sink = InMemoryEventSink()
    sink.emit(_event("operation.started", "run-1"))
    sink.emit(_event("operation.succeeded", "run-1"))
    sink.emit(_event("operation.started", "run-2"))

    started = sink.filter(name="operation.started")

    assert isinstance(started, list)
    assert [event.name for event in started] == [
        "operation.started",
        "operation.started",
    ]


def test_in_memory_sink_filter_by_run_id_returns_list():
    sink = InMemoryEventSink()
    sink.emit(_event("operation.started", "run-1"))
    sink.emit(_event("operation.started", "run-2"))

    run1 = sink.filter(run_id="run-1")

    assert isinstance(run1, list)
    assert len(run1) == 1
    assert run1[0].run_id == "run-1"


def test_in_memory_sink_filter_by_name_and_run_id():
    sink = InMemoryEventSink()
    sink.emit(_event("operation.started", "run-1"))
    sink.emit(_event("operation.succeeded", "run-1"))
    sink.emit(_event("operation.started", "run-2"))

    matched = sink.filter(name="operation.started", run_id="run-1")

    assert len(matched) == 1
    assert matched[0].name == "operation.started"
    assert matched[0].run_id == "run-1"


def test_in_memory_sink_filter_with_no_constraints_returns_copy_of_all():
    sink = InMemoryEventSink()
    sink.emit(_event("operation.started", "run-1"))
    sink.emit(_event("operation.succeeded", "run-1"))

    matched = sink.filter()

    assert [event.name for event in matched] == [
        "operation.started",
        "operation.succeeded",
    ]
    # filter returns a new list, not the internal storage
    matched.clear()
    assert len(sink.events) == 2


def test_logging_sink_emits_one_record_per_event_with_attributes(caplog):
    logger = logging.getLogger("naot_poc.test.obs.attrs")
    sink = LoggingEventSink(logger=logger, level=logging.INFO)
    caplog.set_level(logging.INFO, logger="naot_poc.test.obs.attrs")

    sink.emit(
        _event(
            "operation.succeeded",
            "run-123",
            {"operation_name": "barcode_scan", "attempts": 2},
        )
    )

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelno == logging.INFO
    message = record.getMessage()
    assert "run_id=run-123" in message
    assert "operation.succeeded" in message
    assert "operation_name=barcode_scan" in message
    assert "attempts=2" in message


def test_logging_sink_emits_no_trailing_space_when_attributes_empty(caplog):
    logger = logging.getLogger("naot_poc.test.obs.empty")
    sink = LoggingEventSink(logger=logger, level=logging.INFO)
    caplog.set_level(logging.INFO, logger="naot_poc.test.obs.empty")

    sink.emit(_event("operation.started", "run-abc"))

    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert message == "run_id=run-abc operation.started"
    assert not message.endswith(" ")


def test_logging_sink_respects_custom_level(caplog):
    logger = logging.getLogger("naot_poc.test.obs.level")
    sink = LoggingEventSink(logger=logger, level=logging.WARNING)
    caplog.set_level(logging.DEBUG, logger="naot_poc.test.obs.level")

    sink.emit(_event("operation.started", "run-1"))

    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING


def test_logging_sink_uses_default_logger_when_none_given(caplog):
    sink = LoggingEventSink(level=logging.INFO)
    caplog.set_level(logging.INFO, logger="naot_poc.observability")

    sink.emit(_event("operation.started", "run-default"))

    assert len(caplog.records) == 1
    assert "run_id=run-default" in caplog.records[0].getMessage()
