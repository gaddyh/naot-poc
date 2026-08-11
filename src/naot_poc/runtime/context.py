from dataclasses import dataclass, field
from uuid import uuid4


@dataclass(frozen=True)
class RunContext:
    operation_name: str
    run_id: str = field(default_factory=lambda: str(uuid4()))