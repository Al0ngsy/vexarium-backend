from slowapi import Limiter
from slowapi.util import get_remote_address

from ..logging import get_logger

logger = get_logger("rate_limit")

limiter = Limiter(key_func=get_remote_address)
logger.debug("rate limiter initialized key_func=get_remote_address")
