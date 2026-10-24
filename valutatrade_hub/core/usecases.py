"""Application use cases; the CLI only parses input and renders these results."""

from dataclasses import dataclass

from ..decorators import log_action
from ..infra.database import DatabaseManager
from ..infra.settings import SettingsLoader
from ..parser_service.storage import RatesStorage
from ..parser_service.updater import RatesUpdater
from .currencies import CryptoCurrency, get_currency
from .exceptions import (
    ApiRequestError,
    CurrencyNotFoundError,
    InsufficientFundsError,
    StorageError,
)
from .models import Portfolio, User, Wallet
from .utils import (
    iso_timestamp,
    parse_timestamp,
    resolve_rate,
    utc_now,
    validate_currency_code,
    validate_number,
    validate_password,
    validate_username,
)


@dataclass(frozen=True)
class RateQuote:
    """A conversion quote identifying cache age and original provider timestamps."""

    from_code: str
    to_code: str
    rate: float
    updated_at: str
    sources: tuple[str, ...]
    provider_times: dict[str, str]
    stale: bool


class CoreService:
    """Own an in-process login session and coordinate domain/persistence services."""

    def __init__(self, database=None, settings=None, updater=None):
        self.settings = settings or SettingsLoader()
        self.database = database or DatabaseManager(self.settings)
        self.storage = RatesStorage(self.database)
        self.updater = updater or RatesUpdater(storage=self.storage)
        self._current_user = None

    @property
    def current_user_id(self) -> int | None:
        """Identify the authenticated user without retaining plaintext credentials."""
        return self._current_user.user_id if self._current_user else None

    @property
    def current_username(self) -> str | None:
        """Return a safe display name for operation logs."""
        return self._current_user.username if self._current_user else None

    def _require_login(self) -> User:
        """Protect portfolio operations even when called without the CLI."""
        if self._current_user is None:
            raise ValueError("Сначала выполните login")
        return self._current_user

    def _users(self) -> list[User]:
        """Restore accounts while rejecting duplicate identities or damaged records."""
        try:
            users = [User.from_dict(row) for row in self.database.read("users")]
            if len({user.user_id for user in users}) != len(users):
                raise ValueError("Duplicate user ID")
            if len({user.username for user in users}) != len(users):
                raise ValueError("Duplicate username")
            return users
        except (KeyError, TypeError, ValueError):
            raise StorageError("Некорректные данные пользователей") from None

    def _portfolio_records(self) -> list[dict]:
        """Read portfolio identities without silently repairing user holdings."""
        rows = self.database.read("portfolios")
        identifiers = [row.get("user_id") for row in rows]
        if any(type(value) is not int or value < 1 for value in identifiers):
            raise StorageError("Некорректные владельцы портфелей")
        if len(set(identifiers)) != len(identifiers):
            raise StorageError("В хранилище повторяются портфели пользователя")
        return rows

    def _portfolio(self, user: User) -> Portfolio:
        """Restore a portfolio and connect valuation to the real rate service."""
        for row in self._portfolio_records():
            if row["user_id"] == user.user_id:
                try:
                    return Portfolio.from_dict(
                        row,
                        user,
                        lambda source, target: self.get_rate(source, target).rate,
                    )
                except (KeyError, TypeError, ValueError):
                    raise StorageError("Некорректные данные кошельков") from None
        raise StorageError("Портфель пользователя не найден; данные не изменены")

    def _save_portfolio(self, portfolio: Portfolio) -> None:
        """Replace one user's record while retaining every other user's holdings."""
        rows = self._portfolio_records()
        for index, row in enumerate(rows):
            if row["user_id"] == portfolio.user_id:
                rows[index] = portfolio.to_dict()
                self.database.write("portfolios", rows)
                return
        raise StorageError("Портфель пользователя не найден; данные не изменены")

    def register(self, username: str, password: str) -> User:
        """Create an account and an empty portfolio with no invented starting money."""
        username = validate_username(username)
        validate_password(password)
        with self.database.lock:
            users = self._users()
            if any(user.username == username for user in users):
                raise ValueError(f"Имя пользователя '{username}' уже занято")
            user_id = max((user.user_id for user in users), default=0) + 1
            portfolios = self._portfolio_records()
            if any(row["user_id"] == user_id for row in portfolios):
                raise StorageError(
                    "Обнаружен портфель без соответствующего пользователя"
                )
            user = User.create(user_id, username, password)
            self.database.write_many(
                {
                    "users": [*(item.to_dict() for item in users), user.to_dict()],
                    "portfolios": [*portfolios, Portfolio(user).to_dict()],
                }
            )
            return user

    def login(self, username: str, password: str) -> User:
        """Authenticate into this running process, not a persisted password session."""
        username = validate_username(username)
        user = next((user for user in self._users() if user.username == username), None)
        if user is None:
            raise ValueError(f"Пользователь '{username}' не найден")
        if not user.verify_password(password):
            raise ValueError("Неверный пароль")
        self._current_user = user
        return user

    def _fresh(self, entry: dict) -> bool:
        """Apply the configured TTL to local receipt time, not provider quote time."""
        age = (utc_now() - parse_timestamp(entry["updated_at"])).total_seconds()
        return 0 <= age <= self.settings.get("rates_ttl_seconds")

    def _quote(self, cache: dict, from_code: str, to_code: str, *, fresh_only: bool):
        """Resolve a conversion using only the pair entries allowed by age policy."""
        pairs = {
            pair: entry
            for pair, entry in cache["pairs"].items()
            if not fresh_only or self._fresh(entry)
        }
        rate, path = resolve_rate(pairs, from_code, to_code)
        entries = [pairs[pair] for pair in path]
        updated_at = (
            min(entries, key=lambda entry: parse_timestamp(entry["updated_at"]))[
                "updated_at"
            ]
            if entries
            else iso_timestamp()
        )
        provider_times = {}
        for entry in entries:
            stamp = entry.get("meta", {}).get("source_updated_at")
            if stamp:
                source = entry["source"]
                if source not in provider_times or parse_timestamp(
                    stamp
                ) < parse_timestamp(provider_times[source]):
                    provider_times[source] = stamp
        return RateQuote(
            from_code,
            to_code,
            rate,
            updated_at,
            tuple(sorted({entry["source"] for entry in entries})),
            provider_times,
            any(not self._fresh(entry) for entry in entries),
        )

    @staticmethod
    def _source_for(from_code: str, to_code: str) -> str | None:
        """Refresh only needed providers when one USD-based source is sufficient."""
        currencies = [
            get_currency(code) for code in (from_code, to_code) if code != "USD"
        ]
        crypto = any(isinstance(currency, CryptoCurrency) for currency in currencies)
        fiat = any(not isinstance(currency, CryptoCurrency) for currency in currencies)
        if crypto and fiat:
            return None
        return "coingecko" if crypto else "exchangerate"

    @log_action("GET_RATE")
    def get_rate(self, from_code: str, to_code: str) -> RateQuote:
        """Read a fresh quote or refresh once; never disguise a stale cached quote."""
        get_currency(from_code)
        get_currency(to_code)
        cache = self.storage.read_cache()
        try:
            return self._quote(cache, from_code, to_code, fresh_only=True)
        except ApiRequestError:
            pass
        try:
            self.updater.run_update(self._source_for(from_code, to_code))
        except ApiRequestError:
            pass  # Preserve the standard unavailable-quote message below.
        try:
            return self._quote(
                self.storage.read_cache(), from_code, to_code, fresh_only=True
            )
        except ApiRequestError:
            raise ApiRequestError(
                f"Курс {from_code}→{to_code} недоступен. "
                "Кеш отсутствует или устарел. Повторите попытку позже / update-rates."
            ) from None

    def show_portfolio(self, base: str | None = None) -> dict:
        """Describe authenticated holdings and their total in the chosen base."""
        user = self._require_login()
        base = base or self.settings.get("default_base_currency")
        try:
            get_currency(base)
        except CurrencyNotFoundError:
            raise ValueError(f"Неизвестная базовая валюта '{base}'") from None
        portfolio = self._portfolio(user)
        rows = []
        for code, wallet in sorted(portfolio.wallets.items()):
            rate = self.get_rate(code, base).rate if wallet.balance else 1.0
            rows.append(
                {
                    "currency_code": code,
                    "balance": wallet.balance,
                    "value": wallet.balance * rate,
                }
            )
        return {
            "username": user.username,
            "base": base,
            "wallets": rows,
            "total": portfolio.get_total_value(base),
        }

    @log_action("BUY", verbose=True)
    def buy(self, currency_code: str, amount: float) -> dict:
        """Increase purchased units; USD is valuation only, as agreed with the user."""
        user = self._require_login()
        get_currency(currency_code)
        amount = validate_number(amount)
        quote = self.get_rate(currency_code, "USD")
        value = validate_number(amount * quote.rate, name="value")
        with self.database.lock:
            portfolio = self._portfolio(user)
            wallet = portfolio.add_currency(currency_code)
            before = wallet.balance
            wallet.deposit(amount)
            self._save_portfolio(portfolio)
            return {
                "currency_code": currency_code,
                "amount": amount,
                "before": before,
                "after": wallet.balance,
                "rate": quote.rate,
                "base": "USD",
                "value": value,
                "user_id": user.user_id,
                "username": user.username,
            }

    @staticmethod
    def _sale_wallet(portfolio: Portfolio, currency_code: str, amount: float) -> Wallet:
        """Validate holdings before a sale or any potentially slow API refresh."""
        wallet = portfolio.get_wallet(currency_code)
        if wallet is None:
            raise ValueError(
                f"У вас нет кошелька '{currency_code}'. "
                "Добавьте валюту: она создаётся автоматически при первой покупке."
            )
        if amount > wallet.balance:
            raise InsufficientFundsError(wallet.balance, amount, currency_code)
        return wallet

    @log_action("SELL", verbose=True)
    def sell(self, currency_code: str, amount: float) -> dict:
        """Withdraw held units without inventing a separate USD settlement wallet."""
        user = self._require_login()
        get_currency(currency_code)
        amount = validate_number(amount)
        self._sale_wallet(self._portfolio(user), currency_code, amount)
        quote = self.get_rate(currency_code, "USD")
        value = validate_number(amount * quote.rate, name="value")
        with self.database.lock:
            portfolio = self._portfolio(user)
            wallet = self._sale_wallet(portfolio, currency_code, amount)
            before = wallet.balance
            wallet.withdraw(amount)
            self._save_portfolio(portfolio)
            return {
                "currency_code": currency_code,
                "amount": amount,
                "before": before,
                "after": wallet.balance,
                "rate": quote.rate,
                "base": "USD",
                "value": value,
                "user_id": user.user_id,
                "username": user.username,
            }

    def update_rates(self, source: str | None = None):
        """Run the real Parser Service without requiring an authenticated user."""
        return self.updater.run_update(source)

    def show_rates(self, currency=None, top=None, base=None) -> dict:
        """Inspect cached rates and filters without making hidden network requests."""
        base = base or self.settings.get("default_base_currency")
        get_currency(base)
        if top is not None and (type(top) is not int or top <= 0):
            raise ValueError("'top' должен быть положительным целым числом")
        cache = self.storage.read_cache()
        if not cache["pairs"]:
            raise ValueError(
                "Локальный кеш курсов пуст. "
                "Выполните 'update-rates', чтобы загрузить данные."
            )
        codes = {code for pair in cache["pairs"] for code in pair.split("_")}
        if currency is not None:
            validate_currency_code(currency)
            if currency not in codes:
                raise ValueError(f"Курс для '{currency}' не найден в кеше.")
            candidates = [currency]
        else:
            candidates = sorted(codes - {base})
        if top is not None:
            candidates = [
                code
                for code in candidates
                if isinstance(get_currency(code), CryptoCurrency)
            ]
        quotes = [
            self._quote(cache, code, base, fresh_only=False) for code in candidates
        ]
        if top is not None:
            quotes = sorted(quotes, key=lambda quote: quote.rate, reverse=True)[:top]
        return {
            "quotes": quotes,
            "last_refresh": cache.get("last_refresh"),
            "base": base,
        }
