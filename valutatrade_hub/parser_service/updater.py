"""Collect multiple providers while keeping partial failures explicit."""

import logging
from dataclasses import dataclass

from ..core.exceptions import ApiRequestError
from ..core.utils import iso_timestamp
from .api_clients import CoinGeckoClient, ExchangeRateApiClient
from .storage import RatesStorage

logger = logging.getLogger("valutatrade.parser")


@dataclass(frozen=True)
class UpdateResult:
    """Distinguish saved observations from provider errors and previous cache data."""

    updated_count: int
    errors: dict[str, str]
    last_refresh: str | None


class RatesUpdater:
    """Orchestrate provider calls, observations, logging and cache persistence."""

    def __init__(self, clients=None, storage: RatesStorage | None = None):
        self.clients = (
            [CoinGeckoClient(), ExchangeRateApiClient()]
            if clients is None
            else list(clients)
        )
        self.storage = storage or RatesStorage()

    def run_update(self, source: str | None = None) -> UpdateResult:
        """Update all/one sources; failure of one never discards the other source."""
        selected = [
            client
            for client in self.clients
            if source is None or client.source_key == source
        ]
        if not selected:
            raise ValueError("Неизвестный источник курсов")
        logger.info("Starting rates update...")
        observations = []
        errors = {}
        for client in selected:
            try:
                rates = client.fetch_rates()
                if not rates:
                    raise ApiRequestError(
                        f"{client.source_name}: источник не вернул курсы"
                    )
            except ApiRequestError as error:
                errors[client.source_key] = str(error)
                logger.error("Failed to fetch from %s: %s", client.source_name, error)
                continue
            observed_at = iso_timestamp()
            for pair, rate in rates.items():
                from_code, to_code = pair.split("_")
                observations.append(
                    {
                        "id": f"{pair}_{observed_at}",
                        "from_currency": from_code,
                        "to_currency": to_code,
                        "rate": rate,
                        "timestamp": observed_at,
                        "source": client.source_name,
                        "meta": client.last_metadata.get(pair, {}).copy(),
                    }
                )
            logger.info(
                "Fetching from %s... OK (%s rates)", client.source_name, len(rates)
            )
        if observations:
            updated_count = self.storage.save_observations(
                observations, iso_timestamp()
            )
        else:
            updated_count = 0
        cache = self.storage.read_cache()
        logger.info(
            "Update finished: %s rates saved, %s source errors",
            updated_count,
            len(errors),
        )
        return UpdateResult(updated_count, errors, cache.get("last_refresh"))
