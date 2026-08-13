"""Idempotency support for the runtime executor.

When a caller supplies an ``idempotency_key`` and an :class:`IdempotencyStore`
to :func:`naot_poc.runtime.executor.execute`, the runtime caches terminal
outcomes (successes and permanent failures) so that repeat calls with the same
key short-circuit, and concurrent duplicates wait for the owner to finish and
share its result.

The store protocol is intentionally minimal so that a future distributed
implementation (Redis, Postgres) can drop in without changing ``execute()``.
The default :class:`InMemoryIdempotencyStore` coordinates concurrent callers
within a single Python process via per-key :class:`asyncio.Future` objects.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Generic, Protocol, TypeVar, runtime_checkable

from naot_poc.runtime.errors import RetryableError


TOutput = TypeVar("TOutput")


class ReserveStatus(Enum):
    """Result of attempting to claim a key for execution."""

    ACQUIRED = "acquired"
    """This caller owns execution and must store a terminal outcome."""

    IN_PROGRESS = "in_progress"
    """Another caller is already running it; call :meth:`IdempotencyStore.wait_for_completion`."""

    COMPLETED = "completed"
    """A terminal outcome is already stored; call :meth:`IdempotencyStore.get`."""


@dataclass(frozen=True)
class SuccessOutcome(Generic[TOutput]):
    """Cached successful outcome of an idempotent operation."""

    value: TOutput
    attempts: int
    duration_ms: float


@dataclass(frozen=True)
class PermanentFailureOutcome:
    """Cached permanent failure of an idempotent operation.

    ``error_type`` is retained for observability/debugging only. On replay the
    runtime always raises ``PermanentError(error_message)``; it does not attempt
    to reconstruct the original exception class.
    """

    error_type: str
    error_message: str


StoredOutcome = SuccessOutcome[TOutput] | PermanentFailureOutcome


@runtime_checkable
class IdempotencyStore(Protocol[TOutput]):
    """Persistence + coordination contract for idempotent execution."""

    async def get(self, key: str) -> StoredOutcome[TOutput] | None:
        """Return the terminal outcome stored for ``key``, or ``None`` if absent."""
        ...

    async def reserve(self, key: str) -> ReserveStatus:
        """Atomically claim ``key`` for execution.

        Returns :attr:`ReserveStatus.ACQUIRED` if this caller now owns
        execution, :attr:`ReserveStatus.IN_PROGRESS` if another caller is
        already running it, or :attr:`ReserveStatus.COMPLETED` if a terminal
        outcome is already stored.
        """
        ...

    async def wait_for_completion(self, key: str) -> StoredOutcome[TOutput]:
        """Wait for the owner of ``key`` to store a terminal outcome.

        Raises ``RetryableError`` if the owner released the claim without
        storing a terminal outcome (e.g. retryable failure or cancellation).
        """
        ...

    async def put_success(
        self,
        key: str,
        outcome: SuccessOutcome[TOutput],
    ) -> None:
        """Store a successful terminal outcome and wake any waiters."""
        ...

    async def put_failure(
        self,
        key: str,
        outcome: PermanentFailureOutcome,
    ) -> None:
        """Store a permanent-failure terminal outcome and wake any waiters."""
        ...

    async def release(self, key: str) -> None:
        """Release an acquired claim without a terminal outcome.

        Wakes any waiters with ``RetryableError`` so they may retry. Used on
        retryable failures and on owner cancellation.
        """
        ...


class InMemoryIdempotencyStore(Generic[TOutput]):
    """Process-local :class:`IdempotencyStore` backed by dicts and futures.

    Suitable for POC and tests. Concurrent duplicates within one Python
    process are coordinated via per-key ``asyncio.Future`` objects. A future
    distributed store would need an atomic ``reserve`` (e.g. ``SETNX`` or
    ``INSERT ... ON CONFLICT``) and a lease/TTL on in-progress claims.
    """

    def __init__(self) -> None:
        self._outcomes: dict[str, StoredOutcome[TOutput]] = {}
        self._in_progress: dict[str, asyncio.Future[StoredOutcome[TOutput]]] = {}

    async def get(self, key: str) -> StoredOutcome[TOutput] | None:
        return self._outcomes.get(key)

    async def reserve(self, key: str) -> ReserveStatus:
        if key in self._outcomes:
            return ReserveStatus.COMPLETED
        if key in self._in_progress:
            return ReserveStatus.IN_PROGRESS
        loop = asyncio.get_running_loop()
        self._in_progress[key] = loop.create_future()
        return ReserveStatus.ACQUIRED

    async def wait_for_completion(self, key: str) -> StoredOutcome[TOutput]:
        future = self._in_progress.get(key)
        if future is None:
            # The owner finished (or released) between reserve() and here;
            # fall back to a terminal lookup.
            outcome = self._outcomes.get(key)
            if outcome is not None:
                return outcome
            raise RetryableError(
                "Idempotent operation did not complete"
            )
        return await future

    async def put_success(
        self,
        key: str,
        outcome: SuccessOutcome[TOutput],
    ) -> None:
        self._outcomes[key] = outcome
        future = self._in_progress.pop(key, None)
        if future is not None and not future.done():
            future.set_result(outcome)

    async def put_failure(
        self,
        key: str,
        outcome: PermanentFailureOutcome,
    ) -> None:
        self._outcomes[key] = outcome
        future = self._in_progress.pop(key, None)
        if future is not None and not future.done():
            future.set_result(outcome)

    async def release(self, key: str) -> None:
        future = self._in_progress.pop(key, None)
        if future is not None and not future.done():
            future.set_exception(
                RetryableError("Idempotent operation did not complete")
            )
