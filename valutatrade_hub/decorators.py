"""Structured domain-operation logging that preserves results and exceptions."""

import inspect
import logging
from functools import wraps

from .logging_config import redact_secrets

logger = logging.getLogger("valutatrade.actions")


def log_action(action_name: str, *, verbose: bool = False):
    """Log the operation's safe context and re-raise all domain failures."""

    def decorator(function):
        """Attach action identity without changing the business-function API."""
        signature = inspect.signature(function)

        @wraps(function)
        def wrapper(*args, **kwargs):
            """Record only named public fields; passwords and arguments stay private."""
            arguments = signature.bind_partial(*args, **kwargs).arguments
            owner = arguments.get("self")
            fields = {
                "action": action_name.upper(),
                "username": getattr(owner, "current_username", None),
                "user_id": getattr(owner, "current_user_id", arguments.get("user_id")),
                "currency_code": arguments.get(
                    "currency_code", arguments.get("from_code")
                ),
                "amount": arguments.get("amount"),
                "base": "USD",
                "rate": None,
            }
            try:
                result = function(*args, **kwargs)
            except Exception as error:
                fields.update(
                    result="ERROR",
                    error_type=type(error).__name__,
                    error_message=redact_secrets(str(error)),
                )
                logger.info("Domain operation failed", extra={"event_fields": fields})
                raise
            if isinstance(result, dict):
                for key in ("rate", "base", "user_id", "username"):
                    if key in result:
                        fields[key] = result[key]
                if verbose:
                    fields.update(
                        before=result.get("before"), after=result.get("after")
                    )
            elif hasattr(result, "rate"):
                fields.update(rate=result.rate, base=result.to_code)
            fields["result"] = "OK"
            logger.info("Domain operation completed", extra={"event_fields": fields})
            return result

        return wrapper

    return decorator
