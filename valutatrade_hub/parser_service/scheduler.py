"""Optional explicitly started updates; importing this module starts nothing."""

from threading import Event

from ..core.utils import validate_number
from .updater import RatesUpdater


def run_periodically(
    updater: RatesUpdater, interval_seconds: float, stop_event: Event | None = None
) -> None:
    """Update until stopped, without creating a background thread or busy loop."""
    interval = validate_number(interval_seconds, name="interval_seconds")
    stop = stop_event or Event()
    while not stop.is_set():
        updater.run_update()
        if stop.wait(interval):
            return
