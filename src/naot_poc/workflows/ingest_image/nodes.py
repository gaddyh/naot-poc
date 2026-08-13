from naot_poc.runtime.context import RunContext
from naot_poc.runtime.executor import execute
from .state import IngestImageState


class IngestImageNodes:
    def __init__(self, scanner):
        self.scanner = scanner

    async def scan(self, state: IngestImageState):
        execution = await execute(
            operation=self.scanner.scan,
            input_=state["image_path"],
            context=RunContext(operation_name="scan_image"),
        )

        return {
            "scan_result": execution.value,
        }