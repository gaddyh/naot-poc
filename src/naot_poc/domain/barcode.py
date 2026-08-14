"""Barcode value validation utilities.

The Naot workflow cares only about the primary EAN-13/GTIN-13 barcode on each
shoe box. zxing-cpp classifies these bars as Code128 regardless of the requested
format set (see README "Baseline vs. multi-pass scanner"), so the primary/
secondary distinction cannot be made by the ``format`` field — it must be made
by validating the decoded value.
"""

from __future__ import annotations


def is_valid_ean13(value: str) -> bool:
    """Return ``True`` if *value* is a valid 13-digit EAN-13 with correct checksum.

    EAN-13 checksum algorithm:
      - 13 digits total; the first 12 are data, the 13th is the check digit.
      - Sum digits at odd positions (1-indexed) × 1 + even positions × 3.
      - Check digit = (10 - (sum % 10)) % 10.
      - Valid iff the 13th digit equals the computed check digit.
    """
    if not value.isdigit() or len(value) != 13:
        return False

    digits = [int(ch) for ch in value]
    odd_sum = sum(digits[i] for i in range(0, 12, 2))  # positions 1,3,5,7,9,11
    even_sum = sum(digits[i] for i in range(1, 12, 2))  # positions 2,4,6,8,10,12
    total = odd_sum + even_sum * 3
    check_digit = (10 - (total % 10)) % 10

    return digits[12] == check_digit
