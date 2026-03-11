"""
utils/retry.py — exponential backoff retry decorator.
"""
import time
import logging
import functools
from typing import Tuple, Type
import config

logger = logging.getLogger(__name__)


def retry(
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    max_tries: int = config.MAX_RETRIES,
    backoff: float = config.RETRY_BACKOFF,
    logger_: logging.Logger = None,
):
    """
    Decorator: retries the wrapped function on specified exceptions.
    Usage:
        @retry(exceptions=(requests.Timeout, IOError), max_tries=3)
        def fetch_feed(url): ...
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            _log = logger_ or logger
            attempt = 0
            while attempt < max_tries:
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:
                    attempt += 1
                    if attempt == max_tries:
                        _log.error(
                            "%s failed after %d attempts: %s",
                            fn.__name__, max_tries, exc
                        )
                        raise
                    wait = backoff ** attempt
                    _log.warning(
                        "%s failed (attempt %d/%d): %s — retrying in %.1fs",
                        fn.__name__, attempt, max_tries, exc, wait,
                    )
                    time.sleep(wait)
        return wrapper
    return decorator
