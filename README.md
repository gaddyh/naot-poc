# naot-poc

Barcode scanning proof of concept built on top of [zxing-cpp](https://github.com/zxing-cpp/zxing-cpp).

## Architecture

```
                         CLI
                          │
                          ▼
                  ingest_image graph
                          │
                          ▼
                      scan node
                          │
                          ▼
                    runtime.execute
                          │
                          ▼
                  BarcodeScanner
                    domain port
                          │
                          ▼
                ZXingBarcodeScanner
                     integration
                          │
                          ▼
                       zxing
```

| Layer         | Responsibility                                      |
|---------------|-----------------------------------------------------|
| `domain/`     | Contracts & models (`ScanResult`, `BarcodeScanner`) |
| `integrations/` | External adapters (`ZXingBarcodeScanner`)         |
| `workflows/`  | Orchestration (LangGraph state machines)            |
| `runtime/`    | Execution reliability (retry, timeout, events)      |
| `apps/`       | Entry points (CLI, web, API)                        |

## Layout

```
naot-poc/
├── pyproject.toml
├── README.md
├── samples/                # place images to scan here
└── src/
    └── naot_poc/
        ├── __main__.py             # CLI entry point
        ├── domain/
        │   ├── models.py           # ScanResult, DetectedBarcode, ...
        │   ├── errors.py           # InvalidInputError, ScannerError, ...
        │   └── ports.py            # BarcodeScanner protocol
        ├── integrations/
        │   └── zxing/
        │       └── scanner.py      # ZXingBarcodeScanner implementation
        ├── workflows/
        │   └── ingest_image/
        │       ├── graph.py        # LangGraph state machine
        │       ├── nodes.py        # scan node
        │       └── state.py        # IngestImageState
        └── runtime/
            ├── executor.py         # async execute() with retry & timeout
            ├── context.py          # RunContext
            ├── policy.py           # ExecutionPolicy
            ├── events.py           # RuntimeEvent, EventSink
            └── errors.py           # RetryableError, PermanentError, TimeoutError
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Usage

Scan a single image:

```bash
naot-scan samples/multi_clear_6_boxes.jpeg
```

If no path is provided, the CLI defaults to `samples/multi_12_clean.jpeg`
relative to the current working directory.

## Tests

```bash
pytest
```
