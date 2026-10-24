"""Provider adapters exposing the same pair-to-rate dictionary interface."""

import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from urllib.parse import quote

import requests

from ..core.exceptions import ApiRequestError
from ..core.utils import iso_timestamp, validate_number
from .config import ParserConfig

HTTP_ERROR_REASONS = {
    401: "неверный ключ или недостаточно прав (HTTP 401)",
    403: "доступ запрещён (HTTP 403)",
    429: "превышен лимит запросов (HTTP 429), повторите позже",
}
PROVIDER_ERROR_REASONS = {
    "invalid-key": "неверный API-ключ",
    "inactive-account": "аккаунт не активирован, подтвердите адрес почты",
    "quota-reached": "исчерпан лимит запросов, повторите после сброса квоты",
    "unsupported-code": "источник не поддерживает базовую валюту",
    "malformed-request": "источник отклонил формат запроса",
}


class BaseApiClient(ABC):
    """A provider returning validated numeric quotes and separate safe metadata."""

    source_name = ""
    source_key = ""

    def __init__(self, config: ParserConfig | None = None):
        self.config = config or ParserConfig()
        self.last_metadata = {}
        self._request_meta = {}

    @abstractmethod
    def fetch_rates(self) -> dict[str, float]:
        """Fetch rates as {FROM_TO: positive_float}, raising ApiRequestError."""

    def _request_json(self, url: str, *, params: dict | None = None) -> dict:
        """Request JSON without exposing authenticated URLs in any exception."""
        started = time.monotonic()
        try:
            response = requests.get(
                url, params=params, timeout=self.config.REQUEST_TIMEOUT
            )
        except requests.Timeout:
            raise ApiRequestError(
                f"{self.source_name}: превышено время ожидания"
            ) from None
        except requests.RequestException:
            raise ApiRequestError(f"{self.source_name}: ошибка сети") from None
        if response.status_code != 200:
            reason = HTTP_ERROR_REASONS.get(
                response.status_code, f"сервис вернул HTTP {response.status_code}"
            )
            raise ApiRequestError(f"{self.source_name}: {reason}")
        try:
            payload = response.json()
        except ValueError:
            raise ApiRequestError(
                f"{self.source_name}: ответ не является JSON"
            ) from None
        if not isinstance(payload, dict):
            raise ApiRequestError(f"{self.source_name}: некорректная структура ответа")
        self._request_meta = {
            "request_ms": round((time.monotonic() - started) * 1000),
            "status_code": response.status_code,
        }
        etag = response.headers.get("ETag")
        if isinstance(etag, str):
            self._request_meta["etag"] = etag
        return payload

    def _validate_rate(self, value, pair: str) -> float:
        """Translate type/non-finite/zero quote failures into a safe domain error."""
        try:
            return validate_number(value, name="rate")
        except ValueError:
            raise ApiRequestError(
                f"{self.source_name}: некорректный курс {pair}"
            ) from None

    @staticmethod
    def _source_time(value) -> dict:
        """Preserve a provider's timestamp separately from local receipt time."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return {}
        try:
            date = datetime.fromtimestamp(value, timezone.utc)
        except (OSError, OverflowError, ValueError):
            return {}
        return {"source_updated_at": iso_timestamp(date)}


class CoinGeckoClient(BaseApiClient):
    """Batch cryptocurrency quotes using public access with no API key."""

    source_name = "CoinGecko"
    source_key = "coingecko"

    def fetch_rates(self) -> dict[str, float]:
        """Map CoinGecko IDs to symbols and keep provider time/capitalization."""
        self.last_metadata = {}
        base = self.config.BASE_CURRENCY
        payload = self._request_json(
            self.config.COINGECKO_URL,
            params={
                "ids": ",".join(
                    self.config.CRYPTO_ID_MAP[code]
                    for code in self.config.CRYPTO_CURRENCIES
                ),
                "vs_currencies": base.lower(),
                "include_last_updated_at": "true",
                "include_market_cap": "true",
            },
        )
        rates = {}
        for code in self.config.CRYPTO_CURRENCIES:
            raw_id = self.config.CRYPTO_ID_MAP[code]
            pair = f"{code}_{base}"
            entry = payload.get(raw_id)
            value = entry.get(base.lower()) if isinstance(entry, dict) else None
            rates[pair] = self._validate_rate(value, pair)
            metadata = {
                **self._request_meta,
                "raw_id": raw_id,
                **self._source_time(entry.get("last_updated_at")),
            }
            market_cap = entry.get(f"{base.lower()}_market_cap")
            if market_cap is not None:
                try:
                    metadata["market_cap"] = validate_number(
                        market_cap, name="market_cap", allow_zero=True
                    )
                except ValueError:
                    pass  # Optional capitalization must not invalidate a valid quote.
            self.last_metadata[pair] = metadata
        return rates


class ExchangeRateApiClient(BaseApiClient):
    """Fiat adapter for the keyed Standard endpoint, normalized to CODE_BASE."""

    source_name = "ExchangeRate-API"
    source_key = "exchangerate"

    def fetch_rates(self) -> dict[str, float]:
        """Invert BASE-to-CODE rates rather than mislabelling their direction."""
        self.last_metadata = {}
        if not self.config.EXCHANGERATE_API_KEY:
            raise ApiRequestError("ExchangeRate-API: не задан EXCHANGERATE_API_KEY")
        base = self.config.BASE_CURRENCY
        key = quote(self.config.EXCHANGERATE_API_KEY, safe="")
        url = f"{self.config.EXCHANGERATE_API_URL.rstrip('/')}/{key}/latest/{base}"
        payload = self._request_json(url)
        if payload.get("result") != "success":
            error_type = payload.get("error-type")
            reason = (
                PROVIDER_ERROR_REASONS.get(error_type, "источник сообщил об ошибке")
                if isinstance(error_type, str)
                else "источник сообщил об ошибке"
            )
            raise ApiRequestError(f"{self.source_name}: {reason}")
        raw_rates = payload.get("conversion_rates")
        if payload.get("base_code") != base or not isinstance(raw_rates, dict):
            raise ApiRequestError("ExchangeRate-API: неверная база или формат курсов")
        rates = {}
        for code in self.config.FIAT_CURRENCIES:
            if code == base:
                continue
            pair = f"{code}_{base}"
            inverse = self._validate_rate(raw_rates.get(code), pair)
            rates[pair] = self._validate_rate(1.0 / inverse, pair)
            self.last_metadata[pair] = {
                **self._request_meta,
                **self._source_time(payload.get("time_last_update_unix")),
            }
        return rates
