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
            ┌─────────────┴─────────────┐
            ▼                           ▼
      ZXingBarcodeScanner        MultiPassZXingScanner
       (single-pass)                 (adapter)
            │                           │
            ▼                           ▼
          zxing              enhanced_scanner.BarcodeScanner
                                   (multi-pass algorithm)
                                          │
                                          ▼
                                  zxing / cv2 / numpy

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

Both scanner implementations conform to the same `BarcodeScanner` port
(`scan(path) -> ScanResult`), so the workflow and evaluation harness are
agnostic to which one runs. `MultiPassZXingScanner` is a thin adapter over
the imported multi-pass algorithm in `enhanced_scanner.py`; it maps that
algorithm's internal detections into the domain `ScanResult`. It defaults to
Code128-only (matching the imported algorithm's own default); a controlled
experiment confirmed Code128 alone is sufficient for this dataset, so keeping
it isolates the multi-pass *algorithm* as the sole variable under test.

| Layer            | Responsibility                                              |
|------------------|-------------------------------------------------------------|
| `domain/`        | Contracts & models (`ScanResult`, `BarcodeScanner`)         |
| `integrations/`  | External adapters (`ZXingBarcodeScanner`, `MultiPassZXingScanner`) |
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
        │       ├── scanner.py          # ZXingBarcodeScanner (single-pass baseline)
        │       ├── multipass.py        # MultiPassZXingScanner adapter -> ScanResult
        │       └── enhanced_scanner.py # imported multi-pass algorithm (cv2/numpy)
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

Select the scanner implementation under test (`baseline` = single-pass
`ZXingBarcodeScanner`, `multipass` = `MultiPassZXingScanner`):

```bash
naot-eval --scanner multipass
```

Optionally point at a different dataset or image root:

```bash
naot-eval --dataset path/to/dataset.json --root /repo
```

Both scanners are driven through the same target, domain output and evaluator,
so a run is a controlled before/after experiment where the scanner is the only
variable.

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

### Baseline vs. multi-pass scanner

First controlled experiment, run over `barcode_image_ground_truth_v1`
(9 evaluated cases, 74 expected barcodes). Dataset, target, domain output and
evaluator are identical across all runs; the only variable is the scanner.

Three runs are reported to separate two confounded variables — the multi-pass
*algorithm* and the barcode *format set*:

- `baseline` — `ZXingBarcodeScanner`, single-pass, reads all zxing-cpp formats.
- `multipass` (Code128-only) — `MultiPassZXingScanner` with its default
  `formats=(Code128,)`. This is the committed default.
- `multipass` (Code128+EAN13) — same scanner with `formats=(Code128, EAN13)`,
  run once to test whether enabling EAN13 decoding matters for this dataset.

| Metric                  | `baseline` | `multipass` (Code128) | `multipass` (Code128+EAN13) |
|-------------------------|-----------:|----------------------:|----------------------------:|
| `overall_barcode_recall`| 12.16%     | **55.41%**            | 55.41%                      |
| `complete_image_rate`   | 0.00%      | **11.11%** (1/9)      | 11.11% (1/9)                |
| `total_false_positives` | 2          | 5                     | 5                           |
| matched / expected      | 9 / 74     | **41 / 74**           | 41 / 74                     |
| `p50_latency_ms`        | 67.2       | 1001.0                | 1080.3                      |
| `p95_latency_ms`        | 84.7       | 1288.4                | 1297.6                      |

Per-image recall (matched / expected) — identical between the two multipass
configurations, so one column is shown:

| image                                  | exp | baseline | multipass |
|----------------------------------------|----:|---------:|----------:|
| WhatsApp Image …17.06.21 (2).jpeg      |   1 |      1/1 |       1/1 |
| marny_brown_42.jpeg                    |   1 |      1/1 |       1/1 |
| multi_12_clean.jpeg                    |  12 |      0/12|    **10/12** |
| multi_clear_6_boxes.jpeg               |   6 |      3/6 |    **6/6 ✓** |
| stacked_6_labels.jpeg                  |   6 |      2/6 |       5/6 |
| topdown_12_labels_a.jpeg               |  12 |      1/12|       8/12 |
| topdown_12_labels_b.jpeg               |  12 |      1/12|       7/12 |
| vegan_12_labels_a.jpeg                 |  12 |      0/12|       1/12 |
| vegan_12_labels_b.jpeg                 |  12 |      0/12|       2/12 |

**Read.** The entire 12% → 55% recall gain comes from the multi-pass *algorithm*
(overlapping tile grid, half-shifted tiles, multi-scale/preprocessing fallbacks,
OpenCV label-candidate detection) — `multi_12_clean` went 0→10,
`multi_clear_6_boxes` reached full recall, and the two `topdown_12_labels`
images went 1→8 and 1→7. Overall recall rose ~4.5× (12% → 55%) and the run
produced the first fully-correct image.

The format set had **zero measured effect**: the Code128-only and
Code128+EAN13 runs are identical on every per-image recall and every aggregate
metric. The 13-digit shoe-box codes in this dataset are decoded as Code128 by
zxing-cpp, so enabling EAN13 does not pick up any additional barcodes. This is
why `MultiPassZXingScanner` defaults to Code128-only — it keeps the multi-pass
algorithm as the sole variable under test and avoids implying the format config
drove the improvement. (EAN13 can be passed explicitly if a future dataset
contains genuine EAN13-only codes.)

The cost of the multi-pass strategy is latency (~15× p50, 67ms → 1001ms) and a
few more false positives (2 → 5) — the expected recall/latency tradeoff for a
multi-pass + label-fallback scanner, and exactly the kind of tradeoff the
harness was built to surface.

The two `vegan_12_labels` images remain near-zero (1/12, 2/12); the multi-pass
pipeline is not reaching those labels, so they are the next thing to diagnose
when pushing recall further.

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
