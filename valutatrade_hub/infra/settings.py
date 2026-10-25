"""One application configuration loaded from pyproject.toml or config.json."""

import json
import tomllib
from pathlib import Path

DEFAULT_SETTINGS = {
    "data_dir": "data",
    "users_file": "data/users.json",
    "portfolios_file": "data/portfolios.json",
    "rates_file": "data/rates.json",
    "history_file": "data/exchange_rates.json",
    "logs_dir": "logs",
    "rates_ttl_seconds": 300,
    "default_base_currency": "USD",
    "log_level": "INFO",
    "log_max_bytes": 1_000_000,
    "log_backup_count": 3,
    "log_format": "json",
}


class SettingsLoader:
    """Cache one configuration instance; __new__ keeps the Singleton explicit."""

    _instance = None

    def __new__(cls, *args, **kwargs):
        # __new__ is sufficient here and clearer than a metaclass for one class.
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, config_path: str | Path | None = None):
        if self._initialized:
            return
        self._config_path = Path(config_path or "pyproject.toml").resolve()
        self._base_dir = self._config_path.parent
        self.reload()
        self._initialized = True

    def reload(self) -> None:
        """Refresh configuration without replacing the Singleton instance."""
        values = {}
        if self._config_path.is_file():
            if self._config_path.suffix == ".json":
                with self._config_path.open(encoding="utf-8") as source:
                    values = json.load(source)
            else:
                with self._config_path.open("rb") as source:
                    values = tomllib.load(source).get("tool", {}).get("valutatrade", {})
        if not isinstance(values, dict):
            raise ValueError("Настройки должны содержать объект параметров")
        values = {key.lower(): value for key, value in values.items()}
        self._values = {**DEFAULT_SETTINGS, **values}
        data_dir = self.get("data_dir")
        if not isinstance(data_dir, str) or not data_dir:
            raise ValueError("data_dir должен быть непустым путём")
        for key in ("users_file", "portfolios_file", "rates_file", "history_file"):
            if key not in values:
                self._values[key] = str(
                    Path(data_dir) / Path(DEFAULT_SETTINGS[key]).name
                )
        ttl = self.get("rates_ttl_seconds")
        if type(ttl) is not int or ttl <= 0:
            raise ValueError("rates_ttl_seconds должен быть положительным целым числом")
        for key in ("log_max_bytes", "log_backup_count"):
            value = self.get(key)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{key} должен быть положительным целым числом")
        if self.get("log_format") != "json":
            raise ValueError("Поддерживается формат логов json")
        if self.get("log_level") not in (
            "DEBUG",
            "INFO",
            "WARNING",
            "ERROR",
            "CRITICAL",
        ):
            raise ValueError("Некорректный уровень логирования")

    def get(self, key: str, default=None):
        """Return a setting using case-insensitive configuration keys."""
        return self._values.get(key.lower(), default)

    def get_path(self, key: str) -> Path:
        """Resolve configured paths relative to the configuration location."""
        value = self.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"Не задан путь {key}")
        path = Path(value)
        return path if path.is_absolute() else self._base_dir / path
