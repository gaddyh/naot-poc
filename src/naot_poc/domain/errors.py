class NaotPocError(Exception):
    """Base exception for expected application errors."""


class InvalidInputError(NaotPocError):
    """The caller provided invalid or unusable input."""


class ScannerError(NaotPocError):
    """The barcode scanner failed to process the image."""
