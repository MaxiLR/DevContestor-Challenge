"""Custom exceptions for the AA flight search service."""


class BrowserFingerprintBannedException(Exception):
    """Raised when browser warmup fails due to fingerprint-based blocking or throttling."""

    pass
