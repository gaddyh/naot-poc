import time

from naot_poc.runtime.context import RunContext
from naot_poc.runtime.executor import execute

import pytest

from naot_poc.domain.errors import InvalidInputError
from naot_poc.runtime.errors import ExecutionError, PermanentError, RetryableError
from naot_poc.runtime.policy import ExecutionPolicy

def test_execute_returns_operation_result():
    context = RunContext(operation_name="barcode_scan")

    def double(value: int) -> int:
        return value * 2

    result = execute(
        operation=double,
        input_=21,
        context=context,
    )

    assert result.value == 42


def test_execute_measures_duration():
    context = RunContext(operation_name="barcode_scan")

    def slow_operation(value: str) -> str:
        time.sleep(0.01)
        return value

    result = execute(
        operation=slow_operation,
        input_="hello",
        context=context,
    )

    assert result.value == "hello"
    assert result.duration_ms >= 10


def test_execute_preserves_application_error():
    context = RunContext(operation_name="barcode_scan")

    def operation(value: str) -> str:
        raise InvalidInputError("bad input")

    with pytest.raises(InvalidInputError):
        execute(
            operation=operation,
            input_="hello",
            context=context,
        )


def test_execute_wraps_unexpected_error():
    context = RunContext(operation_name="barcode_scan")

    def operation(value: str) -> str:
        raise KeyError("boom")

    with pytest.raises(PermanentError):
        execute(
            operation=operation,
            input_="hello",
            context=context,
        )

def test_execute_accepts_policy():
    context = RunContext(operation_name="barcode_scan")

    policy = ExecutionPolicy(
        max_attempts=3,
        timeout_seconds=2.0,
    )

    result = execute(
        operation=lambda value: value * 2,
        input_=10,
        context=context,
        policy=policy,
    )

    assert result.value == 20

def test_execute_preserves_retryable_error():
    context = RunContext(operation_name="barcode_scan")

    def operation(value: str) -> str:
        raise RetryableError("temporary failure")

    with pytest.raises(RetryableError):
        execute(
            operation=operation,
            input_="hello",
            context=context,
        )

def test_execute_retries_retryable_error():
    context = RunContext(operation_name="barcode_scan")
    calls = 0

    def operation(value: int) -> int:
        nonlocal calls
        calls += 1

        if calls < 3:
            raise RetryableError("temporary failure")

        return value * 2

    policy = ExecutionPolicy(
        max_attempts=3,
        retry_delay_seconds=0,
    )

    result = execute(
        operation=operation,
        input_=10,
        context=context,
        policy=policy,
    )

    assert result.value == 20
    assert result.attempts == 3
    assert calls == 3


def test_execute_stops_after_max_attempts():
    context = RunContext(operation_name="barcode_scan")
    calls = 0

    def operation(value: int) -> int:
        nonlocal calls
        calls += 1
        raise RetryableError("still failing")

    policy = ExecutionPolicy(
        max_attempts=3,
        retry_delay_seconds=0,
    )

    with pytest.raises(RetryableError):
        execute(
            operation=operation,
            input_=10,
            context=context,
            policy=policy,
        )

    assert calls == 3


def test_execute_does_not_retry_permanent_error():
    context = RunContext(operation_name="barcode_scan")
    calls = 0

    def operation(value: int) -> int:
        nonlocal calls
        calls += 1
        raise PermanentError("permanent failure")

    policy = ExecutionPolicy(
        max_attempts=5,
        retry_delay_seconds=0,
    )

    with pytest.raises(PermanentError):
        execute(
            operation=operation,
            input_=10,
            context=context,
            policy=policy,
        )

    assert calls == 1


from naot_poc.runtime.context import RunContext
from naot_poc.runtime.events import RuntimeEvent
from naot_poc.runtime.executor import execute


class RecordingEventSink:
    def __init__(self) -> None:
        self.events: list[RuntimeEvent] = []

    def emit(self, event: RuntimeEvent) -> None:
        self.events.append(event)


def test_execute_emits_started_and_succeeded_events():
    sink = RecordingEventSink()
    context = RunContext(run_id="run-123", operation_name="barcode_scan")

    result = execute(
        operation=lambda value: value * 2,
        input_=5,
        context=context,
        event_sink=sink,
    )

    assert result.value == 10
    assert result.attempts == 1

    assert len(sink.events) == 2

    started = sink.events[0]
    assert started.name == "operation.started"
    assert started.run_id == "run-123"
    assert started.attributes["operation_name"] == "barcode_scan"

    succeeded = sink.events[1]
    assert succeeded.name == "operation.succeeded"
    assert succeeded.run_id == "run-123"
    assert succeeded.attributes["operation_name"] == "barcode_scan"
    assert succeeded.attributes["attempts"] == 1
    assert succeeded.attributes["duration_ms"] >= 0


from naot_poc.runtime.context import RunContext
from naot_poc.runtime.errors import RetryableError
from naot_poc.runtime.events import RuntimeEvent
from naot_poc.runtime.executor import execute
from naot_poc.runtime.policy import ExecutionPolicy


class RecordingEventSink:
    def __init__(self) -> None:
        self.events: list[RuntimeEvent] = []

    def emit(self, event: RuntimeEvent) -> None:
        self.events.append(event)


def test_execute_emits_retrying_event():
    sink = RecordingEventSink()
    context = RunContext(run_id="run-123", operation_name="barcode_scan")

    attempts = 0

    def flaky_operation(value: int) -> int:
        nonlocal attempts
        attempts += 1

        if attempts == 1:
            raise RetryableError("temporary failure")

        return value * 2

    policy = ExecutionPolicy(
        max_attempts=2,
        retry_delay_seconds=0,
    )

    result = execute(
        operation=flaky_operation,
        input_=5,
        context=context,
        policy=policy,
        event_sink=sink,
    )

    assert result.value == 10
    assert result.attempts == 2

    assert [event.name for event in sink.events] == [
        "operation.started",
        "operation.retrying",
        "operation.succeeded",
    ]

    retrying = sink.events[1]

    assert retrying.attributes["operation_name"] == "barcode_scan"
    assert retrying.attributes["attempt"] == 1
    assert retrying.attributes["next_attempt"] == 2
    assert retrying.attributes["error_type"] == "RetryableError"
    assert retrying.attributes["retry_delay_seconds"] == 0


def test_execute_emits_failed_when_retries_exhausted():
    sink = RecordingEventSink()
    context = RunContext(run_id="run-123", operation_name="barcode_scan")

    def always_fails(value: int) -> int:
        raise RetryableError("temporary failure")

    policy = ExecutionPolicy(
        max_attempts=2,
        retry_delay_seconds=0,
    )

    try:
        execute(
            operation=always_fails,
            input_=5,
            context=context,
            policy=policy,
            event_sink=sink,
        )
    except RetryableError:
        pass

    assert [event.name for event in sink.events] == [
        "operation.started",
        "operation.retrying",
        "operation.failed",
    ]

    failed = sink.events[-1]

    assert failed.attributes["operation_name"] == "barcode_scan"
    assert failed.attributes["attempts"] == 2
    assert failed.attributes["error_type"] == "RetryableError"