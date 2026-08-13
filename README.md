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

  runtime emits RuntimeEvent ──▶ EventSink
                                    ▲
                                    │
                          observability/sinks.py
                          LoggingEventSink | InMemoryEventSink
```

| Layer            | Responsibility                                              |
|------------------|-------------------------------------------------------------|
| `domain/`        | Contracts & models (`ScanResult`, `BarcodeScanner`)         |
| `integrations/`  | External adapters (`ZXingBarcodeScanner`)                   |
| `workflows/`     | Orchestration (LangGraph state machines)                    |
| `runtime/`       | Execution reliability (retry, timeout, idempotency, events) |
| `observability/` | Concrete `EventSink` implementations (logging, in-memory)  |
| `apps/`          | Entry points (CLI, web, API)                                |

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
            ├── executor.py         # async execute() with retry, timeout & idempotency
            ├── context.py          # RunContext
            ├── policy.py           # ExecutionPolicy
            ├── events.py           # RuntimeEvent, EventSink protocol, NoOpEventSink
            ├── idempotency.py      # IdempotencyStore, InMemoryIdempotencyStore
            └── errors.py           # RetryableError, PermanentError, TimeoutError
        └── observability/
            └── sinks.py            # LoggingEventSink, InMemoryEventSink
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

## Idempotency

`execute()` accepts two optional params — `idempotency_key` and
`idempotency_store` (both or neither) — that make a call idempotent:

```python
from naot_poc.runtime.idempotency import InMemoryIdempotencyStore

store = InMemoryIdempotencyStore()

await execute(
    operation=priority.create_order,
    input_=order,
    context=context,
    policy=EXTERNAL_WRITE,
    idempotency_key=f"create_order:{order_request_id}",
    idempotency_store=store,
)
```

When a key is supplied:

- **Cache hit (success)** — the stored result is returned without re-running
  the operation.
- **Cache hit (permanent failure)** — a `PermanentError` is re-raised with the
  original message, without re-running the operation.
- **Concurrent duplicates** — a second call with the same key while the first
  is still running waits and shares the first call's outcome (process-local
  coordination via `asyncio.Future`).
- **Retryable / timeout failures** — are **not** cached; the claim is released
  so subsequent calls can retry.
- **Owner cancellation** — releases the claim and wakes any waiters with
  `RetryableError`.

Cache scope is driven by the runtime's `ExecutionError` hierarchy
(`PermanentError` → cache, `RetryableError` / `TimeoutError` → release), not
by domain error type. `operation.started` is emitted only when actual
execution begins (on the owner path), never on cache hits or while waiting.

`IdempotencyStore` is a `Protocol` so a future distributed implementation
(Redis, Postgres with an atomic `reserve` and a lease/TTL on in-progress
claims) can drop in without changing `execute()`. The default
`InMemoryIdempotencyStore` coordinates concurrent callers within a single
Python process only.

> **Note:** Today, domain errors (`NaotPocError` subclasses) that propagate
> unwrapped from the operation fall through to the executor's generic-
> `Exception` branch and get wrapped as `PermanentError`, which means they
> end up cached as permanent failures. This is a **temporary classification
> assumption** (the only domain errors today are genuinely permanent), not
> idempotency semantics. The clean fix is to classify domain errors into
> `RetryableError` / `PermanentError` at the integration boundary before they
> reach `execute()`.

## Observability

`execute()` emits `RuntimeEvent`s throughout its lifecycle:

- `operation.started` — actual execution begins (owner path only; never on
  cache hits or while waiting)
- `operation.retrying` — a retryable/timeout failure triggered a retry
- `operation.succeeded` — the operation returned a value
- `operation.failed` — retries exhausted
- `operation.idempotent.hit` — cached success returned without re-running
- `operation.idempotent.replayed` — cached permanent failure re-raised
- `operation.idempotent.waiting` — a concurrent duplicate is waiting for the
  owner's outcome

Events go to an `EventSink` (a `Protocol` defined in `runtime/events.py`).
The default is `NO_OP_SINK` (discards everything). Concrete sinks live in
`observability/`:

- `LoggingEventSink` — one human-readable log line per event via stdlib
  `logging` (`run_id=<id> <event.name> key=value ...`).
- `InMemoryEventSink` — records events in a list; intended for tests and
  local development.

```python
from naot_poc.observability import LoggingEventSink

await execute(
    operation=scanner.scan,
    input_=image_path,
    context=context,
    event_sink=LoggingEventSink(),
)
```

Runtime emits facts; observability decides what to do with them. The
runtime layer has no dependency on `observability/` — it only knows the
`EventSink` protocol.

## Tests

```bash
pytest
```
