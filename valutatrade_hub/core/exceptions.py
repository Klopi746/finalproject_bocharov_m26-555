"""Domain failures translated into user-facing messages by the CLI."""


class InsufficientFundsError(ValueError):
    """A withdrawal exceeds the amount held in a wallet."""

    def __init__(self, available: float, required: float, code: str):
        self.available = available
        self.required = required
        self.code = code
        super().__init__(
            f"Недостаточно средств: доступно {available:.4f} {code}, "
            f"требуется {required:.4f} {code}"
        )


class CurrencyNotFoundError(ValueError):
    """The supplied currency code is not registered by the application."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Неизвестная валюта '{code}'")


class ApiRequestError(RuntimeError):
    """External rate data could not be obtained or validated."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"Ошибка при обращении к внешнему API: {reason}")


class StorageError(RuntimeError):
    """Local JSON data could not be read or saved safely."""
