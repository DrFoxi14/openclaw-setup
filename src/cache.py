import time
import threading
from collections import OrderedDict

class TTLRUCache:
    def __init__(self, capacity: int):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._cap = capacity
        self._data = OrderedDict()  # key -> (value, expire_at)
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            if key not in self._data:
                return None
            val, expire_at = self._data[key]
            if expire_at is not None and time.monotonic() >= expire_at:
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return val

    def put(self, key, value, ttl_seconds=None):
        if isinstance(key, str) and len(key) == 0:
            raise ValueError("key must be non-empty")
        if ttl_seconds is not None and float(ttl_seconds) <= 0:
            raise ValueError("ttl_seconds must be positive")

        expire_at = time.monotonic() + float(ttl_seconds) if ttl_seconds is not None else None

        with self._lock:
            if key in self._data:
                del self._data[key]
            elif len(self._data) >= self._cap:
                self._evict()

            self._data[key] = (value, expire_at)

    def _evict(self):
        now = time.monotonic()
        expired_key = None
        for k, (v, exp) in self._data.items():
            if exp is not None and now >= exp:
                expired_key = k
                break
        if expired_key is not None:
            del self._data[expired_key]
            return
        self._data.popitem(last=False)

    def cleanup(self):
        with self._lock:
            now = time.monotonic()
            expired_keys = [k for k, (v, exp) in self._data.items() if exp is not None and now >= exp]
            for k in expired_keys:
                del self._data[k]
            return len(expired_keys)
