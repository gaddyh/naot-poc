class ExecutionError(Exception):
    """Base class for runtime execution failures."""


class RetryableError(ExecutionError):
    """A transient failure that may succeed on a later attempt."""


class PermanentError(ExecutionError):
    """A failure that should not be retried."""