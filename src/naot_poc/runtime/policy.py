from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionPolicy:
    max_attempts: int = 1
    timeout_seconds: float | None = None
    retry_delay_seconds: float = 0.0


NO_RETRY = ExecutionPolicy(
    max_attempts=1,
)

LOCAL_COMPUTE = ExecutionPolicy(
    max_attempts=1,
    timeout_seconds=5.0,
)

EXTERNAL_READ = ExecutionPolicy(
    max_attempts=3,
    timeout_seconds=10.0,
    retry_delay_seconds=0.5,
)

EXTERNAL_WRITE = ExecutionPolicy(
    max_attempts=1,
    timeout_seconds=10.0,
)
