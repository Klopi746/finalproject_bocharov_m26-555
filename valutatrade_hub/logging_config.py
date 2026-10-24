"""JSON operation logs with rotation and protection against credential leakage."""

import json
import logging
import math
import os
import re
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

from .core.utils import iso_timestamp
from .infra.settings import SettingsLoader

SECRET_ENVIRONMENT_NAMES = ("EXCHANGERATE_API_KEY", "COINGECKO_DEMO_API_KEY")
AUTHENTICATED_URL_PATTERN = re.compile(
    r"(https?://v6\.exchangerate-api\.com/v6/)[^/\s?]+"
)
LOG_FILES = {"valutatrade.actions": "actions.log", "valutatrade.parser": "parser.log"}


def redact_secrets(message: str) -> str:
    """Remove configured API keys and credentials embedded in provider URLs."""
    message = str(message)
    for name in SECRET_ENVIRONMENT_NAMES:
        secret = os.getenv(name, "").strip()
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return AUTHENTICATED_URL_PATTERN.sub(r"\1[REDACTED]", message)


class JsonLogFormatter(logging.Formatter):
    """Serialize a structured event without request objects or raw tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        """Use UTC timestamps and sanitize string fields before JSON encoding."""
        fields = {
            "timestamp": iso_timestamp(
                datetime.fromtimestamp(record.created, timezone.utc)
            ),
            "level": record.levelname,
            "message": record.getMessage(),
            **getattr(record, "event_fields", {}),
        }
        safe = {}
        for key, value in fields.items():
            if isinstance(value, str):
                value = redact_secrets(value)
            elif isinstance(value, float) and not math.isfinite(value):
                value = str(value)
            safe[key] = value
        return json.dumps(safe, ensure_ascii=False, allow_nan=False, default=str)


def configure_logging(settings: SettingsLoader | None = None) -> None:
    """Attach rotating JSON files; repeated setup closes previous handlers."""
    settings = settings or SettingsLoader()
    directory = settings.get_path("logs_dir")
    directory.mkdir(parents=True, exist_ok=True)
    level = settings.get("log_level")
    for name, filename in LOG_FILES.items():
        logger = logging.getLogger(name)
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)
        handler = RotatingFileHandler(
            directory / filename,
            maxBytes=settings.get("log_max_bytes"),
            backupCount=settings.get("log_backup_count"),
            encoding="utf-8",
        )
        handler.setFormatter(JsonLogFormatter())
        logger.addHandler(handler)
        logger.setLevel(level)
        logger.propagate = False
    # HTTP debug logs can contain authenticated URLs; the app logs safe summaries.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
