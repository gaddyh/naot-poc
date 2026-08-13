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

  evaluation targets the real workflow end-to-end:
        dataset ──▶ target ──▶ outputs ──▶ evaluator ──▶ scores
                       │                                      │
                       └──▶ ingest_image graph + runtime ──┘
```

| Layer            | Responsibility                                              |
|------------------|-------------------------------------------------------------|
| `domain/`        | Contracts & models (`ScanResult`, `BarcodeScanner`)         |
| `integrations/`  | External adapters (`ZXingBarcodeScanner`)                   |
| `workflows/`     | Orchestration (LangGraph state machines)                    |
| `runtime/`       | Execution reliability (retry, timeout, idempotency, events) |
| `observability/` | Concrete `EventSink` implementations (logging, in-memory)  |
| `evaluation/`    | Datasets, target adapters, evaluators, regression runner    |
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
        ├── runtime/
        │   ├── executor.py         # async execute() with retry, timeout & idempotency
        │   ├── context.py          # RunContext
        │   ├── policy.py           # ExecutionPolicy
        │   ├── events.py           # RuntimeEvent, EventSink protocol, NoOpEventSink
        │   ├── idempotency.py      # IdempotencyStore, InMemoryIdempotencyStore
        │   └── errors.py           # RetryableError, PermanentError, TimeoutError
        ├── observability/
        │   └── sinks.py            # LoggingEventSink, InMemoryEventSink
        └── evaluation/
            ├── cli.py              # naot-eval entry point
            ├── datasets/
            │   ├── models.py       # EvaluationCase (inputs / reference_outputs / metadata)
            │   ├── loader.py       # load_dataset()
            │   └── barcode_baseline.json
            ├── targets/
            │   └── ingest_image.py # run_ingest_image() — workflow -> {"barcodes": [...]}
            ├── evaluators/
            │   └── barcode_accuracy.py  # pure (inputs, outputs, ref) -> scores dict
            └── regression/
                ├── runner.py       # run_evaluation(), print_report()
                └── aggregate.py    # EvaluationRun, AggregateMetrics, aggregate()
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

## Evaluation

`naot-eval` runs the real `ingest_image` workflow (LangGraph + runtime + ZXing)
end-to-end over a ground-truth dataset and reports per-image + aggregate
metrics. It exists to measure whether each later layer (Gemini, recovery)
actually improves the system, against a fixed baseline.

```bash
naot-eval
```

Optionally point at a different dataset or image root:

```bash
naot-eval --dataset path/to/dataset.json --root /repo
```

### Model: dataset → target → evaluator → experiment

The evaluation layer is shaped to match LangSmith's model so a LangSmith
adapter can be added later without redesign:

```
EvaluationCase (inputs / reference_outputs / metadata)
        │
        ▼
 TARGET: run_ingest_image(inputs)        ── async, real workflow
        │
        ▼
 outputs = {"barcodes": [...]}           ── stable contract
        │
        ├──────────────────────┐
        ▼                      ▼
 barcode_accuracy          execution data
 (inputs, outputs,         latency_ms / error
  reference_outputs)
        │                      │
        ▼                      │
 scores: {                   │
   "barcode_recall",         │
   "complete",               │
   "false_positives",        │
   "matched_count",          │
   "expected_count",         │
   "found_count"             │
 }                            │
        │                      │
        └──────────┬───────────┘
                   ▼
             EvaluationRun
                   │
                   ▼
             aggregate  ──►  AggregateMetrics
```

- **`EvaluationCase`** carries generic `inputs` / `reference_outputs` /
  `metadata` dicts (LangSmith example shape). The dataset schema stays stable
  as richer ground truth is added (`box_count`, `positions`, ...).
- **`targets/ingest_image.py`** is the **only** place that knows about graph
  state (`result["scan_result"]`) and `ScanResult`. It normalizes the workflow
  into `{"barcodes": [...]}`. When Gemini / recovery land, only this adapter
  (or a sibling target) changes — evaluators and the dataset are untouched.
- **`evaluators/barcode_accuracy.py`** is a pure function
  `(inputs, outputs, reference_outputs) -> dict[str, float|int|bool]` returning
  scalar-only scores, directly reusable as a LangSmith code evaluator.
- **`EvaluationRun`** keeps per-example scores separate from execution data
  (`latency_ms`, `error`) and diagnostics (`matched`, `extra` tuples) —
  mirroring LangSmith's evaluator-feedback-vs-run-properties split.
- **`regression/aggregate.py`** computes experiment-level summary metrics.

### Metrics

Per case: `barcode_recall`, `complete`, `false_positives`, `matched_count`,
`expected_count`, `found_count`.

Aggregate:

| Metric                  | Meaning                                              |
|-------------------------|------------------------------------------------------|
| `overall_barcode_recall`| Σ matched / Σ expected                               |
| `complete_image_rate`   | fraction of images where `found == expected` (strict: all expected found **and** no extras) — the headline business metric |
| `total_false_positives` | Σ extra                                              |
| `p50_latency_ms`        | median per-case latency (nearest-rank)               |
| `p95_latency_ms`        | 95th percentile per-case latency (nearest-rank)      |

`complete` is strict: an image with every expected barcode **plus** a false
positive is **not** complete.

### Ground truth caveat

`evaluation/datasets/barcode_baseline.json` now contains manually reviewed
primary left-side barcodes. Secondary product/size barcodes are intentionally
excluded. `fuzzy_16_labels.jpeg` is retained in the dataset for provenance but
is marked `metadata.exclude_from_eval: true` because its primary digits are too
blurred to establish reliable ground truth; the loader skips it and the CLI
reports it as excluded.

Matching is by **multiset of values** (EAN13 strings); duplicate values count as
separate visible boxes, while positions are ignored.

### Reusing the evaluator as a LangSmith code evaluator

```python
from naot_poc.evaluation.evaluators import barcode_accuracy

def langsmith_barcode_evaluator(inputs, outputs, reference_outputs):
    return barcode_accuracy(
        inputs=inputs,
        outputs=outputs,
        reference_outputs=reference_outputs,
    )
```

## Tests

```bash
pytest
```
