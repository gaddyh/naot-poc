# naot-poc

Barcode scanning proof of concept built on top of [zxing-cpp](https://github.com/zxing-cpp/zxing-cpp).

## Layout

```
naot-poc/
├── pyproject.toml
├── README.md
├── samples/                # place images to scan here
└── src/
    └── naot_poc/
        ├── __init__.py
        ├── __main__.py     # CLI entry point
        ├── domain/
        │   ├── __init__.py
        │   └── models.py   # ScanResult, DetectedBarcode, ...
        └── scanning/
            ├── __init__.py
            ├── scanner.py      # BarcodeScanner protocol
            └── zxing_scanner.py# ZXingBarcodeScanner implementation
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Usage

Scan a single image:

```bash
python -m naot_poc samples/multi_12_clean.jpeg
# or, after install:
naot-scan samples/multi_12_clean.jpeg
```

If no path is provided, the CLI defaults to `samples/multi_12_clean.jpeg`
relative to the current working directory and prints a helpful message when
the file is missing.
