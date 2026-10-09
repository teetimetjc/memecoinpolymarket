"""Record live order books for short-horizon crypto Up/Down markets. Read-only.

    python snapshot.py --minutes 345 --outdir books

Once a minute (at :05 past), for every market in SERIES whose window ends in
the next WINDOW_AHEAD minutes, read both outcome books and append one row per
outcome to books/YYYY-MM-DD.csv (UTC). Settled outcomes and fills can be
re-fetched from the API forever; these books cannot, which is why this exists.

Kalshi's matching 15-minute markets (KXBTC15M etc.) are logged in the same rows,
same minute, so the two venues can be compared window by window (venue column).
Kalshi data is public and read without credentials; a Kalshi failure is printed
and never stops the Polymarket recording. Both venues' target prices are logged
(Polymarket priceToBeat, Kalshi floor_strike) because the two are only the same
bet if the targets match. Polymarket only publishes priceToBeat after the window
closes, so it is blank live and is re-fetched from the closed event afterwards.

Every minute prints one line, including when it recorded nothing and why.
"""
import argparse
import csv
import datetime as dt
import json
import os
import time

from pm import api

SERIES = [f"{a}-up-or-down-{h}" for h in ("5m", "15m") for a in ("btc", "eth", "sol", "xrp", "doge", "bnb", "hype")]
WINDOW_AHEAD = 16  # minutes; covers a whole 15m window
FIELDS = ["snap_ts", "venue", "series", "slug", "market_id", "window_end", "min_left", "outcome_index", "outcome",
          "best_bid", "best_ask", "bid_size", "ask_size", "ask_depth_1c", "ask_depth_3c", "last_trade",
          "fee_rate", "fee_exp", "strike", "pair"]
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
# Kalshi series -> the Polymarket series it is paired with, and the shared asset key
KALSHI_SERIES = {"KXBTC15M": "btc", "KXETH15M": "eth", "KXSOL15M": "sol", "KXXRP15M": "xrp", "KXDOGE15M": "doge"}


def iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def upcoming(now, ids):
    """Markets in SERIES ending within the next WINDOW_AHEAD minutes (+ a margin)."""
    out = []
    for slug in SERIES:
        evs = api.get(f"{api.GAMMA}/events", dict(series_id=ids[slug], closed="false", limit=100,
                                                   end_date_min=iso(now), end_date_max=iso(now + 30 * 60)))
        for e in evs:
            m = e["markets"][0]
            out.append(dict(series=slug, m=m, meta=e.get("eventMetadata") or {},
                            end=dt.datetime.fromisoformat(m["endDate"].replace("Z", "+00:00")).timestamp()))
    return out


def book_row(snap, mk, oi, outcome, token):
    b = api.book(token)
    bids = [(float(o["price"]), float(o["size"])) for o in b.get("bids", [])]
    asks = [(float(o["price"]), float(o["size"])) for o in b.get("asks", [])]
    bb = max(bids) if bids else (None, None)  # books are listed worst-first
    ba = min(asks) if asks else (None, None)
    depth = lambda w: sum(s for p, s in asks if ba[0] is not None and p <= ba[0] + w + 1e-9)
    m = mk["m"]
    fee = m.get("feeSchedule") or {}
    asset = mk["series"].split("-")[0]
    return dict(snap_ts=int(snap), venue="polymarket", series=mk["series"], slug=m["slug"], market_id=m["id"],
                window_end=int(mk["end"]), strike=mk["meta"].get("priceToBeat"),
                pair=f"{asset}:{int(mk['end'])}" if mk["series"].endswith("15m") else "",
                min_left=round((mk["end"] - snap) / 60, 2), outcome_index=oi, outcome=outcome,
                best_bid=bb[0], best_ask=ba[0], bid_size=bb[1], ask_size=ba[1],
                ask_depth_1c=round(depth(0.01), 2), ask_depth_3c=round(depth(0.03), 2),
                last_trade=b.get("last_trade_price"), fee_rate=fee.get("rate"), fee_exp=fee.get("exponent"))


def _kf(v):
    try:
        x = float(v)
        return x / 100 if x > 1 else x
    except (TypeError, ValueError):
        return None


