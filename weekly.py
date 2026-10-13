"""Weekly analysis of the 15m favourite trade on Polymarket (request of 2026-10-13).

    python weekly.py > results/weekly_analysis.md

Trade: at T-9 buy whichever side's first fill is in 0.90 <= p < 0.96; YES = Up, NO = Down.
$10 flat, contracts = int(10 / p); Polymarket's actual fee per bet (regime by close time,
as h2h.py). SEs clustered by close time. Weeks are ISO weeks (Mon-Sun, UTC).
"""
import bisect
import math
import sqlite3
import statistics as st
import time
from collections import defaultdict

import h2h

h2h.STAKE = 10.0  # $10 flat: units = floor(10 / p)
SER = {"BTC": "btc-up-or-down-15m", "ETH": "eth-up-or-down-15m", "SOL": "sol-up-or-down-15m",
       "XRP": "xrp-up-or-down-15m", "DOGE": "doge-up-or-down-15m"}


def wk(t):
    return time.strftime("%G-W%V", time.gmtime(t))


def wk_start(w):
    y, n = w.split("-W")
    return time.strftime("%m-%d", time.gmtime(time.mktime(time.strptime(f"{y} {n} 1", "%G %V %u")) - time.timezone))


def cl(bs, key="net_all"):
    m, se, n = h2h.cmean(bs, key)
    return m, se, n


def tstat(m, se):
    return m / se if se and se == se else float("nan")


def welch(a, b):
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    va, vb = st.variance(a), st.variance(b)
    se = math.sqrt(va / len(a) + vb / len(b))
    return (st.mean(a) - st.mean(b)) / se if se else float("nan")


