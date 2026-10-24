"""Validated current-rate snapshots and a deduplicated observation history."""

from ..core.exceptions import StorageError
from ..core.utils import parse_timestamp, validate_currency_code, validate_number
from ..infra.database import DatabaseManager


class RatesStorage:
    """Keep Parser Service observations and the Core Service cache in sync."""

    def __init__(self, database: DatabaseManager | None = None):
        self.database = database or DatabaseManager()

    @staticmethod
    def _validate_pair(pair: str, entry: dict) -> None:
        """Reject malformed cache entries rather than silently overwriting them."""
        try:
            source, target = pair.split("_")
            validate_currency_code(source)
            validate_currency_code(target)
            validate_number(entry["rate"], name="rate")
            parse_timestamp(entry["updated_at"])
            if not isinstance(entry["source"], str) or not entry["source"]:
                raise ValueError("Missing rate source")
            if "meta" in entry and not isinstance(entry["meta"], dict):
                raise ValueError("Invalid rate metadata")
        except (AttributeError, KeyError, TypeError, ValueError):
            raise StorageError("Некорректная запись в кеше курсов") from None

    def read_cache(self) -> dict:
        """Read the latest cache using the final assignment's pairs format."""
        cache = self.database.read("rates")
        for pair, entry in cache["pairs"].items():
            self._validate_pair(pair, entry)
        if cache.get("last_refresh") is not None:
            try:
                parse_timestamp(cache["last_refresh"])
            except (TypeError, ValueError):
                raise StorageError("Некорректное время обновления кеша") from None
        return cache

    def _observation_entry(self, record: dict) -> tuple[str, dict]:
        """Validate one complete observation and return its cache representation."""
        try:
            pair = f"{record['from_currency']}_{record['to_currency']}"
            metadata = record.get("meta", {})
            if not isinstance(metadata, dict):
                raise ValueError("Invalid observation metadata")
            entry = {
                "rate": record["rate"],
                "updated_at": record["timestamp"],
                "source": record["source"],
                "meta": metadata.copy(),
            }
            self._validate_pair(pair, entry)
            if record["id"] != f"{pair}_{record['timestamp']}":
                raise ValueError("Invalid observation ID")
            return pair, entry
        except (AttributeError, KeyError, TypeError, ValueError):
            raise StorageError("Некорректное измерение курса") from None

    def save_observations(self, observations: list[dict], refreshed_at: str) -> int:
        """Keep newer snapshots, unique historical IDs and atomic JSON replacements."""
        with self.database.lock:
            cache = self.read_cache()
            history = self.database.read("history")
            identifiers = set()
            for record in history:
                self._observation_entry(record)
                if record["id"] in identifiers:
                    raise StorageError("В истории курсов повторяется измерение")
                identifiers.add(record["id"])
            updated_count = 0
            for record in observations:
                pair, entry = self._observation_entry(record)
                current = cache["pairs"].get(pair)
                if current is None or parse_timestamp(
                    entry["updated_at"]
                ) > parse_timestamp(current["updated_at"]):
                    cache["pairs"][pair] = entry
                    updated_count += 1
                if record["id"] not in identifiers:
                    history.append(record.copy())
                    identifiers.add(record["id"])
            if observations:
                refreshed = parse_timestamp(refreshed_at)
                current_refresh = cache.get("last_refresh")
                if current_refresh is None or refreshed > parse_timestamp(
                    current_refresh
                ):
                    cache["last_refresh"] = refreshed_at
                self.database.write_many({"rates": cache, "history": history})
            return updated_count
