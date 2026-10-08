"""Record live order books for short-horizon crypto Up/Down markets. Read-only.

    python snapshot.py --minutes 345 --outdir books

Once a minute (at :05 past), for every market in SERIES whose window ends in
the next WINDOW_AHEAD minutes, read both outcome books and append one row per
outcome to books/YYYY-MM-DD.csv (UTC). Settled outcomes and fills can be
re-fetched from the API forever; these books cannot, which is why this exists.

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
FIELDS = ["snap_ts", "series", "slug", "market_id", "window_end", "min_left", "outcome_index", "outcome",
          "best_bid", "best_ask", "bid_size", "ask_size", "ask_depth_1c", "ask_depth_3c", "last_trade",
          "fee_rate", "fee_exp"]


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
            out.append(dict(series=slug, m=m,
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
    return dict(snap_ts=int(snap), series=mk["series"], slug=m["slug"], market_id=m["id"], window_end=int(mk["end"]),
                min_left=round((mk["end"] - snap) / 60, 2), outcome_index=oi, outcome=outcome,
                best_bid=bb[0], best_ask=ba[0], bid_size=bb[1], ask_size=ba[1],
                ask_depth_1c=round(depth(0.01), 2), ask_depth_3c=round(depth(0.03), 2),
                last_trade=b.get("last_trade_price"), fee_rate=fee.get("rate"), fee_exp=fee.get("exponent"))


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
        path = os.path.join(a.outdir, dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime("%Y-%m-%d") + ".csv")
        new = not os.path.exists(path)
        with open(path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if new:
                w.writeheader()
            w.writerows(rows)
        print(f"{iso(now)} {len(live)} live markets, {len(rows)} rows"
              + (f", NO ASK: {', '.join(missing[:6])}" if missing else "")
              + ("" if live else f" (nothing live; {len(markets)} known upcoming)"), flush=True)


if __name__ == "__main__":
    main()
