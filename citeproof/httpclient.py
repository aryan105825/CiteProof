# @exports: HttpError(Exception)
# @exports: RateLimiter(rate_per_sec: float) with wait() -> None  (thread-safe)
# @exports: get_json(url: str, params: dict, cfg: Config) -> tuple[dict | None, int]
# @imports: config.py:Config
# @env: none
# @schema: get_json returns (parsed JSON or None on HTTP 404, number of HTTP requests actually made; cache hits count 0)
# @schema: UA "CiteProof/0.1 (mailto:<cfg.mailto>)"; retries 429/5xx/network errors up to cfg.max_retries with backoff honoring Retry-After; cache at .cache/http.sqlite only if cfg.use_cache
import json
import os
import sqlite3
import threading
import time

import requests

from citeproof.config import Config

_CACHE_DIR = ".cache"
_CACHE_PATH = os.path.join(_CACHE_DIR, "http.sqlite")
_tls = threading.local()
_limiters: dict[float, "RateLimiter"] = {}
_limiters_lock = threading.Lock()


class HttpError(Exception):
    pass


class RateLimiter:
    def __init__(self, rate_per_sec: float):
        if rate_per_sec <= 0:
            raise ValueError(f"RateLimiter needs rate_per_sec > 0, got {rate_per_sec}. Set CITEPROOF_RATE to a positive number.")
        self._interval = 1.0 / rate_per_sec
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self._interval
        delay = start - now
        if delay > 0:
            time.sleep(delay)


def _limiter(rate: float) -> RateLimiter:
    with _limiters_lock:
        lim = _limiters.get(rate)
        if lim is None:
            lim = RateLimiter(rate)
            _limiters[rate] = lim
        return lim


def _session() -> requests.Session:
    s = getattr(_tls, "session", None)
    if s is None:
        s = requests.Session()
        _tls.session = s
    return s


def _conn() -> sqlite3.Connection:
    c = getattr(_tls, "conn", None)
    if c is None:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        c = sqlite3.connect(_CACHE_PATH, timeout=30)
        c.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, body TEXT NOT NULL)")
        c.commit()
        _tls.conn = c
    return c


def _cache_get(key: str) -> str | None:
    row = _conn().execute("SELECT body FROM cache WHERE key = ?", (key,)).fetchone()
    return None if row is None else row[0]


def _cache_put(key: str, body: str) -> None:
    conn = _conn()
    conn.execute("INSERT OR REPLACE INTO cache (key, body) VALUES (?, ?)", (key, body))
    conn.commit()


def _backoff(attempt: int, retry_after: str | None) -> float:
    if retry_after:
        try:
            return min(float(retry_after), 60.0)
        except ValueError:
            pass  # Retry-After was an HTTP date, not seconds: use exponential backoff instead
    return min(2.0 ** attempt, 30.0)


def get_json(url: str, params: dict, cfg: Config) -> tuple[dict | None, int]:
    key = url + "?" + json.dumps(sorted((str(k), str(v)) for k, v in params.items()))
    if cfg.use_cache:
        cached = _cache_get(key)
        if cached is not None:
            return json.loads(cached), 0

    headers = {"User-Agent": f"CiteProof/0.1 (mailto:{cfg.mailto})", "Accept": "application/json"}
    limiter = _limiter(cfg.rate_per_sec)
    made = 0
    for attempt in range(cfg.max_retries + 1):
        limiter.wait()
        made += 1
        try:
            resp = _session().get(url, params=params, headers=headers, timeout=cfg.timeout_s)
        except requests.RequestException as e:
            if attempt < cfg.max_retries:
                time.sleep(_backoff(attempt, None))
                continue
            raise HttpError(
                f"GET {url} failed after {made} attempts: {e}. "
                "Check your network connection, or raise CITEPROOF_TIMEOUT."
            ) from e

        if resp.status_code == 404:
            if cfg.use_cache:
                _cache_put(key, "null")
            return None, made
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt < cfg.max_retries:
                time.sleep(_backoff(attempt, resp.headers.get("Retry-After")))
                continue
            raise HttpError(
                f"GET {url} returned HTTP {resp.status_code} after {made} attempts. "
                "The service is rate limiting or down: lower CITEPROOF_RATE or retry later."
            )
        if resp.status_code >= 400:
            raise HttpError(
                f"GET {url} returned HTTP {resp.status_code}: {resp.text[:200]!r}. "
                "The request was rejected; check the query parameters."
            )
        try:
            data = resp.json()
        except ValueError as e:
            raise HttpError(f"GET {url} returned a non-JSON body: {resp.text[:200]!r}. The upstream API may be misbehaving; retry later.") from e
        if cfg.use_cache:
            _cache_put(key, json.dumps(data))
        return data, made
    raise HttpError(f"GET {url} exhausted retries without a response. Retry later.")
