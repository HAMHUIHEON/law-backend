#utils/cache/interface.py
from typing import Any, Protocol


class CacheBackend(Protocol):
    def save_cache(self, case_id: str, filename: str, data: Any) -> str:
        ...

    def load_cache(self, case_id: str, filename: str) -> Any | None:
        ...
