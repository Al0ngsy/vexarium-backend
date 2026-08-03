class AlpacaError(Exception):
    """Base error for all Alpaca-related issues."""


class SymbolNotFoundError(AlpacaError):
    """Invalid ticker symbol."""


class SubscriptionRequiredError(AlpacaError):
    """Options data not available on user's plan."""
