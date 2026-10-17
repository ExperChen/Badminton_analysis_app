"""Bounded diagnostic logs for the administrator; no request bodies or credentials."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent / ".logs" / "service.log"


def configure():
    logger = logging.getLogger("badminton")
    if not logger.handlers:
        LOG_PATH.parent.mkdir(exist_ok=True)
        handler = RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)


def recent_lines(limit=150):
    if not LOG_PATH.exists():
        return []
    with LOG_PATH.open("rb") as file:
        file.seek(max(0, LOG_PATH.stat().st_size - 64_000))
        lines = file.read().decode("utf-8", errors="replace").splitlines()
    return lines[-limit:]
