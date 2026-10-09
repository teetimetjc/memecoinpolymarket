"""Summarise one session's book snapshots and append to the Sheet.

    python book_summary.py books/2026-10-08.csv --since UNIX_TS [--sheet]

One row per (series, minutes-left bucket):
  both_cost   median of (ask_up + ask_down - 1 + fees) / (ask_up + ask_down):
              pure trading cost of buying both sides, per $ staked
  fav_cost    median of (ask - mid + fee) / ask on the favourite side:
              what one taker bet on the favourite gives up versus the mid
  fav_depth1c median shares offered within 1c of the favourite's best ask
Snapshots where either side has no ask are counted in no_quote, not dropped.
"""
import argparse
import csv
import statistics
import time
from collections import defaultdict

BUCKETS = [(0, 1), (1, 3), (3, 6), (6, 10), (10, 16)]
HEADER = ["session_end_utc", "series", "min_left", "snapshots", "no_quote",
          "both_cost_med", "fav_cost_med", "fav_price_med", "fav_depth1c_med"]


def fee(p, rate, expo):
    return float(rate or 0) * (p * (1 - p)) ** float(expo or 1)


def summarise(path, since):
    pairs = defaultdict(dict)
    with open(path) as f:
        for r in csv.DictReader(f):
            if int(r["snap_ts"]) >= since and r.get("venue", "polymarket") == "polymarket":
                pairs[(r["snap_ts"], r["market_id"])][int(r["outcome_index"])] = r
    agg = defaultdict(lambda: dict(n=0, nq=0, both=[], fav=[], favp=[], depth=[]))
    for (_, _), sides in pairs.items():
        if len(sides) != 2:
            continue
        up, dn = sides[0], sides[1]
        ml = float(up["min_left"])
        b = next((f"{lo}-{hi}" for lo, hi in BUCKETS if lo <= ml < hi), None)
        if b is None:
            continue
        a = agg[(up["series"], b)]
        a["n"] += 1
        if not up["best_ask"] or not dn["best_ask"]:
            a["nq"] += 1
            continue
        au, ad = float(up["best_ask"]), float(dn["best_ask"])
        rate, expo = up["fee_rate"], up["fee_exp"]
        a["both"].append((au + ad - 1 + fee(au, rate, expo) + fee(ad, rate, expo)) / (au + ad))
        fav = up if au >= ad else dn
        p = float(fav["best_ask"])
        if fav["best_bid"]:
            mid = (p + float(fav["best_bid"])) / 2
            a["fav"].append((p - mid + fee(p, rate, expo)) / p)
        a["favp"].append(p)
        a["depth"].append(float(fav["ask_depth_1c"]))
    med = lambda xs: round(statistics.median(xs), 5) if xs else ""
    end = time.strftime("%Y-%m-%d %H:%M", time.gmtime())
    return [[end, s, b, a["n"], a["nq"], med(a["both"]), med(a["fav"]), med(a["favp"]), med(a["depth"])]
            for (s, b), a in sorted(agg.items())]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--since", type=int, default=0)
    ap.add_argument("--sheet", action="store_true")
    a = ap.parse_args()
    rows = [r for p in a.files for r in summarise(p, a.since)]
    for r in rows:
        print(r)
    if not rows:
        print("NO SUMMARY ROWS: no complete snapshot pairs since", a.since)
    if a.sheet:
        from pm import sheet
        sheet.append("book_summary", HEADER, rows)


if __name__ == "__main__":
    main()
