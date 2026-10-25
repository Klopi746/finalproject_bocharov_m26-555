"""User accounts, validated wallets and user-owned currency portfolios."""

import hashlib
import hmac
import secrets
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from .currencies import get_currency
from .exceptions import ApiRequestError, InsufficientFundsError
from .utils import (
    SALT_BYTES,
    iso_timestamp,
    parse_timestamp,
    validate_number,
    validate_password,
    validate_username,
)


class User:
    """A serialized user record; plaintext passwords are never stored."""

    def __init__(
        self,
        user_id: int,
        username: str,
        hashed_password: str,
        salt: str,
        registration_date: str | datetime,
    ):
        if type(user_id) is not int or user_id < 1:
            raise ValueError("ID пользователя должен быть положительным целым числом")
        if (
            not isinstance(hashed_password, str)
            or len(hashed_password) != hashlib.sha256().digest_size * 2
            or any(character not in "0123456789abcdef" for character in hashed_password)
        ):
            raise ValueError("Некорректный SHA-256 хеш пользователя")
        if not isinstance(salt, str) or not salt:
            raise ValueError("Соль пользователя не может быть пустой")
        self._user_id = user_id
        self.username = username
        self._hashed_password = hashed_password
        self._salt = salt
        self._registration_date = parse_timestamp(registration_date)

    @classmethod
    def create(cls, user_id: int, username: str, password: str):
        """Create an account with an individual salt and the required SHA-256."""
        validate_password(password)
        salt = secrets.token_hex(SALT_BYTES)
        hashed = hashlib.sha256((password + salt).encode("utf-8")).hexdigest()
        return cls(user_id, username, hashed, salt, iso_timestamp())

    @property
    def user_id(self) -> int:
        """Return the immutable identifier."""
        return self._user_id

    @property
    def username(self) -> str:
        """Return the display/login name."""
        return self._username

    @username.setter
    def username(self, value: str) -> None:
        """Reject empty names."""
        self._username = validate_username(value)

    @property
    def hashed_password(self) -> str:
        """Return the stored hash for persistence, never for the public profile."""
        return self._hashed_password

    @property
    def salt(self) -> str:
        """Return the per-user salt needed to verify or serialize the account."""
        return self._salt

    @property
    def registration_date(self) -> datetime:
        """Return an aware registration timestamp."""
        return self._registration_date

    def verify_password(self, password: str) -> bool:
        """Compare salted hashes without keeping the supplied password."""
        if not isinstance(password, str):
            return False
        candidate = hashlib.sha256((password + self._salt).encode("utf-8")).hexdigest()
        return hmac.compare_digest(candidate, self._hashed_password)

    def change_password(self, new_password: str) -> None:
        """Validate and replace the password hash using a fresh salt."""
        validate_password(new_password)
        salt = secrets.token_hex(SALT_BYTES)
        hashed = hashlib.sha256((new_password + salt).encode("utf-8")).hexdigest()
        self._salt, self._hashed_password = salt, hashed

    def get_user_info(self) -> dict:
        """Return public profile fields without the password hash or salt."""
        return {
            "user_id": self.user_id,
            "username": self.username,
            "registration_date": iso_timestamp(self.registration_date),
        }

    def to_dict(self) -> dict:
        """Serialize the protected record for users.json."""
        return {
            **self.get_user_info(),
            "hashed_password": self.hashed_password,
            "salt": self.salt,
        }

    @classmethod
    def from_dict(cls, data: dict):
        """Restore and validate a stored user."""
        return cls(
            data["user_id"],
            data["username"],
            data["hashed_password"],
            data["salt"],
            data["registration_date"],
        )


