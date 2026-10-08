"""Round-trip cost on live crypto Up/Down books, read-only.

    python roundtrip.py [--within-min 60] [--out results/roundtrip_YYYYmmddTHHMM.csv]

For every market in the short-horizon series whose window ends within
--within-min minutes, read both outcome books and record:

  both_sides_cost  ask_up + ask_down - 1 + fees, per $ staked. Buying both
                   sides guarantees exactly $1 at settlement, so this is the
                   pure cost of trading with no view at all (Kalshi: ~25%).
  one_side_cost    (ask - mid) / ask + fee / stake for the cheaper-to-reach
                   side: what one taker bet gives up versus the midpoint.

Books list bids worst-first and asks worst-first; best prices are taken with
max/min, never by position.
"""
import argparse
import csv
import datetime as dt
import json
import time

from pm import api

SERIES = [f"{a}-up-or-down-{h}" for h in ("5m", "15m", "hourly", "4h")
          for a in ("btc", "eth", "sol", "xrp", "doge", "bnb", "hype")]
SERIES = [s.replace("sol-up-or-down-hourly", "solana-up-or-down-hourly") for s in SERIES]


def best(book):
    bids = [float(o["price"]) for o in book.get("bids", [])]
    asks = [float(o["price"]) for o in book.get("asks", [])]
    bb = max(bids) if bids else None
    ba = min(asks) if asks else None
    return bb, ba


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--within-min", type=float, default=60)
    ap.add_argument("--out")
    a = ap.parse_args()

    now = time.time()
    lo = dt.datetime.fromtimestamp(now, dt.timezone.utc)
    hi = dt.datetime.fromtimestamp(now + a.within_min * 60, dt.timezone.utc)
    rows, skipped = [], []
    for slug in SERIES:
        try:
            sid = api.series_by_slug(slug)["id"]
        except LookupError as e:
            skipped.append((slug, str(e)))
            continue
        evs = api.get(f"{api.GAMMA}/events", dict(
            series_id=sid, closed="false", limit=100,
            end_date_min=lo.isoformat().replace("+00:00", "Z"),
            end_date_max=hi.isoformat().replace("+00:00", "Z")))
        if not evs:
            skipped.append((slug, "no market ending in range"))
        for e in evs:
            m = e["markets"][0]
            if not m.get("acceptingOrders"):
                skipped.append((m["slug"], "not accepting orders"))
                continue
            tok = json.loads(m["clobTokenIds"])
            fee = m.get("feeSchedule") or {}
            rate, expo = fee.get("rate") or 0.0, fee.get("exponent") or 1
            (bu, au), (bd, ad) = best(api.book(tok[0])), best(api.book(tok[1]))
            if au is None or ad is None:
                skipped.append((m["slug"], f"NO QUOTE up_ask={au} down_ask={ad}"))
                continue
            end = dt.datetime.fromisoformat(m["endDate"].replace("Z", "+00:00")).timestamp()
            fee_u = rate * (au * (1 - au)) ** expo  # USDC per share
            fee_d = rate * (ad * (1 - ad)) ** expo
            stake2 = au + ad
            both = (stake2 + fee_u + fee_d - 1) / stake2
            # one side: whichever side is the favourite (the Kalshi live slice buys favourites)
            p, bid, f = (au, bu, fee_u) if au >= ad else (ad, bd, fee_d)
            mid = (p + bid) / 2 if bid is not None else None
            one = ((p - mid) / p + f / p) if mid is not None else None
            rows.append(dict(
                series=slug, slug=m["slug"], min_left=round((end - now) / 60, 2),
                up_bid=bu, up_ask=au, down_bid=bd, down_ask=ad,
                fav_price=p, both_sides_cost=round(both, 5),
                one_side_cost=None if one is None else round(one, 5),
                fee_rate=rate, fee_exp=expo))

    print(f"{'series':28} {'min_left':>8} {'up b/a':>11} {'dn b/a':>11} {'both%':>7} {'fav':>5} {'one%':>6}")
    for r in sorted(rows, key=lambda r: (r["series"], r["min_left"])):
        print(f"{r['series']:28} {r['min_left']:8.1f} {r['up_bid']}/{r['up_ask']:<5} {r['down_bid']}/{r['down_ask']:<5} "
              f"{100 * r['both_sides_cost']:7.2f} {r['fav_price']:5.2f} "
              f"{'' if r['one_side_cost'] is None else f'{100 * r['one_side_cost']:6.2f}'}")
    for s, why in skipped:
        print(f"skipped {s}: {why}")
    if a.out and rows:
        with open(a.out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
