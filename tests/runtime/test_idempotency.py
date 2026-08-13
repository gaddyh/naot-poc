import asyncio

import pytest

from naot_poc.runtime.context import RunContext
from naot_poc.runtime.errors import PermanentError, RetryableError
from naot_poc.runtime.events import RuntimeEvent
from naot_poc.runtime.executor import execute
from naot_poc.runtime.idempotency import (
    InMemoryIdempotencyStore,
    PermanentFailureOutcome,
    ReserveStatus,
    SuccessOutcome,
)
from naot_poc.runtime.policy import ExecutionPolicy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class RecordingEventSink:
    def __init__(self) -> None:
        self.events: list[RuntimeEvent] = []

    def emit(self, event: RuntimeEvent) -> None:
        self.events.append(event)


def _ctx(operation_name: str = "op") -> RunContext:
    return RunContext(run_id="run-test", operation_name=operation_name)


# ---------------------------------------------------------------------------
# InMemoryIdempotencyStore unit tests
# ---------------------------------------------------------------------------


async def test_reserve_acquires_then_in_progress_then_completed():
    store: InMemoryIdempotencyStore[int] = InMemoryIdempotencyStore()

    assert await store.reserve("k") == ReserveStatus.ACQUIRED
    assert await store.reserve("k") == ReserveStatus.IN_PROGRESS

    await store.put_success("k", SuccessOutcome(value=1, attempts=1, duration_ms=1.0))
    assert await store.reserve("k") == ReserveStatus.COMPLETED


async def test_get_returns_none_for_unknown_key():
    store: InMemoryIdempotencyStore[int] = InMemoryIdempotencyStore()
    assert await store.get("missing") is None


async def test_put_success_resolves_waiter():
    store: InMemoryIdempotencyStore[int] = InMemoryIdempotencyStore()

    await store.reserve("k")

    async def waiter():
        return await store.wait_for_completion("k")

    wait_task = asyncio.create_task(waiter())
    await asyncio.sleep(0)  # let waiter suspend on the future

    await store.put_success("k", SuccessOutcome(value=42, attempts=1, duration_ms=1.0))

    outcome = await wait_task
    assert isinstance(outcome, SuccessOutcome)
    assert outcome.value == 42


async def test_put_failure_resolves_waiter():
    store: InMemoryIdempotencyStore[int] = InMemoryIdempotencyStore()

    await store.reserve("k")

    async def waiter():
        return await store.wait_for_completion("k")

    wait_task = asyncio.create_task(waiter())
    await asyncio.sleep(0)

    await store.put_failure(
        "k",
        PermanentFailureOutcome(error_type="PermanentError", error_message="boom"),
    )

    outcome = await wait_task
    assert isinstance(outcome, PermanentFailureOutcome)
    assert outcome.error_message == "boom"


async def test_release_wakes_waiter_with_retryable_error():
    store: InMemoryIdempotencyStore[int] = InMemoryIdempotencyStore()

    await store.reserve("k")

    async def waiter():
        return await store.wait_for_completion("k")

    wait_task = asyncio.create_task(waiter())
    await asyncio.sleep(0)

    await store.release("k")

    with pytest.raises(RetryableError):
        await wait_task


async def test_release_without_waiter_is_noop():
    store: InMemoryIdempotencyStore[int] = InMemoryIdempotencyStore()
    await store.reserve("k")
    await store.release("k")  # no waiter, should not raise
    assert await store.get("k") is None


async def test_wait_for_completion_after_release_raises_retryable():
    store: InMemoryIdempotencyStore[int] = InMemoryIdempotencyStore()
    await store.reserve("k")
    await store.release("k")

    with pytest.raises(RetryableError):
        await store.wait_for_completion("k")


# ---------------------------------------------------------------------------
# End-to-end tests through execute()
# ---------------------------------------------------------------------------


async def test_cached_success_returned_without_rerunning():
    sink = RecordingEventSink()
    store: InMemoryIdempotencyStore[int] = InMemoryIdempotencyStore()
    calls = 0

    def double(value: int) -> int:
        nonlocal calls
        calls += 1
        return value * 2

    first = await execute(
        operation=double,
        input_=21,
        context=_ctx(),
        idempotency_key="op:1",
        idempotency_store=store,
        event_sink=sink,
    )
    assert first.value == 42
    assert calls == 1

    sink.events.clear()
    second = await execute(
        operation=double,
        input_=21,
        context=_ctx(),
        idempotency_key="op:1",
        idempotency_store=store,
        event_sink=sink,
    )
    assert second.value == 42
    assert calls == 1  # operation not re-run

    assert [e.name for e in sink.events] == ["operation.idempotent.hit"]
    assert sink.events[0].attributes["idempotency_key"] == "op:1"


