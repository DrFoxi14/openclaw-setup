import time
import pytest
import threading
from cache import TTLRUCache

def test_exact_ttl_expiry():
    cache = TTLRUCache(capacity=5)
    cache.put("a", 1, ttl_seconds=0.2)
    assert cache.get("a") == 1
    time.sleep(0.25)
    assert cache.get("a") is None

def test_overwrite_existing_key_refreshes_ttl():
    cache = TTLRUCache(capacity=5)
    cache.put("k", "v1", ttl_seconds=0.2)
    time.sleep(0.1)
    cache.put("k", "v2", ttl_seconds=0.3)
    time.sleep(0.15)
    assert cache.get("k") == "v2"

def test_overwrite_updates_value_and_renews_ttl():
    cache = TTLRUCache(capacity=5)
    cache.put("x", 1, ttl_seconds=0.3)
    time.sleep(0.35)
    assert cache.get("x") is None
    cache.put("x", 2, ttl_seconds=0.3)
    time.sleep(0.1)
    assert cache.get("x") == 2

def test_cleanup_returns_count_and_removes_expired():
    cache = TTLRUCache(capacity=10)
    cache.put("live_a", "a", ttl_seconds=100)
    cache.put("exp_b", "b", ttl_seconds=0.1)
    cache.put("c", "c")
    time.sleep(0.15)
    assert cache.cleanup() == 1
    assert cache.get("exp_b") is None
    assert cache.get("live_a") == "a"

def test_eviction_priority_expired_over_live_lru():
    cache = TTLRUCache(capacity=2)
    cache.put("live", "L")
    cache.put("temp", "T", ttl_seconds=0.1)
    time.sleep(0.15)
    cache.put("new_key", "N")
    assert cache.get("temp") is None
    assert cache.get("live") == "L"
    assert cache.get("new_key") == "N"

def test_lru_eviction_live_only():
    cache = TTLRUCache(capacity=2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.get("a")
    cache.put("c", 3)
    assert cache.get("b") is None
    assert cache.get("a") == 1
    assert cache.get("c") == 3

def test_invalid_ttl_raises_error():
    cache = TTLRUCache(capacity=5)
    with pytest.raises(ValueError):
        cache.put("key", "val", ttl_seconds=-1)

def test_concurrent_access():
    cache = TTLRUCache(capacity=50)
    def worker(i):
        cache.put(f"k{i}", i, ttl_seconds=1)
        cache.get(f"k{i}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert cache.get("k0") == 0

def test_mixed_ttl_and_no_ttl_eviction():
    cache = TTLRUCache(capacity=3)
    cache.put("perm1", "v1")                   # No TTL (permanent)
    cache.put("temp1", "t1", ttl_seconds=0.1)   # Will expire
    cache.put("perm2", "v2")                    # No TTL (permanent)
    time.sleep(0.15)

    # Inserting a 4th item must evict temp1 (expired), not perm1 (LRU)
    cache.put("new_item", "v3")

    assert cache.get("temp1") is None
    assert cache.get("perm1") == "v1"
    assert cache.get("perm2") == "v2"
    assert cache.get("new_item") == "v3"
