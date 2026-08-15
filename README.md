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
              PrimaryOnlyScanner (wrapper)
                 filters to EAN-13-valid
                          │
                          ▼
                  BarcodeScanner
                    domain port
                          │
                          ▼
              MultiPassZXingScanner (adapter)
                          │
                          ▼
              enhanced_scanner.BarcodeScanner
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

`MultiPassZXingScanner` is a thin adapter over the multi-pass algorithm in
`enhanced_scanner.py`; it maps that algorithm's internal detections into the
domain `ScanResult`. It is configured with the symbologies present in this
project's data (Code128 + EAN13).

`PrimaryOnlyScanner` wraps any `BarcodeScanner` and filters its output to only
valid EAN-13 barcodes (13 digits + checksum). The Naot workflow cares only
about the primary GTIN-13 barcode on each shoe box; secondary Code128 model/size
barcodes are noise. zxing-cpp classifies GTIN-13 bars as Code128 regardless of
the requested format set, so the filter validates the decoded *value*, not the
`format` field. The eval CLI applies this filter by default (`--no-primary-only`
to disable).

| Layer            | Responsibility                                              |
|------------------|-------------------------------------------------------------|
| `domain/`        | Contracts & models (`ScanResult`, `BarcodeScanner`, `is_valid_ean13`) |
| `integrations/`  | External adapters (`MultiPassZXingScanner`, `PrimaryOnlyScanner`) |
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
        │   ├── barcode.py          # is_valid_ean13() — EAN-13 checksum validation
        │   ├── errors.py           # InvalidInputError, ScannerError, ...
        │   └── ports.py            # BarcodeScanner protocol
        ├── integrations/
        │   ├── primary_only.py     # PrimaryOnlyScanner wrapper (EAN-13 filter)
        │   ├── gemini/
        │   │   ├── auditor.py      # GeminiSpatialAuditor (cached, lazy Gemini adapter)
        │   │   ├── vision.py       # Gemini spatial label audit (pydantic schemas)
        │   │   └── geometry.py     # normalized→pixel bbox conversion
        │   └── zxing/
        │       ├── multipass.py        # MultiPassZXingScanner adapter -> ScanResult
        │       └── enhanced_scanner.py # multi-pass algorithm (cv2/numpy, perspective)
        ├── workflows/
        │   └── ingest_image/
        │       ├── graph.py        # LangGraph state machine (scan + audited variants)
        │       ├── nodes.py        # scan, audit, reconcile, recovery, merge nodes
        │       ├── reconciliation.py  # spatial matching of detections to Gemini labels
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
            ├── cli.py              # naot-eval entry point (--target scan|audited)
            ├── datasets/
            │   ├── models.py       # EvaluationCase (inputs / reference_outputs / metadata)
            │   ├── loader.py       # load_dataset()
            │   └── barcode_baseline.json
            ├── targets/
            │   └── ingest_image.py # run_ingest_image() / run_audited_ingest_image()
            ├── evaluators/
            │   └── barcode_accuracy.py  # pure (inputs, outputs, ref) -> scores dict
            ├── regression/
            │   ├── runner.py       # run_evaluation(), print_report()
            │   └── aggregate.py    # EvaluationRun, AggregateMetrics, aggregate()
            └── recovery/
                ├── extract_crops.py  # extract failed crops for micro-benchmarking
                └── benchmark.py      # isolated crop-level transform testing
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
# Scan-only (multipass ZXing, no Gemini)
naot-eval

# Audited (multipass ZXing + Gemini spatial audit + targeted recovery)
naot-eval --target audited