async def test_cached_permanent_failure_replayed_as_permanent_error():
    sink = RecordingEventSink()
    store: InMemoryIdempotencyStore[int] = InMemoryIdempotencyStore()
    calls = 0

    def fails(value: int) -> int:
        nonlocal calls
        calls += 1
        raise PermanentError("nope")

    with pytest.raises(PermanentError):
        await execute(
            operation=fails,
            input_=1,
            context=_ctx(),
            idempotency_key="op:fail",
            idempotency_store=store,
            event_sink=sink,
        )
    assert calls == 1

    sink.events.clear()
    with pytest.raises(PermanentError) as exc_info:
        await execute(
            operation=fails,
            input_=1,
            context=_ctx(),
            idempotency_key="op:fail",
            idempotency_store=store,
            event_sink=sink,
        )
    assert calls == 1  # not re-run
    assert "nope" in str(exc_info.value)

    assert [e.name for e in sink.events] == ["operation.idempotent.replayed"]


async def test_retryable_failure_not_cached_subsequent_call_reruns():
    store: InMemoryIdempotencyStore[int] = InMemoryIdempotencyStore()
    calls = 0

    def flaky(value: int) -> int:
        nonlocal calls
        calls += 1
        raise RetryableError("temporary")

    policy = ExecutionPolicy(max_attempts=2, retry_delay_seconds=0)

    with pytest.raises(RetryableError):
        await execute(
            operation=flaky,
            input_=1,
            context=_ctx(),
            policy=policy,
            idempotency_key="op:retry",
            idempotency_store=store,
        )
    assert calls == 2  # exhausted retries on first call

    # Second call should re-run because retryable failures are not cached.
    with pytest.raises(RetryableError):
        await execute(
            operation=flaky,
            input_=1,
            context=_ctx(),
            policy=policy,
            idempotency_key="op:retry",
            idempotency_store=store,
        )
    assert calls == 4


async def test_concurrent_duplicates_wait_and_share_result():
    store: InMemoryIdempotencyStore[int] = InMemoryIdempotencyStore()
    calls = 0
    started = asyncio.Event()

    async def slow_double(value: int) -> int:
        nonlocal calls
        calls += 1
        started.set()
        await asyncio.sleep(0.05)
        return value * 2

    async def call() -> int:
        result = await execute(
            operation=slow_double,
            input_=10,
            context=_ctx(),
            idempotency_key="op:concurrent",
            idempotency_store=store,
        )
        return result.value

    results = await asyncio.gather(call(), call(), call())
    assert results == [20, 20, 20]
    assert calls == 1  # operation ran exactly once
    await started.wait()


async def test_waiter_on_released_claim_gets_retryable_and_can_retry():
    store: InMemoryIdempotencyStore[int] = InMemoryIdempotencyStore()
    calls = 0

    def fail_once_then_succeed(value: int) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RetryableError("temporary")
        return value * 2

    policy = ExecutionPolicy(max_attempts=1, retry_delay_seconds=0)

    # First call: retryable failure -> claim released, not cached.
    with pytest.raises(RetryableError):
        await execute(
            operation=fail_once_then_succeed,
            input_=5,
            context=_ctx(),
            policy=policy,
            idempotency_key="op:release",
            idempotency_store=store,
        )
    assert calls == 1

    # Second call: should re-run and succeed.
    result = await execute(
        operation=fail_once_then_succeed,
        input_=5,
        context=_ctx(),
        policy=policy,
        idempotency_key="op:release",
        idempotency_store=store,
    )
    assert result.value == 10
    assert calls == 2


