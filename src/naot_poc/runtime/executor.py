import asyncio
import inspect
from dataclasses import dataclass
from time import perf_counter
from typing import Awaitable, Callable, Generic, TypeVar

from naot_poc.domain.errors import NaotPocError
from naot_poc.runtime.context import RunContext
from naot_poc.runtime.errors import (
    ExecutionError,
    PermanentError,
    RetryableError,
    TimeoutError,
)
from naot_poc.runtime.events import EventSink, NoOpEventSink, RuntimeEvent
from naot_poc.runtime.policy import ExecutionPolicy, NO_RETRY


TInput = TypeVar("TInput")
TOutput = TypeVar("TOutput")

Operation = Callable[[TInput], TOutput | Awaitable[TOutput]]


@dataclass(frozen=True)
class ExecutionResult(Generic[TOutput]):
    value: TOutput
    duration_ms: float
    attempts: int


def emit_failed(
    *,
    event_sink: EventSink,
    context: RunContext,
    error: Exception,
    attempts: int,
    duration_ms: float,
) -> None:
    event_sink.emit(
        RuntimeEvent(
            name="operation.failed",
            run_id=context.run_id,
            attributes={
                "operation_name": context.operation_name,
                "attempts": attempts,
                "duration_ms": duration_ms,
                "error_type": type(error).__name__,
            },
        )
    )


async def _run_operation(
    operation: Operation[TInput, TOutput],
    input_: TInput,
) -> TOutput:
    if inspect.iscoroutinefunction(operation):
        return await operation(input_)

    return await asyncio.to_thread(operation, input_)


async def execute(
    operation: Operation[TInput, TOutput],
    input_: TInput,
    context: RunContext,
    policy: ExecutionPolicy = NO_RETRY,
    event_sink: EventSink = NoOpEventSink(),
) -> ExecutionResult[TOutput]:
    start = perf_counter()

    event_sink.emit(
        RuntimeEvent(
            name="operation.started",
            run_id=context.run_id,
            attributes={
                "operation_name": context.operation_name,
            },
        )
    )

    for attempt in range(1, policy.max_attempts + 1):
        try:
            value = await asyncio.wait_for(
                _run_operation(operation, input_),
                timeout=policy.timeout_seconds,
            )

            duration_ms = (perf_counter() - start) * 1000

            event_sink.emit(
                RuntimeEvent(
                    name="operation.succeeded",
                    run_id=context.run_id,
                    attributes={
                        "operation_name": context.operation_name,
                        "attempts": attempt,
                        "duration_ms": duration_ms,
                    },
                )
            )

            return ExecutionResult(
                value=value,
                duration_ms=duration_ms,
                attempts=attempt,
            )

        except asyncio.TimeoutError as exc:
            wrapped = TimeoutError(
                f"Operation timed out during run {context.run_id}"
            )

            if attempt >= policy.max_attempts:
                duration_ms = (perf_counter() - start) * 1000

                emit_failed(
                    event_sink=event_sink,
                    context=context,
                    error=wrapped,
                    attempts=attempt,
                    duration_ms=duration_ms,
                )
                raise wrapped from exc

            event_sink.emit(
                RuntimeEvent(
                    name="operation.retrying",
                    run_id=context.run_id,
                    attributes={
                        "operation_name": context.operation_name,
                        "attempt": attempt,
                        "next_attempt": attempt + 1,
                        "error_type": type(wrapped).__name__,
                        "retry_delay_seconds": policy.retry_delay_seconds,
                    },
                )
            )

            if policy.retry_delay_seconds > 0:
                await asyncio.sleep(policy.retry_delay_seconds)

        except RetryableError as exc:
            if attempt >= policy.max_attempts:
                duration_ms = (perf_counter() - start) * 1000

                emit_failed(
                    event_sink=event_sink,
                    context=context,
                    error=exc,
                    attempts=attempt,
                    duration_ms=duration_ms,
                )
                raise

            event_sink.emit(
                RuntimeEvent(
                    name="operation.retrying",
                    run_id=context.run_id,
                    attributes={
                        "operation_name": context.operation_name,
                        "attempt": attempt,
                        "next_attempt": attempt + 1,
                        "error_type": type(exc).__name__,
                        "retry_delay_seconds": policy.retry_delay_seconds,
                    },
                )
            )

            if policy.retry_delay_seconds > 0:
                await asyncio.sleep(policy.retry_delay_seconds)

        except NaotPocError as exc:
            duration_ms = (perf_counter() - start) * 1000

            emit_failed(
                event_sink=event_sink,
                context=context,
                error=exc,
                attempts=attempt,
                duration_ms=duration_ms,
            )
            raise

        except ExecutionError as exc:
            duration_ms = (perf_counter() - start) * 1000

            emit_failed(
                event_sink=event_sink,
                context=context,
                error=exc,
                attempts=attempt,
                duration_ms=duration_ms,
            )
            raise

        except Exception as exc:
            wrapped = PermanentError(
                f"Unexpected failure during run {context.run_id}"
            )

            duration_ms = (perf_counter() - start) * 1000

            emit_failed(
                event_sink=event_sink,
                context=context,
                error=wrapped,
                attempts=attempt,
                duration_ms=duration_ms,
            )

            raise wrapped from exc

    raise RuntimeError("unreachable")