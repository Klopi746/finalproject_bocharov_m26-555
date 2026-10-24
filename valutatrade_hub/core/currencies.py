"""Currency hierarchy and the registry used by domain operations."""

from abc import ABC, abstractmethod

from .exceptions import CurrencyNotFoundError
from .utils import validate_currency_code, validate_number

FIAT_CATALOG = {
    "USD": ("US Dollar", "United States"),
    "EUR": ("Euro", "Eurozone"),
    "GBP": ("Pound Sterling", "United Kingdom"),
    "RUB": ("Russian Ruble", "Russia"),
}
CRYPTO_CATALOG = {
    "BTC": ("Bitcoin", "SHA-256"),
    "ETH": ("Ethereum", "Proof-of-Stake"),
    "SOL": ("Solana", "Proof-of-History / Proof-of-Stake"),
}


class Currency(ABC):
    """An identified currency with a validated name and a polymorphic display."""

    def __init__(self, name: str, code: str):
        self.name = name
        self.code = code

    @property
    def name(self) -> str:
        """Return the human-readable name."""
        return self._name

    @name.setter
    def name(self, value: str) -> None:
        """Preserve the non-empty-name invariant on assignment."""
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Название валюты не может быть пустым")
        self._name = value.strip()

    @property
    def code(self) -> str:
        """Return the uppercase identifier."""
        return self._code

    @code.setter
    def code(self, value: str) -> None:
        """Preserve the code format invariant on assignment."""
        self._code = validate_currency_code(value)

    @abstractmethod
    def get_display_info(self) -> str:
        """Describe currency-specific metadata for UI or logs."""


class FiatCurrency(Currency):
    """A fiat currency issued by a country or monetary union."""

    def __init__(self, name: str, code: str, issuing_country: str):
        super().__init__(name, code)
        if not isinstance(issuing_country, str) or not issuing_country.strip():
            raise ValueError("Страна или зона эмиссии не может быть пустой")
        self.issuing_country = issuing_country.strip()

    def get_display_info(self) -> str:
        """Return the assignment's fiat display format."""
        return f"[FIAT] {self.code} — {self.name} (Issuing: {self.issuing_country})"


class CryptoCurrency(Currency):
    """A cryptocurrency with its algorithm and optionally known capitalization."""

    def __init__(self, name: str, code: str, algorithm: str, market_cap: float = 0.0):
        super().__init__(name, code)
        if not isinstance(algorithm, str) or not algorithm.strip():
            raise ValueError("Алгоритм не может быть пустым")
        self.algorithm = algorithm.strip()
        self.market_cap = validate_number(
            market_cap, name="market_cap", allow_zero=True
        )

    def get_display_info(self) -> str:
        """Display capitalization when known, not a fabricated market estimate."""
        market_cap = (
            f"{self.market_cap:.2e}".replace("e+", "e") if self.market_cap else "n/a"
        )
        return (
            f"[CRYPTO] {self.code} — {self.name} "
            f"(Algo: {self.algorithm}, MCAP: {market_cap})"
        )


def get_currency(code: str) -> Currency:
    """Create a registered currency or report an unknown/invalid code."""
    try:
        validate_currency_code(code)
    except ValueError:
        raise CurrencyNotFoundError(code) from None
    if code in FIAT_CATALOG:
        name, country = FIAT_CATALOG[code]
        return FiatCurrency(name, code, country)
    if code in CRYPTO_CATALOG:
        name, algorithm = CRYPTO_CATALOG[code]
        return CryptoCurrency(name, code, algorithm)
    raise CurrencyNotFoundError(code)


def get_supported_codes() -> tuple[str, ...]:
    """Return the supported codes without exposing mutable registry state."""
    return tuple(sorted((*FIAT_CATALOG, *CRYPTO_CATALOG)))
