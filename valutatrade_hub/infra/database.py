"""Singleton JSON persistence with atomic replacement and shared-process locking."""

import json
import os
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from threading import RLock

from ..core.exceptions import StorageError
from .settings import SettingsLoader

EMPTY_DOCUMENTS = {
    "users": [],
    "portfolios": [],
    "rates": {"pairs": {}, "last_refresh": None},
    "history": [],
}


class DatabaseManager:
    """Keep one JSON access point shared by Core and Parser services."""

    _instance = None

    def __new__(cls, *args, **kwargs):
        # A direct __new__ Singleton avoids metaclass machinery and extra instances.
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, settings: SettingsLoader | None = None):
        if self._initialized:
            return
        self.settings = settings or SettingsLoader()
        self._thread_lock = RLock()
        self._process_locked = False
        self._initialized = True

    @property
    def lock(self):
        """Create a reentrant guard shared by independent Core/Parser processes."""
        return self._guard()

    @staticmethod
    def _process_lock(handle, *, release: bool = False) -> None:
        """Use OS file locks, released automatically when a process exits."""
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            mode = msvcrt.LK_UNLCK if release else msvcrt.LK_LOCK
            msvcrt.locking(handle.fileno(), mode, 1)
        else:
            import fcntl

            mode = fcntl.LOCK_UN if release else fcntl.LOCK_EX
            fcntl.flock(handle.fileno(), mode)

    @contextmanager
    def _guard(self):
        """Hold one OS lock for an entire nested read/modify/write operation."""
        with self._thread_lock:
            if self._process_locked:
                yield self
                return
            lock_path = self.settings.get_path("data_dir") / ".valutatrade.lock"
            try:
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                handle = lock_path.open("a+b")
            except OSError:
                raise StorageError("Не удалось открыть блокировку хранилища") from None
            with handle:
                try:
                    self._process_lock(handle)
                except OSError:
                    raise StorageError(
                        "Хранилище занято другим процессом. Повторите операцию позже."
                    ) from None
                self._process_locked = True
                try:
                    if os.fstat(handle.fileno()).st_size == 0:
                        handle.write(b"0")
                        handle.flush()
                    yield self
                finally:
                    self._process_locked = False
                    self._process_lock(handle, release=True)

    def path(self, collection: str) -> Path:
        """Resolve only application-defined collections, not arbitrary file paths."""
        if collection not in EMPTY_DOCUMENTS:
            raise ValueError("Неизвестное хранилище данных")
        return self.settings.get_path(f"{collection}_file")

    @staticmethod
    def _validate_document(collection: str, data) -> None:
        """Reject incompatible JSON shapes before replacing a saved document."""
        if collection == "rates":
            valid = isinstance(data, dict) and isinstance(data.get("pairs"), dict)
        else:
            valid = isinstance(data, list) and all(
                isinstance(row, dict) for row in data
            )
        if not valid:
            raise StorageError(f"Некорректная структура данных: {collection}")

    def read(self, collection: str):
        """Read a JSON document, distinguishing absence from damaged content."""
        path = self.path(collection)
        with self.lock:
            try:
                with path.open(encoding="utf-8") as source:
                    data = json.load(source)
            except FileNotFoundError:
                return deepcopy(EMPTY_DOCUMENTS[collection])
            except (OSError, ValueError):
                raise StorageError(
                    f"Не удалось прочитать данные: {collection}"
                ) from None
            self._validate_document(collection, data)
            return data

    @staticmethod
    def _stage(path: Path, content: bytes) -> Path:
        """Flush a complete replacement to a unique temporary file beside its target."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=path.name + ".", suffix=".tmp", dir=path.parent, delete=False
            ) as destination:
                temporary = Path(destination.name)
                destination.write(content)
                destination.flush()
                os.fsync(destination.fileno())
            return temporary
        except OSError:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise

    def write(self, collection: str, data) -> None:
        """Atomically replace one validated JSON document."""
        self.write_many({collection: data})

    def write_many(self, documents: dict) -> None:
        """Stage replacements and roll back earlier files if a later rename fails."""
        staged = {}
        previous = {}
        replaced = []
        with self.lock:
            try:
                for collection, data in documents.items():
                    path = self.path(collection)
                    self._validate_document(collection, data)
                    content = (
                        json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2)
                        + "\n"
                    ).encode("utf-8")
                    previous[collection] = path.read_bytes() if path.exists() else None
                    staged[collection] = self._stage(path, content)
                for collection, temporary in staged.items():
                    os.replace(temporary, self.path(collection))
                    replaced.append(collection)
            except (OSError, ValueError):
                for collection in reversed(replaced):
                    path = self.path(collection)
                    if previous[collection] is None:
                        path.unlink(missing_ok=True)
                    else:
                        restore = self._stage(path, previous[collection])
                        try:
                            os.replace(restore, path)
                        finally:
                            restore.unlink(missing_ok=True)
                raise StorageError(
                    "Не удалось сохранить данные; операция отменена"
                ) from None
            finally:
                for temporary in staged.values():
                    temporary.unlink(missing_ok=True)
