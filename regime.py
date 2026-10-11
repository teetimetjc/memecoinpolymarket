"""Score specs/regime.md. Constants copy the spec; the only flag is the stage.

    python regime.py discovery   # discovery stats + the medians to freeze (amendment)
    python regime.py holdout     # refuses unless FROZEN_MEDIANS is filled in from the amendment
"""
import bisect
import calendar
import math
import sqlite3
import statistics
import sys
import time
from collections import defaultdict

import h2h

DISC = ("2026-04-01", "2026-07-31")
HOLD = ("2026-08-01", "2026-10-07")
BAD = ("2026-04-01", "2026-07-18")
T_DISC, T_HOLD, RHO_MIN = 3.0, 2.0, 0.3
VOL_WINDOWS = 96
MOM_DAYS, MOM_MIN = 7, 50
BLOCKS = ((0, 5), (6, 11), (12, 17), (18, 23))
SERIES = {"BTC": "btc-up-or-down-15m", "ETH": "eth-up-or-down-15m", "SOL": "sol-up-or-down-15m",
          "XRP": "xrp-up-or-down-15m", "DOGE": "doge-up-or-down-15m"}
# Filled in ONLY from the committed discovery amendment in specs/regime.md:
FROZEN_MEDIANS = None  # e.g. {"S1": ..., "S2": ..., "S4": ...}
FROZEN_FAVOURABLE = None  # e.g. {"S1": "LOW", "S2": "HIGH", "S3": "06-11", "S4": "HIGH"}


def ts(d, end=False):
    return calendar.timegm(time.strptime(d + ("T23:59:59" if end else "T00:00:00"), "%Y-%m-%dT%H:%M:%S"))


def load():
    bets, _ = h2h.load_pm()
    db = sqlite3.connect("data/ptb.sqlite")
    px = defaultdict(list)
    for series, end, p in db.execute("SELECT series, window_end, price FROM ptb ORDER BY window_end"):
        px[series].append((end, p))
    ends = {s: [e for e, _ in v] for s, v in px.items()}
    # S1: stdev of 15m log returns over the 96 windows up to and including this window's start
    for b in bets:
        s = SERIES[b["asset"]]
        i = bisect.bisect_right(ends[s], b["close"])  # prices with window_end <= close
        seq = [p for _, p in px[s][max(0, i - VOL_WINDOWS - 1):i]]
        rets = [math.log(b2 / a) for a, b2 in zip(seq, seq[1:]) if a > 0 and b2 > 0]
        b["S1"] = statistics.pstdev(rets) if len(rets) >= VOL_WINDOWS * 0.9 else None
        b["S2"] = None if b["mid"] is None else 2 * (b["p"] - b["mid"])
        h = time.gmtime(b["close"]).tm_hour
        b["S3"] = next(f"{lo:02d}-{hi:02d}" for lo, hi in BLOCKS if lo <= h <= hi)
    # S4: mean NET-all of same-side bets that closed in the 7 days before this bet's entry (T-9)
    for side in (0, 1):
        sb = sorted((b for b in bets if b["side"] == side), key=lambda b: b["close"])
        closes = [b["close"] for b in sb]
        cum = [0.0]
        for b in sb:
            cum.append(cum[-1] + b["net_all"])
        for b in sb:
            entry = b["close"] - 540
            j = bisect.bisect_right(closes, entry)
            i = bisect.bisect_left(closes, entry - MOM_DAYS * 86400)
            n = j - i
            b["S4"] = (cum[j] - cum[i]) / n if n >= MOM_MIN else None
    return bets


def window(bets, a, b):
    return [x for x in bets if ts(a) <= x["close"] <= ts(b, True)]


def stats(bs):
    m, se, n = h2h.cmean(bs, "net_all")
    return m, se, n


def diff(fav, unf):
    mf, sf, nf = stats(fav)
    mu, su, nu = stats(unf)
    d = mf - mu
    se = math.sqrt(sf ** 2 + su ** 2)
    return d, se, (d / se if se else float("nan")), (mf, sf, nf), (mu, su, nu)


def groups(bets, sig, median, fav):
    if sig == "S3":
        f = [b for b in bets if b["S3"] == fav]
        u = [b for b in bets if b["S3"] != fav]
    else:
        has = [b for b in bets if b[sig] is not None]
        lo = [b for b in has if b[sig] < median]
        hi = [b for b in has if b[sig] >= median]
        f, u = (lo, hi) if fav == "LOW" else (hi, lo)
    return f, u


def fmt(tag, r):
    d, se, t, (mf, sf, nf), (mu, su, nu) = r
    return (f"{tag:<28} fav {mf:+.4f}±{1.96 * sf:.4f} (n={nf:,})  other {mu:+.4f}±{1.96 * su:.4f} (n={nu:,})"
            f"  diff {d:+.4f} t={t:+.2f}")


