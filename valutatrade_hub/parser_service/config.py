"""Centralize provider settings while loading API keys only from the environment."""

import os
from dataclasses import dataclass, field

from ..infra.settings import SettingsLoader


@dataclass(frozen=True)
class ParserConfig:
    """Provider configuration; secret fields are excluded from representations."""

    EXCHANGERATE_API_KEY: str = field(
        default_factory=lambda: os.getenv("EXCHANGERATE_API_KEY", "").strip(),
        repr=False,
    )
    COINGECKO_URL: str = "https://api.coingecko.com/api/v3/simple/price"
    EXCHANGERATE_API_URL: str = "https://v6.exchangerate-api.com/v6"
    BASE_CURRENCY: str = "USD"
    FIAT_CURRENCIES: tuple[str, ...] = ("EUR", "GBP", "RUB")
    CRYPTO_CURRENCIES: tuple[str, ...] = ("BTC", "ETH", "SOL")
    CRYPTO_ID_MAP: dict[str, str] = field(
        default_factory=lambda: {
            "BTC": "bitcoin",
            "ETH": "ethereum",
            "SOL": "solana",
        }
    )
    REQUEST_TIMEOUT: int = 10

    @property
    def RATES_FILE_PATH(self) -> str:
        """Use the same cache path as Core Service, not a second editable default."""
        return str(SettingsLoader().get_path("rates_file"))

    @property
    def HISTORY_FILE_PATH(self) -> str:
        """Expose the history path from the shared settings source of truth."""
        return str(SettingsLoader().get_path("history_file"))
