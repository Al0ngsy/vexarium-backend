import re

from fastapi import HTTPException

SYMBOL_PATTERN = re.compile(r"^[A-Z^]{1,10}$")


def validate_symbol(symbol: str) -> str:
    sym = symbol.strip().upper()
    if not SYMBOL_PATTERN.match(sym):
        raise HTTPException(status_code=422, detail=f"Invalid symbol format: {symbol}")
    return sym
