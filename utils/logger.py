"""
utils/logger.py — structured logging setup.
"""
import logging
import sys
from pathlib import Path
import config

LOG_DIR = Path(config.BASE_DIR) / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def setup_logging(level: str = "INFO"):
    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "app.log", encoding="utf-8"),
    ]
    logging.basicConfig(level=getattr(logging, level), format=fmt, handlers=handlers)
    # silence noisy third-party libs
    for lib in ("httpx", "httpcore", "urllib3", "telegram"):
        logging.getLogger(lib).setLevel(logging.WARNING)