def main():
    bets, excl = h2h.load_pm()
    for b in bets:
        b["wk"] = wk(b["close"])
    weeks = sorted({b["wk"] for b in bets})
    side_name = {0: "YES", 1: "NO"}

    print("# Weekly analysis: 15m favourite trade on Polymarket\n")
    print(f"Data 2026-04-01..10-07, 5 coins, {len(bets):,} in-band bets ({sum(b['side']==0 for b in bets):,} YES/Up,"
          f" {sum(b['side']==1 for b in bets):,} NO/Down). $10 flat, contracts = int(10/p), Polymarket fees.")
    print(f"Excluded markets: {dict(excl)}. SEs clustered by close time.\n")

    print("## Overall\n\n| side | n | win % | break-even % | net $/bet ± 95% | t | total $ |\n|---|---|---|---|---|---|---|")
    for s in (0, 1):
        bs = [b for b in bets if b["side"] == s]
        m, se, n = cl(bs)
        print(f"| {side_name[s]} | {n:,} | {100*st.mean(b['won'] for b in bs):.1f} | "
              f"{100*st.mean(b['breakeven'] for b in bs):.1f} | {m:+.3f} ± {1.96*se:.3f} | {tstat(m,se):+.2f} | "
              f"{sum(b['net_all'] for b in bs):+.2f} |")

    # ---- Analysis 1: weekly table
    print("\n## Analysis 1: week by week\n")
    print("| week (Mon) | YES n | YES win% | YES net $ | YES cum $ | NO n | NO win% | NO net $ | NO cum $ |")
    print("|---|---|---|---|---|---|---|---|---|")
    cum = {0: 0.0, 1: 0.0}
    W = {}
    for w in weeks:
        row = [wk_start(w)]
        W[w] = {}
        for s in (0, 1):
            bs = [b for b in bets if b["wk"] == w and b["side"] == s]
            net = sum(b["net_all"] for b in bs)
            cum[s] += net
            W[w][s] = (len(bs), net, bs)
            row += [f"{len(bs)}", f"{100*st.mean(b['won'] for b in bs):.1f}" if bs else "", f"{net:+.2f}", f"{cum[s]:+.2f}"]
        print("| " + " | ".join(row) + " |")

    # a) same weeks or different?
    ys = [W[w][0][1] for w in weeks]
    ns = [W[w][1][1] for w in weeks]
    r = st.correlation(ys, ns)
    rank = lambda v: [sorted(v).index(x) for x in v]
    rho = st.correlation(rank(ys), rank(ns))
    same = sum((a > 0) == (b > 0) for a, b in zip(ys, ns))
    both_neg = sum(a < 0 and b < 0 for a, b in zip(ys, ns))
    opp = sum((a > 0) != (b > 0) for a, b in zip(ys, ns))
    print(f"\n### a) Do the sides fail together?\n")
    print(f"Weekly YES net vs NO net across {len(weeks)} weeks: Pearson r = {r:+.2f}, Spearman rho = {rho:+.2f}.")
    print(f"Same sign in {same} of {len(weeks)} weeks ({both_neg} weeks both negative); opposite sign in {opp}.")
    worst_y = sorted(weeks, key=lambda w: W[w][0][1])[:5]
    worst_n = sorted(weeks, key=lambda w: W[w][1][1])[:5]
    print(f"5 worst YES weeks: {[wk_start(w) for w in worst_y]}; 5 worst NO weeks: {[wk_start(w) for w in worst_n]};"
          f" overlap {len(set(worst_y) & set(worst_n))} of 5.")
    # bet-level: same windows
    bywin = defaultdict(list)
    for b in bets:
        bywin[b["close"]].append(b)
    multi = [v for v in bywin.values() if len(v) > 1]
    allsame = sum(len({b["won"] for b in v}) == 1 for v in multi)
    print(f"Within a close window: {len(multi):,} windows have 2+ bets; every leg resolved the same way in {allsame:,} "
          f"({100*allsame/len(multi):.0f}%).")

    # b) concentration
    print("\n### b) How concentrated is the profit?\n")
    print("| side | weeks + / total | total $ | best week $ (share of total) | worst week $ | total without best & worst | net/bet without best & worst (t) |")
    print("|---|---|---|---|---|---|---|")
    for s in (0, 1):
        nets = {w: W[w][s][1] for w in weeks}
        tot = sum(nets.values())
        bw, ww = max(nets, key=nets.get), min(nets, key=nets.get)
        rest = [b for w in weeks if w not in (bw, ww) for b in W[w][s][2]]
        m, se, n = cl(rest)
        share = f"{100*nets[bw]/tot:.0f}%" if tot > 0 else "n/a (total <= 0)"
        print(f"| {side_name[s]} | {sum(v > 0 for v in nets.values())} / {len(weeks)} | {tot:+.2f} | "
              f"{nets[bw]:+.2f} ({wk_start(bw)}, {share}) | {nets[ww]:+.2f} ({wk_start(ww)}) | "
              f"{sum(b['net_all'] for b in rest):+.2f} | {m:+.3f} (t {tstat(m,se):+.2f}, n {n:,}) |")

    # c) periods
    print("\n### c) The two periods\n")
    print("| period | side | n | win % | break-even % | net $/bet ± 95% | t | total $ |\n|---|---|---|---|---|---|---|---|")
    for lab, a, z in (("04-01..07-18", "2026-04-01", "2026-07-18"), ("07-19..10-07", "2026-07-19", "2026-10-07")):
        for s in (0, 1):
            bs = [b for b in h2h.window(bets, a, z) if b["side"] == s]
            m, se, n = cl(bs)
            print(f"| {lab} | {side_name[s]} | {n:,} | {100*st.mean(b['won'] for b in bs):.1f} | "
                  f"{100*st.mean(b['breakeven'] for b in bs):.1f} | {m:+.3f} ± {1.96*se:.3f} | {tstat(m,se):+.2f} | "
                  f"{sum(b['net_all'] for b in bs):+.2f} |")

    # ---- Analysis 2: weekly variables
    db = sqlite3.connect("data/ptb.sqlite")
    px = defaultdict(list)
    for s, e, p in db.execute("SELECT series, window_end, price FROM ptb ORDER BY window_end"):
        px[s].append((e, p))
    allm = {}  # every market's Up and Down first fills (not only in band), for decisiveness
    for path in ("data/t9_a.sqlite", "data/t9_b.sqlite"):
        d = sqlite3.connect(path)
        for mid, oi, p, close, status in d.execute(
                "SELECT e.market_id, e.outcome_index, e.price, m.window_end, m.status FROM entries e "
                "JOIN markets m USING (market_id) WHERE m.series IN (" + ",".join("?" * 5) + ")", list(SER.values())):
            allm.setdefault(mid, {"close": close})[oi] = p
    V = {}
    for w in weeks:
        v = {}
        vols, ers, drifts = [], [], []
        for s in SER.values():
            seq = [p for e, p in px[s] if wk(e) == w]
            rets = [math.log(b / a) for a, b in zip(seq, seq[1:]) if a > 0 and b > 0]
            if len(rets) > 100:
                vols.append(st.pstdev(rets) * 1e4)
                ers.append(abs(sum(rets)) / sum(abs(x) for x in rets))
                drifts.append(100 * sum(rets))
        v["1 realized vol (15m stdev, bp)"] = st.mean(vols)
        v["2a trend efficiency |net|/path"] = st.mean(ers)
        v["2b net drift % (avg of 5 coins)"] = st.mean(drifts)
        ms = [m for m in allm.values() if wk(m["close"]) == w]
        ups = [m[0] if 0 in m else 1 - m[1] for m in ms if 0 in m or 1 in m]
        v["3a decisiveness mean |p_up - 0.5|"] = st.mean(abs(p - 0.5) for p in ups)
        v["3b share of markets with an in-band side"] = st.mean(
            any(0.90 <= m.get(k, -1) < 0.96 for k in (0, 1)) for m in ms)
        days = 7 if w not in (weeks[0], weeks[-1]) else len({time.gmtime(b["close"]).tm_yday for b in bets if b["wk"] == w})
        for s in (0, 1):
            bs = W[w][s][2]
            v[f"4 bets/day {side_name[s]}"] = len(bs) / days
            sl = [b["p"] - b["mid"] for b in bs if b["mid"] is not None]
            v[f"5 slippage proxy fill-mid c {side_name[s]}"] = 100 * st.mean(sl) if sl else float("nan")
            v[f"6 avg entry price c {side_name[s]}"] = 100 * st.mean(b["p"] for b in bs) if bs else float("nan")
            v[f"8 win% - breakeven% {side_name[s]}"] = (100 * (st.mean(b["won"] for b in bs) - st.mean(b["breakeven"] for b in bs))
                                                       if bs else float("nan"))
        wb = [b for s in (0, 1) for b in W[w][s][2]]
        cnt = defaultdict(int)
        for b in wb:
            cnt[b["close"]] += 1
        v["7 share of bets sharing a close window"] = st.mean(cnt[b["close"]] > 1 for b in wb)
        v["_pnl"] = W[w][0][1] + W[w][1][1]
        v["_n"] = len(wb)
        V[w] = v

    full = [w for w in weeks if w not in (weeks[0], weeks[-1])]  # drop partial first/last week
    ranked = sorted(full, key=lambda w: V[w]["_pnl"], reverse=True)
    k = len(ranked) // 3
    top, bot = ranked[:k], ranked[-k:]
    nt, nb = sum(V[w]["_n"] for w in top), sum(V[w]["_n"] for w in bot)
    print("\n## Analysis 2: top third vs bottom third of weeks (YES+NO P&L)\n")
    print(f"{len(full)} full weeks (partial first and last weeks dropped). Top {k} weeks: "
          f"{[wk_start(w) for w in top]} ({nt:,} bets, {sum(V[w]['_pnl'] for w in top):+.2f} $). "
          f"Bottom {k}: {[wk_start(w) for w in bot]} ({nb:,} bets, {sum(V[w]['_pnl'] for w in bot):+.2f} $).")
    print("Unit of comparison = the week (n = {} vs {} weeks); t is Welch's t on weekly values.\n".format(k, k))
    keys = [x for x in V[full[0]] if not x.startswith("_")]
    print("| variable | good weeks (mean) | bad weeks (mean) | diff | t (weeks) | flag |\n|---|---|---|---|---|---|")
    for key in keys:
        a = [V[w][key] for w in top if V[w][key] == V[w][key]]
        b = [V[w][key] for w in bot if V[w][key] == V[w][key]]
        t = welch(a, b)
        flag = "FLAG" if abs(t) > 2.5 and min(nt, nb) >= 150 else ""
        print(f"| {key} | {st.mean(a):.3f} | {st.mean(b):.3f} | {st.mean(a)-st.mean(b):+.3f} | {t:+.2f} | {flag} |")
    print(f"\nComparisons made: {len(keys)} variables (some split by side). By luck, ~{len(keys)*0.05:.1f} would clear |t| > 2 "
          f"and ~{len(keys)*0.012:.1f} would clear |t| > 2.5.")


if __name__ == "__main__":
    main()