async def test_owner_cancellation_releases_claim_and_waiter_wakes():
    store: InMemoryIdempotencyStore[int] = InMemoryIdempotencyStore()
    started = asyncio.Event()

    async def hanging(value: int) -> int:
        started.set()
        await asyncio.sleep(10)
        return value  # unreachable

    async def owner() -> None:
        await execute(
            operation=hanging,
            input_=1,
            context=_ctx(),
            idempotency_key="op:cancel",
            idempotency_store=store,
        )

    owner_task = asyncio.create_task(owner())
    await started.wait()

    async def waiter() -> int:
        result = await execute(
            operation=hanging,
            input_=1,
            context=_ctx(),
            idempotency_key="op:cancel",
            idempotency_store=store,
        )
        return result.value

    wait_task = asyncio.create_task(waiter())
    await asyncio.sleep(0)  # let waiter suspend on the future

    owner_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner_task

    # Waiter should wake with RetryableError (released claim), not hang.
    with pytest.raises(RetryableError):
        await asyncio.wait_for(wait_task, timeout=1.0)


async def test_key_without_store_raises_value_error():
    with pytest.raises(ValueError):
        await execute(
            operation=lambda v: v,
            input_=1,
            context=_ctx(),
            idempotency_key="op:1",
        )


async def test_store_without_key_raises_value_error():
    store: InMemoryIdempotencyStore[int] = InMemoryIdempotencyStore()
    with pytest.raises(ValueError):
        await execute(
            operation=lambda v: v,
            input_=1,
            context=_ctx(),
            idempotency_store=store,
        )


async def test_cache_hit_emits_no_operation_started():
    sink = RecordingEventSink()
    store: InMemoryIdempotencyStore[int] = InMemoryIdempotencyStore()

    await execute(
        operation=lambda v: v * 2,
        input_=5,
        context=_ctx(),
        idempotency_key="op:events",
        idempotency_store=store,
        event_sink=sink,
    )

    sink.events.clear()
    await execute(
        operation=lambda v: v * 2,
        input_=5,
        context=_ctx(),
        idempotency_key="op:events",
        idempotency_store=store,
        event_sink=sink,
    )

    names = [e.name for e in sink.events]
    assert names == ["operation.idempotent.hit"]
    assert "operation.started" not in names


async def test_owner_path_emits_started_then_succeeded_with_key():
    sink = RecordingEventSink()
    store: InMemoryIdempotencyStore[int] = InMemoryIdempotencyStore()

    await execute(
        operation=lambda v: v * 2,
        input_=5,
        context=_ctx(),
        idempotency_key="op:owner",
        idempotency_store=store,
        event_sink=sink,
    )

    names = [e.name for e in sink.events]
    assert names == ["operation.started", "operation.succeeded"]
    assert sink.events[0].attributes["idempotency_key"] == "op:owner"
    assert sink.events[1].attributes["idempotency_key"] == "op:owner"


async def test_waiter_then_shared_success_emits_waiting_then_hit():
    sink = RecordingEventSink()
    store: InMemoryIdempotencyStore[int] = InMemoryIdempotencyStore()
    started = asyncio.Event()

    async def slow_double(value: int) -> int:
        started.set()
        await asyncio.sleep(0.02)
        return value * 2

    async def owner_call():
        return await execute(
            operation=slow_double,
            input_=7,
            context=_ctx(),
            idempotency_key="op:wait",
            idempotency_store=store,
            event_sink=sink,
        )

    async def waiter_call():
        # Ensure owner reserves first.
        await started.wait()
        return await execute(
            operation=slow_double,
            input_=7,
            context=_ctx(),
            idempotency_key="op:wait",
            idempotency_store=store,
            event_sink=sink,
        )

    owner_result, waiter_result = await asyncio.gather(
        owner_call(), waiter_call(),
    )
    assert owner_result.value == 14
    assert waiter_result.value == 14

    waiter_events = [
        e for e in sink.events if e.name == "operation.idempotent.waiting"
    ]
    assert len(waiter_events) == 1
    hit_events = [
        e for e in sink.events if e.name == "operation.idempotent.hit"
    ]
    assert len(hit_events) == 1
    # The waiter must not have emitted operation.started.
    started_events = [e for e in sink.events if e.name == "operation.started"]
    assert len(started_events) == 1  # only the owner


async def test_no_idempotency_zero_behavior_change():
    """Without idempotency params, execute() behaves exactly as before."""
    sink = RecordingEventSink()
    result = await execute(
        operation=lambda v: v * 2,
        input_=21,
        context=_ctx(),
        event_sink=sink,
    )
    assert result.value == 42
    assert result.attempts == 1
    assert [e.name for e in sink.events] == [
        "operation.started",
        "operation.succeeded",
    ]
    # No idempotency_key attribute on events when not used.
    assert "idempotency_key" not in sink.events[0].attributes
