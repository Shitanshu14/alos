"""
Week 15 — API performance optimization: a small in-process TTL cache.

This is deliberately simple - a dict with expiry timestamps, no external
dependency. It's the right amount of caching for a single-instance
deployment (e.g. one Render/Railway web service). If ALOS ever runs
multiple instances behind a load balancer, replace this with Redis (the
`cached` decorator's call sites wouldn't need to change, only this
file's internals).

Usage:
    from ..cache import cached

    @cached(ttl_seconds=30)
    def expensive_query(arg1, arg2):
        ...
"""
import functools
import time
import threading

_store = {}
_lock = threading.Lock()


def cached(ttl_seconds: int = 30):
    """Caches a function's return value per distinct set of arguments for
    ttl_seconds. Only use on functions whose arguments are hashable and
    whose result doesn't need to be instantly fresh (e.g. read-heavy list
    endpoints where a few seconds of staleness is fine)."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            key = (func.__module__, func.__qualname__, args, tuple(sorted(kwargs.items())))
            now = time.monotonic()

            with _lock:
                cached_entry = _store.get(key)
                if cached_entry and cached_entry[1] > now:
                    return cached_entry[0]

            result = func(*args, **kwargs)

            with _lock:
                _store[key] = (result, now + ttl_seconds)

            return result
        return wrapper
    return decorator


def clear_cache():
    """Mainly for tests - wipes every cached entry."""
    with _lock:
        _store.clear()