class Wallet:
    """A non-negative balance belonging to exactly one registered currency."""

    def __init__(self, currency_code: str, balance: float = 0.0):
        self._currency_code = get_currency(currency_code).code
        self.balance = balance

    @property
    def currency_code(self) -> str:
        """Return this wallet's fixed currency identity."""
        return self._currency_code

    @property
    def balance(self) -> float:
        """Return the validated numeric balance."""
        return self._balance

    @balance.setter
    def balance(self, value: float) -> None:
        """Prevent negative, non-numeric and non-finite balances."""
        self._balance = validate_number(value, name="balance", allow_zero=True)

    def deposit(self, amount: float) -> None:
        """Add a positive amount without binary float arithmetic artifacts."""
        amount = validate_number(amount)
        self.balance = float(Decimal(str(self.balance)) + Decimal(str(amount)))

    def withdraw(self, amount: float) -> None:
        """Subtract a positive amount only when it is available."""
        amount = validate_number(amount)
        if amount > self.balance:
            raise InsufficientFundsError(self.balance, amount, self.currency_code)
        self.balance = float(Decimal(str(self.balance)) - Decimal(str(amount)))

    def get_balance_info(self) -> str:
        """Describe the currency and balance for a user-facing view."""
        return f"{self.currency_code}: {self.balance:.4f}"

    def to_dict(self) -> dict:
        """Serialize a wallet without domain objects in the JSON value."""
        return {"currency_code": self.currency_code, "balance": self.balance}


class Portfolio:
    """User-owned wallets with injectable rate lookup for total valuation."""

    def __init__(
        self,
        user: User,
        wallets: dict[str, Wallet] | None = None,
        rate_provider: Callable[[str, str], float] | None = None,
    ):
        if not isinstance(user, User):
            raise ValueError("Портфель должен принадлежать пользователю")
        self._user = user
        self._user_id = user.user_id
        self._wallets = dict(wallets or {})
        self._rate_provider = rate_provider
        for code, wallet in self._wallets.items():
            if not isinstance(wallet, Wallet) or code != wallet.currency_code:
                raise ValueError("Некорректный кошелёк в портфеле")

    @property
    def user(self) -> User:
        """Return the owner without permitting reassignment."""
        return self._user

    @property
    def user_id(self) -> int:
        """Return the owning account identifier."""
        return self._user_id

    @property
    def wallets(self) -> dict[str, Wallet]:
        """Return a dictionary copy so callers cannot replace its entries."""
        return self._wallets.copy()

    def add_currency(self, currency_code: str) -> Wallet:
        """Create a wallet once and preserve its balance on repeated calls."""
        code = get_currency(currency_code).code
        if code not in self._wallets:
            self._wallets[code] = Wallet(code)
        return self._wallets[code]

    def get_wallet(self, currency_code: str) -> Wallet | None:
        """Return a supported currency's wallet when it exists."""
        get_currency(currency_code)
        return self._wallets.get(currency_code)

    def get_total_value(self, base_currency: str = "USD") -> float:
        """Convert all non-zero holdings using the injected current rate source."""
        get_currency(base_currency)
        values = []
        for code, wallet in self._wallets.items():
            if not wallet.balance:
                continue
            if code == base_currency:
                rate = 1.0
            elif self._rate_provider is not None:
                rate = validate_number(
                    self._rate_provider(code, base_currency), name="rate"
                )
            else:
                raise ApiRequestError(
                    "Источник курсов для оценки портфеля не подключён"
                )
            values.append(wallet.balance * rate)
        return validate_number(sum(values), name="total", allow_zero=True)

    def to_dict(self) -> dict:
        """Serialize holdings in the assignment's portfolios.json format."""
        return {
            "user_id": self.user_id,
            "wallets": {
                code: {"balance": wallet.balance}
                for code, wallet in self._wallets.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict, user: User, rate_provider=None):
        """Restore wallets while checking the stored ownership relationship."""
        if data["user_id"] != user.user_id or not isinstance(data["wallets"], dict):
            raise ValueError("Некорректный владелец или состав портфеля")
        wallets = {
            code: Wallet(code, entry["balance"])
            for code, entry in data["wallets"].items()
        }
        return cls(user, wallets, rate_provider)
