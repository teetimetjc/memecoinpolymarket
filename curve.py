"""Favourite-longshot curve: entry price vs realised win rate, after fees.

    python curve.py [--series SLUG ...] [--method snapshot|fills] [--at 9] [--outcome-index 0|1] [--out CSV]

Two ways to define a bet (both score each bet at ITS OWN fill price):

  snapshot (default)  One bet per market side: the first taker BUY fill at or
                      after T-`at` minutes. This is a stopping time, so in a
                      fair market E[win | price] = price exactly; it mirrors the
                      Kalshi "buy at T-9" rule. Wilson intervals are over these
                      bets (window clustering still applies, see below).
  fills               Every taker BUY fill before the close is a bet, weighted
                      by size. Says what real takers earned. Fills are not
                      independent, so no Wilson interval is shown; use the
                      clustered t.

Do NOT average a market side's fills before scoring: winners keep trading up
toward $1 inside a bucket and losers leave it, which biases edge negative by
several points even in a perfectly fair market. (An earlier version did this.)

Fees: fee_usdc = size * rate * (p(1-p))**exponent, ADDED to the USDC paid; every
share redeems for $1. Verified against data-api /activity (verify_fee.py).
Break-even win rate = all-in cost per share, not 50%.

Standard errors are clustered by window_end: every asset's market in the same
window moves with one crypto tape, and Up/Down of one market share an outcome.
"""
import argparse
import csv
import math
import sqlite3
from collections import defaultdict

DB = "data/pm.sqlite"
NB = 20  # 5c buckets


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    ph = k / n
    den = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / den
    h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / den
    return (c - h, c + h)


def cluster_mean_se(by_cluster):
    """Mean over all values and its cluster-robust SE."""
    n = sum(len(v) for v in by_cluster.values())
    mean = sum(sum(v) for v in by_cluster.values()) / n
    G = len(by_cluster)
    s = sum(sum(x - mean for x in v) ** 2 for v in by_cluster.values())
    return mean, math.sqrt(s * G / max(G - 1, 1)) / n, G


def bets(a):
    db = sqlite3.connect(DB)
    q = """SELECT m.market_id, m.window_end, m.winner, m.fee_rate, m.fee_exponent,
                  f.outcome_index, f.price, f.size, f.ts
           FROM fills f JOIN markets m USING (market_id)
           WHERE m.status = 'ok' AND f.side = 'BUY' AND f.ts < m.window_end"""
    args = []
    if a.outcome_index is not None:
        q += " AND f.outcome_index = ?"
        args.append(a.outcome_index)
    if a.series:
        q += f" AND m.series IN ({','.join('?' * len(a.series))})"
        args += a.series
    if a.method == "snapshot":
        q += " AND f.ts >= m.window_end - ?"
        args.append(int(a.at * 60))
    q += " ORDER BY f.market_id, f.outcome_index, f.ts"
    counts = dict(db.execute("SELECT status, COUNT(*) FROM markets GROUP BY status").fetchall())
    seen, out = set(), []
    for mid, wend, winner, rate, expo, oi, p, size, ts in db.execute(q, args):
        if a.method == "snapshot":
            if (mid, oi) in seen:
                continue
            seen.add((mid, oi))
            size = 1.0  # one equal-sized bet per market side
        fee = rate * (p * (1 - p)) ** expo if rate else 0.0
        out.append(dict(mid=mid, oi=oi, wend=wend, won=int(oi == winner), p=p, size=size, cost=p + fee))
    return out, counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", nargs="*")
    ap.add_argument("--method", choices=["snapshot", "fills"], default="snapshot")
    ap.add_argument("--at", type=float, default=9, help="snapshot: minutes before close")
    ap.add_argument("--outcome-index", type=int, help="0 = Up, 1 = Down; default both")
    ap.add_argument("--out")
    a = ap.parse_args()

    bs, counts = bets(a)
    print(f"markets by status: {counts}")
    print(f"method={a.method}" + (f" at T-{a.at:g}min" if a.method == "snapshot" else "")
          + f"  bets={len(bs):,}  series={a.series or 'all'}"
          + f"  outcome={'both' if a.outcome_index is None else a.outcome_index}")
    if not bs:
        print("NOTHING TO SCORE: no fills matched the filters above.")
        return

    buckets = defaultdict(list)
    for b in bs:
        buckets[min(int(b["p"] * NB), NB - 1)].append(b)

    hdr = ("bucket", "bets", "sides", "windows", "avg_price", "breakeven", "win_rate", "wilson_lo",
           "wilson_hi", "edge", "edge_se", "t", "roi_per_$", "fee_per_$", "win_$/loss_$")
    rows = []
    print("\n" + " ".join(f"{h:>10}" for h in hdr))
    for i in sorted(buckets):
        bk = buckets[i]
        sh = sum(b["size"] for b in bk)
        avg_p = sum(b["size"] * b["p"] for b in bk) / sh
        be = sum(b["size"] * b["cost"] for b in bk) / sh
        wr = sum(b["size"] * b["won"] for b in bk) / sh
        sides = {(b["mid"], b["oi"]): b["won"] for b in bk}
        if a.method == "snapshot":
            wl, wh = wilson(sum(b["won"] for b in bk), len(bk))
        else:  # no honest Wilson for size-weighted, dependent fills; use the clustered t
            wl = wh = float("nan")
        mean_size = sh / len(bk)
        by_win = defaultdict(list)
        for b in bk:  # size-weighted edge per share; its mean is exactly wr - be
            by_win[b["wend"]].append((b["won"] - b["cost"]) * b["size"] / mean_size)
        edge, se, G = cluster_mean_se(by_win)
        r = (f"{i / NB:.2f}-{(i + 1) / NB:.2f}", len(bk), len(sides), G, avg_p, be, wr, wl, wh,
             edge, se, edge / se if se else float("nan"), (wr - be) / be, (be - avg_p) / be, (1 - be) / be)
        rows.append(r)
        print(" ".join(f"{x:>10}" if isinstance(x, (str, int)) else f"{x:>10.4f}" for x in r))

    tot_stake = sum(b["size"] * b["cost"] for b in bs)
    tot_pnl = sum(b["size"] * (b["won"] - b["cost"]) for b in bs)
    print(f"\nall buckets: ROI per $ staked {tot_pnl / tot_stake:+.4f}")
    print("edge = win_rate - breakeven. win_$/loss_$ = $ won per $1 staked on a win; a loss costs the $1.")
    if a.out:
        with open(a.out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(hdr)
            w.writerows(rows)
        print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