def kalshi_rows(snap):
    """Both sides of every open Kalshi 15m market closing within WINDOW_AHEAD minutes.

    Kalshi runs one book: NO ask = 1 - YES bid, NO bid = 1 - YES ask. Returns (rows, problems)."""
    rows, problems = [], []
    for series, asset in KALSHI_SERIES.items():
        try:
            r = api._S.get(f"{KALSHI}/markets", params=dict(series_ticker=series, status="open", limit=50), timeout=20)
            if r.status_code != 200:
                problems.append(f"{series} HTTP {r.status_code}")
                continue
            ms = r.json().get("markets") or []
        except Exception as e:
            problems.append(f"{series} {e!r}"[:80])
            continue
        n = 0
        for m in ms:
            try:
                end = dt.datetime.fromisoformat(str(m["close_time"]).replace("Z", "+00:00")).timestamp()
            except (KeyError, ValueError):
                continue
            if not (0 < end - snap <= WINDOW_AHEAD * 60):
                continue
            yb, ya = _kf(m.get("yes_bid_dollars", m.get("yes_bid"))), _kf(m.get("yes_ask_dollars", m.get("yes_ask")))
            if yb is None and ya is None:
                problems.append(f"{m.get('ticker')} NO QUOTE")
            base = dict(snap_ts=int(snap), venue="kalshi", series=series, slug=m.get("ticker"), market_id=m.get("ticker"),
                        window_end=int(end), min_left=round((end - snap) / 60, 2), ask_depth_1c=None, ask_depth_3c=None,
                        last_trade=_kf(m.get("last_price_dollars", m.get("last_price"))), fee_rate=0.07, fee_exp=1,
                        strike=m.get("floor_strike"), pair=f"{asset}:{int(end)}", bid_size=None, ask_size=None)
            rows.append(dict(base, outcome_index=0, outcome="Yes", best_bid=yb, best_ask=ya))
            rows.append(dict(base, outcome_index=1, outcome="No",
                             best_bid=None if ya is None else round(1 - ya, 4),
                             best_ask=None if yb is None else round(1 - yb, 4)))
            n += 1
        if n == 0:
            problems.append(f"{series}: no market closing within {WINDOW_AHEAD} min")
    return rows, problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=345)
    ap.add_argument("--outdir", default="books")
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)

    ids = {s: api.series_by_slug(s)["id"] for s in SERIES}
    stop = time.time() + a.minutes * 60
    markets, refreshed = [], 0
    while time.time() < stop:
        time.sleep((65 - time.time() % 60) % 60 or 60)  # wake at :05 past each minute
        now = time.time()
        try:
            if now - refreshed > 300:
                markets, refreshed = upcoming(now, ids), now
            live = [mk for mk in markets if 0 < mk["end"] - now <= WINDOW_AHEAD * 60 and mk["m"].get("acceptingOrders")]
            rows, missing = [], []
            for mk in live:
                for oi, (outcome, tok) in enumerate(zip(json.loads(mk["m"]["outcomes"]),
                                                        json.loads(mk["m"]["clobTokenIds"]))):
                    r = book_row(now, mk, oi, outcome, tok)
                    rows.append(r)
                    if r["best_ask"] is None:
                        missing.append(f"{mk['m']['slug']}:{outcome}")
        except Exception as e:  # one bad minute must not end the session; say why
            print(f"{iso(now)} ERROR {e!r}", flush=True)
            continue
        krows, kprob = kalshi_rows(now)
        rows += krows
        path = os.path.join(a.outdir, dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime("%Y-%m-%d") + ".csv")
        new = not os.path.exists(path)
        with open(path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if new:
                w.writeheader()
            w.writerows(rows)
        print(f"{iso(now)} {len(live)} live markets, {len(rows)} rows ({len(krows)} Kalshi)"
              + (f", KALSHI: {'; '.join(kprob[:3])}" if kprob else "")
              + (f", NO ASK: {', '.join(missing[:6])}" if missing else "")
              + ("" if live else f" (nothing live; {len(markets)} known upcoming)"), flush=True)


if __name__ == "__main__":
    main()
