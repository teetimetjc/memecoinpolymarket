"""Favourite-longshot curve: entry price vs realised win rate, after fees.

    python curve.py [--series SLUG ...] [--min-left M] [--max-left M] [--out results/x.csv]

Unit of observation
  A "bet" is one taker BUY fill placed before the window closed, in a market
  whose status is 'ok' (clean 1/0 resolution, fills reconcile to volume).
  Fills in the same market side share one outcome, so win rates and Wilson
  intervals are computed over MARKET SIDES (one 0/1 per market-outcome within
  a price bucket), not over fills. Standard errors of the edge are clustered
  by window_end, because every asset's market in the same 15-minute window
  moves with the same crypto tape.

Fees (rate/exponent from each market's own feeSchedule)
  fee_usdc = size * rate * (p * (1 - p)) ** exponent, ADDED to the USDC paid.
  Every share still redeems for $1. Verified 2026-10-08 against data-api
  /activity: usdcSize = size*price + fee_usdc to the cent, and positions and
  redemptions show the full fill size (see verify_fee.py).

Break-even win rate is the price paid grossed up for the fee, not 50%.
"""
import argparse
import csv
import math
import sqlite3
from collections import defaultdict

DB = "data/pm.sqlite"
BUCKETS = [(i / 100, (i + 5) / 100) for i in range(0, 100, 5)]


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    ph = k / n
    den = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / den
    h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / den
    return (c - h, c + h)


def fee_per_share(p, rate, expo):
    return rate * (p * (1 - p)) ** expo if rate else 0.0


def cluster_mean_se(values_by_cluster):
    """Mean of all values and its cluster-robust SE (clusters = keys)."""
    n = sum(len(v) for v in values_by_cluster.values())
    if n == 0:
        return float("nan"), float("nan"), 0
    mean = sum(sum(v) for v in values_by_cluster.values()) / n
    G = len(values_by_cluster)
    s = sum(sum(x - mean for x in v) ** 2 for v in values_by_cluster.values())
    se = math.sqrt(s * G / max(G - 1, 1)) / n
    return mean, se, G


def load(series, min_left, max_left):
    db = sqlite3.connect(DB)
    q = """SELECT m.market_id, m.series, m.window_end, m.winner, m.fee_rate, m.fee_exponent,
                  f.outcome_index, f.price, f.size, f.ts
           FROM fills f JOIN markets m USING (market_id)
           WHERE m.status = 'ok' AND f.side = 'BUY' AND f.ts < m.window_end
             AND f.ts >= m.window_end - ? AND f.ts <= m.window_end - ?"""
    args = [max_left * 60, min_left * 60]
    if series:
        q += f" AND m.series IN ({','.join('?' * len(series))})"
        args += series
    rows = db.execute(q, args).fetchall()
    counts = dict(db.execute("SELECT status, COUNT(*) FROM markets GROUP BY status").fetchall())
    return rows, counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", nargs="*")
    ap.add_argument("--min-left", type=float, default=0, help="minutes before close, lower bound")
    ap.add_argument("--max-left", type=float, default=10_000, help="minutes before close, upper bound")
    ap.add_argument("--out")
    a = ap.parse_args()

    rows, counts = load(a.series, a.min_left, a.max_left)
    print(f"markets by status: {counts}")
    print(f"taker BUY fills in scope: {len(rows):,}")
    if not rows:
        print("NOTHING TO SCORE: no fills matched the filters above.")
        return

    # bucket -> (market, outcome) -> accumulators
    sides = [defaultdict(lambda: dict(stake=0.0, shares=0.0, pnl=0.0, fee=0.0, n=0)) for _ in BUCKETS]
    meta = {}
    for mid, series, wend, winner, rate, expo, oi, p, size, ts in rows:
        b = min(int(p * 20), 19)
        won = int(oi == winner)
        fee = size * fee_per_share(p, rate, expo)
        acc = sides[b][(mid, oi)]
        acc["stake"] += size * p + fee  # total USDC paid, fee included
        acc["shares"] += size
        acc["fee"] += fee
        acc["pnl"] += size * won - (size * p + fee)
        acc["n"] += 1
        meta[(mid, oi)] = (wend, won)

    out = []
    hdr = ("bucket", "fills", "market_sides", "windows", "avg_price", "breakeven",
           "win_rate", "wilson_lo", "wilson_hi", "edge", "edge_se", "t",
           "roi_per_$", "fee_per_$", "win_$/loss_$")
    print("\n" + " ".join(f"{h:>11}" for h in hdr))
    for (lo, hi), bk in zip(BUCKETS, sides):
        if not bk:
            continue
        stake = sum(v["stake"] for v in bk.values())
        shares = sum(v["shares"] for v in bk.values())
        pnl = sum(v["pnl"] for v in bk.values())
        fee = sum(v["fee"] for v in bk.values())
        fills = sum(v["n"] for v in bk.values())
        avg_p = (stake - fee) / shares   # price paid per share, before fee
        breakeven = stake / shares        # all-in cost per $1 payout
        k = sum(meta[s][1] for s in bk)
        n = len(bk)
        wl, wh = wilson(k, n)
        # edge per market side = won - breakeven(side price), clustered by window
        by_win = defaultdict(list)
        for s, v in bk.items():
            by_win[meta[s][0]].append(meta[s][1] - v["stake"] / v["shares"])
        edge, se, G = cluster_mean_se(by_win)
        win_per_loss = (1 - breakeven) / breakeven  # $ won per $1 staked on a win; a loss costs $1
        r = (f"{lo:.2f}-{hi:.2f}", fills, n, G, avg_p, breakeven, k / n, wl, wh,
             edge, se, edge / se if se else float("nan"), pnl / stake, fee / stake, win_per_loss)
        out.append(r)
        print(" ".join(f"{x:>11}" if isinstance(x, (str, int)) else f"{x:>11.4f}" for x in r))

    tot_stake = sum(v["stake"] for bk in sides for v in bk.values())
    tot_pnl = sum(v["pnl"] for bk in sides for v in bk.values())
    print(f"\nall buckets: ROI per $ staked {tot_pnl / tot_stake:+.4f} on ${tot_stake:,.0f} staked")
    print("win_$/loss_$ = dollars won per $1 staked when the bet wins; a loss always costs the $1.")
    if a.out:
        with open(a.out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(hdr)
            w.writerows(out)
        print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
