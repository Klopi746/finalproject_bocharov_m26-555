"""Validation, UTC timestamps and rate-conversion helpers."""

import math
from collections import deque
from datetime import datetime, timezone

from .exceptions import ApiRequestError

MIN_PASSWORD_LENGTH = 4
MIN_CURRENCY_CODE_LENGTH = 2
MAX_CURRENCY_CODE_LENGTH = 5
SALT_BYTES = 16


def validate_currency_code(code: str) -> str:
    """Require an uppercase ASCII currency code of two to five letters."""
    if (
        not isinstance(code, str)
        or not MIN_CURRENCY_CODE_LENGTH <= len(code) <= MAX_CURRENCY_CODE_LENGTH
        or not code.isascii()
        or not code.isalpha()
        or not code.isupper()
    ):
        raise ValueError("Код валюты: 2–5 латинских букв в верхнем регистре.")
    return code


def validate_number(value, *, name: str = "amount", allow_zero: bool = False) -> float:
    """Accept finite int/float values, excluding booleans and negative numbers."""
    message = (
        f"'{name}' должен быть неотрицательным числом"
        if allow_zero
        else f"'{name}' должен быть положительным числом"
    )
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(message)
    try:
        number = float(value)
    except OverflowError:
        raise ValueError(message) from None
    if not math.isfinite(number) or number < 0 or (number == 0 and not allow_zero):
        raise ValueError(message)
    return number


def validate_username(username: str) -> str:
    """Return a trimmed, non-empty username without changing its case."""
    if not isinstance(username, str) or not username.strip():
        raise ValueError("Имя пользователя не может быть пустым")
    return username.strip()


def validate_password(password: str) -> str:
    """Enforce the assignment's minimum password length without echoing it."""
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов")
    return password


def utc_now() -> datetime:
    """Return an aware UTC timestamp."""
    return datetime.now(timezone.utc)


def parse_timestamp(value: str | datetime) -> datetime:
    """Read ISO timestamps; legacy timezone-less values are interpreted as UTC."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise ValueError("Ожидается дата в ISO-формате")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def iso_timestamp(value: datetime | None = None) -> str:
    """Serialize UTC with enough precision to identify individual observations."""
    return parse_timestamp(value or utc_now()).isoformat().replace("+00:00", "Z")


def resolve_rate(pairs: dict, from_code: str, to_code: str) -> tuple[float, tuple]:
    """Find a shortest conversion path and identify every quote it uses."""
    if from_code == to_code:
        return 1.0, ()
    graph = {}
    for pair, entry in sorted(pairs.items()):
        source, target = pair.split("_")
        value = entry.get("rate") if isinstance(entry, dict) else entry
        rate = validate_number(value, name="rate")
        graph.setdefault(source, []).append((target, rate, pair))
        graph.setdefault(target, []).append((source, 1.0 / rate, pair))
    pending = deque([(from_code, 1.0, ())])
    visited = {from_code}
    while pending:
        currency, accumulated, path = pending.popleft()
        for target, rate, pair in graph.get(currency, []):
            if target in visited:
                continue
            converted = validate_number(accumulated * rate, name="rate")
            used = (*path, pair)
            if target == to_code:
                return converted, used
            visited.add(target)
            pending.append((target, converted, used))
    raise ApiRequestError(f"Курс {from_code}→{to_code} недоступен")


def rate_from_pairs(pairs: dict, from_code: str, to_code: str) -> float:
    """Resolve a rate without exposing the internal conversion path."""
    return resolve_rate(pairs, from_code, to_code)[0]
