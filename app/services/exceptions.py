"""VEXARIUM service-layer exception hierarchy.

Raising an error here logs a debug line so the raised-vs-caught path is
traceable; callers still own the error/warning logging at their level.
"""
from ..logging import get_logger

logger = get_logger("exceptions")


class AlpacaError(Exception):
    """Base error for all Alpaca-related issues."""

    def __init__(self, message: str = ""):
        super().__init__(message)
        logger.debug("AlpacaError raised type=%s message=%s", type(self).__name__, message)


class SymbolNotFoundError(AlpacaError):
    """Invalid ticker symbol."""


class SubscriptionRequiredError(AlpacaError):
    """Options data not available on user's plan."""
