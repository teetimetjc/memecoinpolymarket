"""Read-only HTTP access to Polymarket's public APIs.

No keys, no wallet, no order endpoints. Every failure is raised or logged,
never swallowed: a fetch that returns nothing says why.
"""
import time

import requests

GAMMA = "https://gamma-api.polymarket.com"
DATA = "https://data-api.polymarket.com"
CLOB = "https://clob.polymarket.com"

_S = requests.Session()
# Cloudflare rejects the default python-requests/urllib user agents with 403.
_S.headers["User-Agent"] = "pm-research/0.1 (read-only)"


def get(url, params=None, tries=6):
    delay = 2.0
    for attempt in range(tries):
        try:
            r = _S.get(url, params=params, timeout=30)
        except requests.RequestException as e:
            err = f"network error {e!r}"
        else:
            if r.status_code == 200:
                return r.json()
            if r.status_code not in (429, 500, 502, 503, 504):
                raise RuntimeError(f"HTTP {r.status_code} {url} {params}: {r.text[:200]}")
            err = f"HTTP {r.status_code}"
        print(f"  retry {attempt + 1}/{tries} after {err}: {url} {params}", flush=True)
        time.sleep(delay)
        delay *= 2
    raise RuntimeError(f"gave up after {tries} tries: {url} {params}")


_series = {}


def series_by_slug(slug):
    if not _series:
        off = 0
        while True:
            page = get(f"{GAMMA}/series", dict(limit=100, offset=off))
            if not page:
                break
            for s in page:
                _series[s.get("slug")] = s
            off += len(page)
    if slug not in _series:
        raise LookupError(f"series {slug!r} not found among {len(_series)} series")
    return _series[slug]


def closed_events(series_id, day):
    """All closed events in a series whose window ends on `day` (YYYY-MM-DD, UTC)."""
    out, off = [], 0
    while True:
        page = get(f"{GAMMA}/events", dict(
            series_id=series_id, closed="true", limit=100, offset=off,
            end_date_min=f"{day}T00:00:00Z", end_date_max=f"{day}T23:59:59Z"))
        out += page
        if len(page) < 100:
            return out
        off += len(page)


def taker_trades(condition_id):
    """Every taker fill in a market, as reported by the data API."""
    out, off = [], 0
    while True:
        page = get(f"{DATA}/trades", dict(market=condition_id, limit=500, offset=off, takerOnly="true"))
        out += page
        if len(page) < 500:
            return out
        off += len(page)


def book(token_id):
    return get(f"{CLOB}/book", dict(token_id=token_id))
