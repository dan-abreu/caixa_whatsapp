import json
import logging
import os
import threading
from typing import Any, Optional


logger = logging.getLogger("caixa_whatsapp")
_SHARED_CACHE_LOCK = threading.Lock()
_SHARED_CACHE_BACKEND: Optional["SharedCacheBackend"] = None
_SHARED_CACHE_INITIALIZED = False
_SHARED_CACHE_PREFIX = os.getenv("SHARED_CACHE_PREFIX", "caixa_whatsapp")
_SHARED_CACHE_CONNECT_TIMEOUT_SECONDS = float(os.getenv("SHARED_CACHE_CONNECT_TIMEOUT_SECONDS", "0.2"))
_SHARED_CACHE_SOCKET_TIMEOUT_SECONDS = float(os.getenv("SHARED_CACHE_SOCKET_TIMEOUT_SECONDS", "0.2"))


class SharedCacheBackend:
    def __init__(self, client: Any, prefix: str) -> None:
        self._client = client
        self._prefix = prefix.strip() or "caixa_whatsapp"

    def _key(self, key: str) -> str:
        return f"{self._prefix}:{key}"

    def get_json(self, key: str) -> Optional[Any]:
        try:
            cached = self._client.get(self._key(key))
            if cached in (None, ""):
                return None
            return json.loads(str(cached))
        except Exception as exc:
            logger.debug("Shared cache read failed for %s: %s", key, exc)
            return None

    def set_json(self, key: str, value: Any, ttl_seconds: float) -> bool:
        if ttl_seconds <= 0:
            return False
        try:
            payload = json.dumps(value, ensure_ascii=False)
            self._client.set(self._key(key), payload, ex=max(1, int(ttl_seconds)))
            return True
        except Exception as exc:
            logger.debug("Shared cache write failed for %s: %s", key, exc)
            return False

    def delete(self, *keys: str) -> None:
        if not keys:
            return
        try:
            self._client.delete(*(self._key(key) for key in keys))
        except Exception as exc:
            logger.debug("Shared cache delete failed for %s: %s", ", ".join(keys), exc)


def get_shared_cache() -> Optional[SharedCacheBackend]:
    global _SHARED_CACHE_BACKEND, _SHARED_CACHE_INITIALIZED

    if _SHARED_CACHE_INITIALIZED:
        return _SHARED_CACHE_BACKEND

    with _SHARED_CACHE_LOCK:
        if _SHARED_CACHE_INITIALIZED:
            return _SHARED_CACHE_BACKEND

        redis_url = os.getenv("REDIS_URL") or os.getenv("CACHE_REDIS_URL") or os.getenv("SHARED_CACHE_REDIS_URL")
        if not redis_url:
            _SHARED_CACHE_INITIALIZED = True
            return None

        try:
            redis_module = __import__("redis")
            client = redis_module.Redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=_SHARED_CACHE_CONNECT_TIMEOUT_SECONDS,
                socket_timeout=_SHARED_CACHE_SOCKET_TIMEOUT_SECONDS,
            )
            client.ping()
            _SHARED_CACHE_BACKEND = SharedCacheBackend(client, _SHARED_CACHE_PREFIX)
            logger.info("Shared Redis cache enabled.")
        except Exception as exc:
            logger.warning("Shared Redis cache unavailable, using local cache only: %s", exc)
            _SHARED_CACHE_BACKEND = None

        _SHARED_CACHE_INITIALIZED = True
        return _SHARED_CACHE_BACKEND