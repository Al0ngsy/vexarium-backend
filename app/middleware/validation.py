import re

from fastapi import HTTPException

from ..logging import get_logger

logger = get_logger("validation")

SYMBOL_PATTERN = re.compile(r"^[A-Z]{1,10}(\.[A-Z]{1,2})?$")


def validate_symbol(symbol: str) -> str:
    sym = symbol.strip().upper()
    if not SYMBOL_PATTERN.match(sym):
        logger.debug("symbol rejected raw=%s", symbol)
        raise HTTPException(status_code=422, detail=f"Invalid symbol format: {symbol}")
    logger.debug("symbol validated %s", sym)
    return sym