def discovery(bets):
    print(f"DISCOVERY {DISC[0]}..{DISC[1]}, Up side (the rule); Down side shown as mirror\n")
    up = [b for b in window(bets, *DISC) if b["side"] == 0]
    out = {}
    for sig in ("S1", "S2", "S4"):
        vals = [b[sig] for b in up if b[sig] is not None]
        med = statistics.median(vals)
        lo = [b for b in up if b[sig] is not None and b[sig] < med]
        hi = [b for b in up if b[sig] is not None and b[sig] >= med]
        fav = "LOW" if stats(lo)[0] >= stats(hi)[0] else "HIGH"
        out[sig] = (med, fav)
        r = diff(*groups(up, sig, med, fav))
        print(fmt(f"{sig} median={med:.6g} fav={fav}", r), "PASS-1" if r[2] > T_DISC else "fails t>3")
    blocks = {f"{lo:02d}-{hi:02d}": stats([b for b in up if b["S3"] == f"{lo:02d}-{hi:02d}"]) for lo, hi in BLOCKS}
    best = max(blocks, key=lambda k: blocks[k][0])
    out["S3"] = (None, best)
    r = diff(*groups(up, "S3", None, best))
    print(fmt(f"S3 best block={best}", r), "PASS-1" if r[2] > T_DISC else "fails t>3")
    for k, (m, se, n) in blocks.items():
        print(f"    block {k}: {m:+.4f} ± {1.96 * se:.4f}  n={n:,}")
    print("\nMirror (Down side), same splits:")
    dn = [b for b in window(bets, *DISC) if b["side"] == 1]
    for sig, (med, fav) in out.items():
        print("  " + fmt(sig, diff(*groups(dn, sig, med, fav))))
    print("\nTO FREEZE (amendment to specs/regime.md):")
    print("  FROZEN_MEDIANS =", {k: v[0] for k, v in out.items() if k != "S3"})
    print("  FROZEN_FAVOURABLE =", {k: v[1] for k, v in out.items()})


def spearman(x, y):
    rank = lambda v: {i: r for r, i in enumerate(sorted(range(len(v)), key=lambda i: v[i]))}
    rx, ry = rank(x), rank(y)
    n = len(x)
    return 1 - 6 * sum((rx[i] - ry[i]) ** 2 for i in range(n)) / (n * (n * n - 1))


def holdout(bets):
    if not FROZEN_MEDIANS or not FROZEN_FAVOURABLE:
        sys.exit("REFUSING: FROZEN_MEDIANS / FROZEN_FAVOURABLE are empty. Commit the discovery amendment first.")
    up_all = [b for b in bets if b["side"] == 0]
    for sig in ("S1", "S2", "S3", "S4"):
        med, fav = FROZEN_MEDIANS.get(sig), FROZEN_FAVOURABLE[sig]
        print(f"\n== {sig} (fav {fav}{'' if med is None else f', split {med:.6g}'})")
        rd = diff(*groups(window(up_all, *DISC), sig, med, fav))
        rb = diff(*groups(window(up_all, *BAD), sig, med, fav))
        rg = diff(*groups(window(up_all, "2026-07-19", HOLD[1]), sig, med, fav))
        rh = diff(*groups(window(up_all, *HOLD), sig, med, fav))
        print(fmt("discovery", rd)); print(fmt("bad 04-01..07-18", rb)); print(fmt("good 07-19..10-07", rg))
        print(fmt("HOLDOUT", rh))
        weeks = defaultdict(list)
        f_ids = {id(b) for b in groups(up_all, sig, med, fav)[0]}
        for b in window(up_all, DISC[0], HOLD[1]):
            weeks[time.strftime("%G-%V", time.gmtime(b["close"]))].append(b)
        wk = [(sum(id(b) in f_ids for b in v) / len(v), statistics.mean(b["net_all"] for b in v))
              for v in weeks.values() if len(v) >= 30]
        rho = spearman([a for a, _ in wk], [b for _, b in wk])
        mf, sf, _ = rh[3]
        checks = [rd[2] > T_DISC, rb[0] > 0 and rg[0] > 0, rho > RHO_MIN,
                  rh[2] > T_HOLD and mf > 0 and mf - 1.96 * sf > 0]
        print(f"week-level Spearman rho = {rho:+.2f} over {len(wk)} weeks")
        print("criteria [disc t>3, both periods same sign, weekly rho>0.3, holdout]:", checks,
              "=> PASS" if all(checks) else "=> FAIL")


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else ""
    b = load()
    {"discovery": discovery, "holdout": holdout}.get(stage, lambda _: sys.exit("usage: regime.py discovery|holdout"))(b)
