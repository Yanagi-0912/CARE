class LineError(Exception):
    """Base exception for LINE messaging helpers."""


class LineValidationError(LineError):
    """Invalid LINE webhook or reply payload."""


class LineTokenError(LineError):
    """LINE channel access token retrieval failed."""