# Force re-fetch from Gemini (ignore cached audit responses)
naot-eval --target audited --refresh-cache
```

By default the scanner is wrapped with `PrimaryOnlyScanner`, which filters
results to only valid EAN-13 barcodes (the primary GTIN-13 on each shoe box).
Use `--no-primary-only` to see raw scanner output including secondary Code128
model/size barcodes.

The `scan` target runs the multipass scanner alone. The `audited` target runs
the multipass scanner and Gemini's spatial label audit concurrently, reconciles
their original-image bounding boxes, then sends only unmatched eligible label
regions through targeted deterministic recovery. Gemini locates labels and
reports status/confidence; it never supplies barcode digits.

Gemini audit responses are cached per-image in `evaluation/.gemini_cache/` so
repeated audited runs produce identical results without calling the API. Use
`--refresh-cache` to ignore the cache and re-fetch (useful when the prompt or
model changes). The cache is gitignored.

```bash
pip install -e ".[dev,gemini]"
export GEMINI_API_KEY="..."
naot-eval --target audited
```

Audited runs print per-region recovery diagnostics after each image, including
label index, attempt count, whether a primary barcode was recovered, successful
transform names, and region latency. Any evaluation false positives are also
printed as `fp_values` so targeted-recovery regressions can be investigated.

Optionally point at a different dataset or image root:

```bash
naot-eval --dataset path/to/dataset.json --root /repo
```

Both targets use the same domain output and evaluator, so results are directly
comparable.

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
| `audit_failure_rate`    | Gemini audit failures / audited images               |
| `targeted_recovery_success_rate` | recovered missing regions / attempted regions |

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

### Scan vs. audited target (primary-only)

The Naot workflow cares only about the primary EAN-13/GTIN-13 barcode on each
shoe box. The scanner is wrapped with `PrimaryOnlyScanner`, which filters
results to only valid EAN-13 barcodes (13 digits + checksum). This removes
secondary Code128 model/size barcodes (e.g. `900439-42`) that are real barcodes
but noise for this workflow.

Run over `barcode_image_ground_truth_v1` (9 evaluated cases, 74 expected
barcodes). The `scan` target runs the multipass scanner alone; the `audited`
target adds Gemini spatial audit + targeted recovery.

| Metric                          | `scan`   | `audited`   |
|---------------------------------|---------:|------------:|
| `overall_barcode_recall`        | 55.41%   | **74.32%**  |
| `complete_image_rate`           | 33.33%   | **44.44%**  |
| `total_false_positives`         | 1        | 3           |
| matched / expected              | 41 / 74  | **55 / 74** |
| `targeted_recovery_success_rate`| —        | 50.00%      |
| `p50_latency_ms`                | 1008.4   | 3929.4      |

Per-image results (matched / expected, false positives):

| image                                  | exp | scan      | audited       |
|----------------------------------------|----:|----------:|--------------:|
| WhatsApp Image …17.06.21 (2).jpeg      |   1 |   1/1 0fp |    1/1 0fp    |
| marny_brown_42.jpeg                    |   1 |   1/1 0fp |    1/1 0fp    |
| multi_12_clean.jpeg                    |  12 |  10/12 1fp|   11/12 1fp   |
| multi_clear_6_boxes.jpeg               |   6 |  **6/6 ✓**|   **6/6 ✓**   |
| stacked_6_labels.jpeg                  |   6 |   5/6 0fp |   **6/6 ✓**   |
| topdown_12_labels_a.jpeg               |  12 |   8/12    |   11/12 1fp   |
| topdown_12_labels_b.jpeg               |  12 |   7/12    |   11/12 1fp   |
| vegan_12_labels_a.jpeg                 |  12 |   1/12    |    5/12       |
| vegan_12_labels_b.jpeg                 |  12 |   2/12    |    3/12       |

The audited path adds +14 matched barcodes (41→55) via targeted recovery.
Perspective correction + CLAHE is the top-performing recovery transform.

The two `vegan_12_labels` images remain low (5/12, 3/12). The `vegan_12_labels_b`
crops are below Nyquist (~90px wide for 95 EAN-13 modules = 1px/module) — no
deterministic decoder can recover them at this capture resolution.

### Audited workflow: Gemini spatial audit + targeted recovery

The audited target (`--target audited`) runs the multipass scanner and a Gemini
spatial-label audit in parallel, reconciles the results, and applies targeted
deterministic recovery to Gemini-unmatched regions.

Workflow stages:

```
parallel_scan_audit  (scanner + Gemini audit in parallel)
        ↓
reconcile            (match scanner detections to Gemini label boxes)
        ↓
targeted_recovery    (crop + decode each unmatched region)
        ↓
merge                (dedup recovery results against initial scan)
```

Recovery uses adaptive crop padding (exact, +10%, +25%, +40%) and a 12-transform
tree per crop (8 original + perspective correction + 4x scale variants). Each
transform is named in diagnostics (e.g. `rotate_0_3x_perspective_clahe`).

#### Sampling limit on vegan_12_labels_b

The `vegan_12_labels_b` image has 12 visible labels but the barcode crops are
only ~90x50px. An EAN-13 barcode has 95 modules, so at 90px width the sampling
rate is ~1px/module -- below the Nyquist rate (>=2px/module needed). No
deterministic decoder can recover these barcodes; the information was never
captured at sufficient resolution. The `vegan_12_labels_a` image (same carton,
photographed closer) has larger crops (55-81px min dimension) and recovers 5/12.

This is a capture-quality limit, not a decoding limit. Future work to recover
these images would require either higher-resolution photography or a neural
barcode super-resolution model.

#### False-positive provenance

The audited false positives are duplicate occurrences of real barcode values
(multiset semantics), not hallucinated values:

- `multi_12_clean`: `7297501154117` appears twice in the initial multipass scan
  at positions far enough apart that the scanner's dedup doesn't merge them.
- `topdown_12_labels_a` / `topdown_12_labels_b`: recovery crops overlap
  neighboring already-detected labels, re-finding a barcode that was already
  counted. This is a spatial reconciliation issue, not a decoding error.

#### Crop-level micro-benchmark

A fast, isolated diagnostic tool for recovery development -- no Gemini, no
LangGraph, no full image:

```bash
# Extract crops from an audited run
.venv/bin/python -m naot_poc.evaluation.recovery.extract_crops \
    --output-dir evaluation/recovery

# Run transform candidates on each crop
.venv/bin/python -m naot_poc.evaluation.recovery.benchmark \
    --input evaluation/recovery/recovery_cases.json
```

The micro-benchmark reports per-crop success/failure, transform success
distribution, false positives, and neighbor bleeds. Per-transform latency is
<1ms (vs ~4s for the full audited workflow), enabling rapid iteration on
recovery parameters.

#### Why the filter validates the value, not the format field

zxing-cpp classifies the GTIN-13 bars as `Code128` regardless of the requested
format set (verified with an unrestricted scan *and* an EAN13-only scan, which
fails to decode the GTIN at all). So the `format` field cannot distinguish
primary (EAN-13/GTIN) from secondary (Code128 model/size) barcodes — zxing-cpp
labels both as `Code128`. `PrimaryOnlyScanner` validates the decoded *value*
(13 digits + EAN-13 checksum), which correctly accepts `7297501098442` and
rejects `900439-42`. `MultiPassZXingScanner` is configured with `Code128 + EAN13`
because that is the semantically correct set for this data; the value-based
filter, not the format field, carries the primary/secondary distinction.

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
