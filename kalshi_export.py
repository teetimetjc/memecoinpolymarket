"""Read-only export of the Kalshi side for the head-to-head (specs/headtohead.md).

Runs in GitHub Actions (kalshi_export.yml); the research container cannot reach Kalshi.

    python kalshi_export.py --out kalshi --start 2026-04-01 --end 2026-10-07

1. Reads tabs M15H and M15 of the Kalshi sheet with a READ-ONLY token
   (scope spreadsheets.readonly), keeps the five majors and the T-9 columns.
2. For every one of those markets closing in [start, end], fetches Kalshi's public
   trades in [T-9, T-8), widening to [T-8, close) only for a missing taker side
   (no credentials), falling back to the historical endpoint when the live one is empty.
Writes kalshi/quotes.csv.gz and kalshi/trades.csv.gz. Writes nothing anywhere else.
"""
import argparse
import calendar
import csv
import gzip
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from google.auth.transport.requests import Request
from google.oauth2 import service_account

KALSHI_SHEET = "1PjtaTxSW1AKZ4rAUeIoHSfrV8Imh6WV_XM9uErXunQc"  # "Meme Coin - Kalshi", read only
TABS = ("M15H", "M15")
MAJORS = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M")
KEEP = ("Ticker", "Series", "Close Time", "bid9", "ask9", "bidsz9", "asksz9", "Result")
HOST = "https://api.elections.kalshi.com/trade-api/v2"


def ts(s):  # same parser as the Kalshi repo's score.py
    try:
        return calendar.timegm(time.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


def read_tabs():
    info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    creds.refresh(Request())
    out = []
    for tab in TABS:
        # Quote the tab name: unquoted, "M15" is read as the single CELL M15.
        rng = requests.utils.quote(f"'{tab}'", safe="")
        r = requests.get(f"https://sheets.googleapis.com/v4/spreadsheets/{KALSHI_SHEET}/values/{rng}",
                         headers={"Authorization": f"Bearer {creds.token}"}, timeout=120)
        r.raise_for_status()
        rows = r.json().get("values", [])
        h = {k: i for i, k in enumerate(rows[0])}
        missing = [k for k in KEEP if k not in h]
        if missing:
            raise SystemExit(f"tab {tab}: columns missing {missing}")
        n = 0
        for row in rows[1:]:
            get = lambda k: row[h[k]] if h[k] < len(row) else ""
            if get("Series") in MAJORS:
                out.append([tab] + [get(k) for k in KEEP])
                n += 1
        print(f"{tab}: {len(rows) - 1} rows, {n} in the five majors", flush=True)
    return out


S = requests.Session()


_reported = []


def kget(path, params, tries=4):
    delay, err = 1.0, None
    for _ in range(tries):
        try:
            r = S.get(HOST + path, params=params, timeout=20)
            if r.status_code == 200:
                return r.json()
            err = f"HTTP {r.status_code}: {r.text[:120]}"
            if r.status_code not in (429, 500, 502, 503, 504):
                break
        except requests.RequestException as e:
            err = repr(e)[:120]
        time.sleep(delay)
        delay *= 2
    if len(_reported) < 5:  # say why, immediately, the first few times
        _reported.append(err)
        print(f"  kalshi {path} {params.get('ticker')}: {err}", flush=True)
    return {"_error": err or "gave up"}


def _fetch(path, ticker, t0, t1):
    out, cursor = [], None
    while True:
        p = dict(ticker=ticker, min_ts=t0, max_ts=t1, limit=1000)
        if cursor:
            p["cursor"] = cursor
        d = kget(path, p)
        if "_error" in d:
            return None, d["_error"]
        out += d.get("trades", [])
        cursor = d.get("cursor")
        if not cursor:
            return out, None


def trades_for(ticker, close):
    """Trades in [T-9, T-8); widened to [T-8, close) only if a taker side has none in the first minute.
    Live endpoint first, the historical one if the live one returns nothing."""
    for path in ("/markets/trades", "/historical/trades"):
        first, err = _fetch(path, ticker, close - 540, close - 480)
        if first is None:
            continue
        sides = {str(t.get("taker_side")).lower() for t in first}
        out = list(first)
        if not {"yes", "no"} <= sides:
            more, err = _fetch(path, ticker, close - 480, close)
            out += more or []
        if out:
            return path, out, None
    return None, [], err


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="kalshi")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--max-markets", type=int, default=0, help="smoke test: stop after N markets (0 = all)")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    rows = read_tabs()
    with gzip.open(f"{a.out}/quotes.csv.gz", "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(("tab",) + KEEP)
        w.writerows(rows)

    t0 = ts(a.start + "T00:00:00")
    t1 = ts(a.end + "T23:59:59")
    markets = {}
    for r in rows:
        c = ts(r[3])
        if c is not None and t0 <= c <= t1:
            markets[r[1]] = c
    if a.max_markets:
        markets = dict(sorted(markets.items(), key=lambda kv: kv[1])[: a.max_markets])
    print(f"markets to fetch trades for: {len(markets)}", flush=True)

    stats = {"live": 0, "historical": 0, "none": 0}
    errors = []
    sample_printed = False
    with gzip.open(f"{a.out}/trades.csv.gz", "wt", newline="") as f, ThreadPoolExecutor(6) as pool:
        w = csv.writer(f)
        w.writerow(("ticker", "close_ts", "source", "created_time", "taker_side",
                    "yes_price", "no_price", "count"))
        items = sorted(markets.items(), key=lambda kv: kv[1])
        for i, (res, (tk, c)) in enumerate(zip(pool.map(lambda kv: trades_for(*kv), items), items)):
            path, trades, err = res
            if not trades:
                stats["none"] += 1
                if err:
                    errors.append(f"{tk}: {err}")
            else:
                stats["live" if path == "/markets/trades" else "historical"] += 1
                if not sample_printed:
                    print("sample trade:", json.dumps(trades[0]), flush=True)
                    sample_printed = True
            for t in trades:
                w.writerow((tk, c, path, t.get("created_time"), t.get("taker_side"),
                            t.get("yes_price_dollars", t.get("yes_price")),
                            t.get("no_price_dollars", t.get("no_price")),
                            t.get("count_fp", t.get("count"))))
            if i % 500 == 0:
                print(f"  {i}/{len(items)} markets, {stats}", flush=True)
    print(f"done: {stats}; errors {len(errors)}", flush=True)
    for e in errors[:10]:
        print("  ", e)


if __name__ == "__main__":
    main()
