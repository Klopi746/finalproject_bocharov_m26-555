"""Validation, UTC timestamps and rate-conversion helpers."""

import math
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


def rate_from_pairs(pairs: dict, from_code: str, to_code: str) -> float:
    """Resolve direct, inverse or USD-cross rates from validated pair entries."""
    if from_code == to_code:
        return 1.0

    def read_pair(source: str, target: str):
        entry = pairs.get(f"{source}_{target}")
        if entry is None:
            return None
        value = entry.get("rate") if isinstance(entry, dict) else entry
        return validate_number(value, name="rate")

    direct = read_pair(from_code, to_code)
    if direct is not None:
        return direct
    inverse = read_pair(to_code, from_code)
    if inverse is not None:
        return validate_number(1.0 / inverse, name="rate")
    from_usd = 1.0 if from_code == "USD" else read_pair(from_code, "USD")
    to_usd = 1.0 if to_code == "USD" else read_pair(to_code, "USD")
    if from_usd is None or to_usd is None:
        raise ApiRequestError(f"Курс {from_code}→{to_code} недоступен")
    return validate_number(from_usd / to_usd, name="rate")
