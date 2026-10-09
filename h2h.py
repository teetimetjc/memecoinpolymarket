"""Score the head-to-head exactly as specs/headtohead.md (with amendments 1-3) says.

    python h2h.py

No thresholds are adjustable here: every constant below is copied from the spec.
Inputs (paths only):
  data/t9_a.sqlite, data/t9_b.sqlite      Polymarket, from collect_t9.py
  data/kalshi/quotes.csv.gz, trades.csv.gz Kalshi, from kalshi_export.yml (data branch)
  data/books/**/*.csv.gz                   Polymarket live books (depth, different period)
Writes results/h2h.md and results/h2h.csv.
"""
import calendar
import csv
import glob
import gzip
import math
import os
import sqlite3
import statistics
import time
from collections import Counter, defaultdict

# ---- constants from the spec; do not edit without a new dated amendment ----
T9 = 540                        # seconds before close
BAND = (0.90, 0.96)             # 0.90 <= p < 0.96 (amendment 2)
STAKE = 4.00                    # units = floor(4.00 / p)
PM_RATE_USDC = 0.07             # usdc regime: fee = u*0.07*p*(1-p), added to cost
PM_RATE_SHARES = 0.072          # shares regime: 0.072*u*(1-p) shares withheld on a win
KALSHI_RATE = 0.07              # ceil to the cent
MID_PAIR_S = 15                 # Polymarket paired-fill mid (amendment 3)
MAX_DELAY_S = 3600              # Polymarket resolution delay exclusion
P_START, P_END = "2026-04-01", "2026-10-07"
HOLDOUT_END = "2026-09-07"      # 2026-04-01..2026-09-07 unseen; 09-08..10-07 seen
PM_SERIES = {"btc-up-or-down-15m": "BTC", "eth-up-or-down-15m": "ETH", "sol-up-or-down-15m": "SOL",
             "xrp-up-or-down-15m": "XRP", "doge-up-or-down-15m": "DOGE"}
K_SERIES = {"KXBTC15M": "BTC", "KXETH15M": "ETH", "KXSOL15M": "SOL", "KXXRP15M": "XRP", "KXDOGE15M": "DOGE"}
# ---------------------------------------------------------------------------


def day_ts(d, end=False):
    return calendar.timegm(time.strptime(d + ("T23:59:59" if end else "T00:00:00"), "%Y-%m-%dT%H:%M:%S"))


def kts(s):
    try:
        return calendar.timegm(time.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


def dollars(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x / 100 if x > 1 else x


def in_band(p):
    return p is not None and BAND[0] <= p < BAND[1]


def bet(venue, row, side, close, p, mid, won, regime=None, extra=None):
    u = math.floor(STAKE / p)
    if venue == "PM" and regime == "shares":
        keep = 1 - PM_RATE_SHARES * (1 - p)        # fraction of each share paid on a win
        fee = u * PM_RATE_SHARES * (1 - p) * won   # dollars lost to the fee (only on a win)
        stake = u * p
        payout = u * keep * won
        breakeven = p / keep
        net_all = payout - u * p
        net_fee = None if mid is None else payout - u * mid
    else:
        if venue == "PM":
            fee = u * PM_RATE_USDC * p * (1 - p)
        else:
            fee = math.ceil(round(100 * KALSHI_RATE * u * p * (1 - p), 9)) / 100
        stake = u * p + fee
        breakeven = stake / u
        net_all = u * won - stake
        net_fee = None if mid is None else u * won - u * mid - fee
    gross = None if mid is None else u * won - u * mid
    return dict(venue=venue, row=row, side=side, close=close, p=p, mid=mid, won=won, u=u, fee=fee,
                stake=stake, breakeven=breakeven, gross=gross, net_fee=net_fee, net_all=net_all,
                regime=regime, **(extra or {}))


# ---------------------------------------------------------------- Polymarket
def load_pm():
    bets, excl = [], Counter()
    for path in ("data/t9_a.sqlite", "data/t9_b.sqlite"):
        if not os.path.exists(path):
            excl[f"missing {path}"] += 1
            continue
        db = sqlite3.connect(path)
        mk = {r[0]: r for r in db.execute(
            "SELECT market_id, series, window_end, closed_time, winner, uma_status, fee_regime, status FROM markets")}
        ent = defaultdict(dict)
        for mid_, oi, ts, price, size in db.execute("SELECT market_id, outcome_index, fill_ts, price, size FROM entries"):
            ent[mid_][oi] = (ts, price, size)
        for mid_, (_, series, close, closed, winner, uma, regime, status) in mk.items():
            if series not in PM_SERIES or not (day_ts(P_START) <= close <= day_ts(P_END, True)):
                continue
            why = (status != "ok" and status[:20]) or (winner is None and "no clean 1/0") or \
                  (uma != "resolved" and f"uma={uma}") or \
                  ((closed is None or closed - close > MAX_DELAY_S) and "delayed/no close time") or \
                  (regime not in ("usdc", "shares") and f"regime={regime}")
            if why:
                excl[why] += 1
                continue
            for side in (0, 1):
                if side not in ent[mid_]:
                    continue
                ts, p, size = ent[mid_][side]
                if not in_band(p):
                    continue
                o = ent[mid_].get(1 - side)
                mid = (p + 1 - o[1]) / 2 if o and abs(o[0] - ts) <= MID_PAIR_S else None
                bets.append(bet("PM", "P-fill", side, close, p, mid, int(winner == side), regime,
                                dict(size=size, asset=PM_SERIES[series], lag=ts - (close - T9))))
    return bets, excl


# -------------------------------------------------------------------- Kalshi
def load_kalshi():
    q_paths = sorted(glob.glob("data/kalshi/quotes_*.csv.gz"))
    t_paths = sorted(glob.glob("data/kalshi/trades_*.csv.gz"))
    if not q_paths:
        return [], [], Counter({"no Kalshi export yet": 1})
    quotes, excl, dup = {}, Counter(), 0
    for q_path in q_paths:  # each export re-reads the whole sheet: identical rows, dedupe by ticker
        with gzip.open(q_path, "rt") as f:
            for r in csv.DictReader(f):
                if r["Ticker"] in quotes:
                    dup += 1
                    continue
                quotes[r["Ticker"]] = r
    excl["duplicate ticker across tabs/files (kept first)"] = dup
    first = defaultdict(dict)  # ticker -> side -> (ts, price)
    for t_path in t_paths:
        with gzip.open(t_path, "rt") as f:
            for r in csv.DictReader(f):
                ts = kts(r["created_time"])
                close = int(r["close_ts"])
                if ts is None or not (close - T9 <= ts < close):
                    continue
                side = {"yes": 0, "no": 1}.get(str(r["taker_side"]).lower())
                if side is None:
                    continue
                p = dollars(r["yes_price"] if side == 0 else r["no_price"])
                if p is None:
                    continue
                if side not in first[r["ticker"]] or ts < first[r["ticker"]][side][0]:
                    first[r["ticker"]][side] = (ts, p)
    kq, kf = [], []
    for tk, r in quotes.items():
        close = kts(r["Close Time"])
        res = str(r["Result"]).lower().strip()
        if close is None or res not in ("yes", "no"):
            excl["not settled yes/no"] += 1
            continue
        b, a = dollars(r["bid9"]), dollars(r["ask9"])
        has_q = b is not None and a is not None and 0 < b <= a < 1
        mid = (b + a) / 2 if has_q else None
        extra = dict(asset=K_SERIES.get(r["Series"], "?"), asksz=dollars_sz(r["asksz9"]), bidsz=dollars_sz(r["bidsz9"]))
        if has_q:
            for side, p, m in ((0, a, mid), (1, 1 - b, 1 - mid)):
                if in_band(p):
                    kq.append(bet("K", "K-quote", side, close, p, m, int(res == ("yes" if side == 0 else "no")),
                                  extra=extra))
        for side, (ts, p) in first.get(tk, {}).items():
            if in_band(p):
                m = None if mid is None else (mid if side == 0 else 1 - mid)
                kf.append(bet("K", "K-fill", side, close, p, m, int(res == ("yes" if side == 0 else "no")),
                              extra=dict(extra, lag=ts - (close - T9))))
    return kq, kf, excl


def dollars_sz(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- statistics
def wilson(k, n, z=1.96):
    if not n:
        return float("nan"), float("nan")
    ph = k / n
    d = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / d
    h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def cmean(bs, key):
    xs = [(b["close"], b[key]) for b in bs if b[key] is not None]
    n = len(xs)
    if n < 2:
        return (xs[0][1] if xs else float("nan")), float("nan"), n
    m = sum(x for _, x in xs) / n
    g = defaultdict(float)
    for c, x in xs:
        g[c] += x - m
    G = len(g)
    se = math.sqrt(G / (G - 1) * sum(v * v for v in g.values())) / n if G > 1 else float("nan")
    return m, se, n


def summarise(bs):
    if not bs:
        return None
    n = len(bs)
    k = sum(b["won"] for b in bs)
    wl, wh = wilson(k, n)
    # GROSS, NET-fee and NET-all(mid) on the SAME bets (those with a mid), so the three
    # read as one cost ladder; NET-all over every bet is reported alongside.
    wm = [b for b in bs if b["mid"] is not None]
    g, gse, gn = cmean(wm, "gross")
    nf, nfse, _ = cmean(wm, "net_fee")
    nam, namse, _ = cmean(wm, "net_all")
    na, nase, _ = cmean(bs, "net_all")
    stake = statistics.mean(b["stake"] for b in bs)
    need = (1.96 * nase * math.sqrt(n) / abs(na)) ** 2 if na and nase == nase else float("nan")
    mids = [b["mid"] for b in bs if b["mid"] is not None]
    return dict(bets=n, windows=len({b["close"] for b in bs}), avg_p=statistics.mean(b["p"] for b in bs),
                avg_mid=statistics.mean(mids) if mids else float("nan"), n_mid=gn,
                breakeven=statistics.mean(b["breakeven"] for b in bs), win=k / n, wl=wl, wh=wh,
                gross=g, gross_se=gse, net_fee=nf, net_fee_se=nfse, net_all_mid=nam, net_all_mid_se=namse,
                net_all=na, net_all_se=nase,
                stake=stake, n_needed=need)


def fmt_row(label, s, note=""):
    if not s:
        return f"| {label} | 0 | | | | | | | | | | {note} |"
    pct = lambda x: f"{100 * x / s['stake']:+.2f}%"
    return (f"| {label} | {s['bets']:,} ({s['windows']:,}) | {s['avg_p']:.4f} | {s['breakeven']:.2%} | "
            f"{s['win']:.2%} [{s['wl']:.2%}, {s['wh']:.2%}] | "
            f"{s['gross']:+.4f} ± {1.96 * s['gross_se']:.4f} ({pct(s['gross'])}, n={s['n_mid']:,}) | "
            f"{s['net_fee']:+.4f} ({pct(s['net_fee'])}) | "
            f"{s['net_all_mid']:+.4f} ± {1.96 * s['net_all_mid_se']:.4f} ({pct(s['net_all_mid'])}) | "
            f"{s['net_all']:+.4f} ± {1.96 * s['net_all_se']:.4f} ({pct(s['net_all'])}) | "
            f"{s['n_needed']:,.0f} | {note} |")


HDR = ("| row | bets (windows) | avg fill | break-even | win rate [Wilson 95%] "
       "| GROSS $/bet ± 95% (% stake, n with mid) | NET fees $/bet (same n) | NET all $/bet ± 95% (same n) "
       "| NET all, every bet ± 95% | bets needed | note |\n"
       "|---|---|---|---|---|---|---|---|---|---|---|")


def window(bs, a, b):
    return [x for x in bs if day_ts(a) <= x["close"] <= day_ts(b, True)]


def main():
    pm, pm_ex = load_pm()
    kq, kf, k_ex = load_kalshi()
    out = ["# Head-to-head: Kalshi narrow slice vs Polymarket (specs/headtohead.md)", "",
           f"Generated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}. Units: 4 per bet "
           "(floor($4/p)) on both venues. ± is a 95% interval clustered by close time.", ""]

    # overlap of Kalshi fills and Polymarket
    kspan = (min((b["close"] for b in kf), default=None), max((b["close"] for b in kf), default=None))
    if kspan[0]:
        ov = (max(kspan[0], day_ts(P_START)), min(kspan[1], day_ts(P_END, True)))
        ovs = (time.strftime("%Y-%m-%d", time.gmtime(ov[0])), time.strftime("%Y-%m-%d", time.gmtime(ov[1])))
    else:
        ovs = None

    for side, name in ((0, "YES / Up — the rule"), (1, "NO / Down — mirror test")):
        out += [f"## {name}", ""]
        if ovs:
            out += [f"### Head-to-head, same calendar window {ovs[0]} to {ovs[1]}", "", HDR]
            for lab, bs in (("Kalshi K-fill", kf), ("Polymarket P-fill", pm), ("Kalshi K-quote (existing backtest def.)", kq)):
                sel = [b for b in window(bs, *ovs) if b["side"] == side]
                out.append(fmt_row(lab + " — band 90-96c", summarise(sel)))
            for c in range(90, 96):
                for lab, bs in (("K-fill", kf), ("P-fill", pm)):
                    sel = [b for b in window(bs, *ovs) if b["side"] == side and math.floor(100 * b["p"] + 1e-9) == c]
                    out.append(fmt_row(f"{lab} {c}c", summarise(sel)))
            out.append("")
        else:
            out += ["**No Kalshi fill data yet: the head-to-head table is not produced.**", ""]
        out += ["### Polymarket, full sample and its parts (context)", "", HDR]
        for lab, a, b, extra in (("P-fill all 2026-04-01..10-07", P_START, P_END, None),
                                 ("P-fill HOLDOUT 04-01..09-07", P_START, HOLDOUT_END, None),
                                 ("P-fill seen 09-08..10-07", "2026-09-08", P_END, None),
                                 ("P-fill regime shares", P_START, P_END, "shares"),
                                 ("P-fill regime usdc", P_START, P_END, "usdc")):
            sel = [x for x in window(pm, a, b) if x["side"] == side and (extra is None or x["regime"] == extra)]
            out.append(fmt_row(lab, summarise(sel)))
        if kq:
            out.append(fmt_row("Kalshi K-quote, all dates in export", summarise([b for b in kq if b["side"] == side]),
                               "context only"))
        out.append("")

    # pass / fail (spec §7)
    hold = [x for x in window(pm, P_START, HOLDOUT_END) if x["side"] == 0]
    s = summarise(hold)
    verdict = "NO DATA"
    if s:
        lo = s["net_all"] - 1.96 * s["net_all_se"]
        verdict = "PASS" if s["net_all"] > 0 and lo > 0 else "FAIL"
        out += ["## Spec §7 verdict", "",
                f"Holdout, Up side, band total: NET-all {s['net_all']:+.4f} $/bet, 95% interval "
                f"[{lo:+.4f}, {s['net_all'] + 1.96 * s['net_all_se']:+.4f}], {s['bets']:,} bets → **{verdict}**. "
                f"Bets needed to separate the observed mean from zero: {s['n_needed']:,.0f}.", ""]

    # structural facts
    out += ["## Structural differences and data checks", ""]
    pin = [b for b in pm if b["side"] == 0]
    if pin:
        out.append(f"- Polymarket in-band distinct fill prices: {len({round(b['p'], 6) for b in pin}):,} "
                   f"(fills are size-averaged, effectively continuous); Kalshi: "
                   f"{len({round(b['p'], 4) for b in kf if b['side'] == 0}):,} (1c tick).")
        out.append(f"- Polymarket median first-fill size in band: {statistics.median(b['size'] for b in pin):.1f} shares; "
                   f"median lag after T-9: {statistics.median(b['lag'] for b in pin):.0f} s.")
        out.append(f"- Polymarket bets with a paired-fill mid: {sum(b['mid'] is not None for b in pin):,} of {len(pin):,}.")
    if kf:
        out.append(f"- Kalshi K-fill median lag after T-9: {statistics.median(b['lag'] for b in kf):.0f} s.")
    kd = [b["asksz"] for b in kq if b["side"] == 0 and b.get("asksz") is not None]
    if kd:
        out.append(f"- Kalshi depth at the quoted ask (asksz9), YES in band: median {statistics.median(kd):,.0f} contracts.")
    out.append(f"- Polymarket exclusions: {dict(pm_ex) or 'none'}")
    out.append(f"- Kalshi exclusions: {dict(k_ex) or 'none'}")
    for path in ("data/t9_a.sqlite", "data/t9_b.sqlite"):
        if os.path.exists(path):
            rc = Counter(r[0] for r in sqlite3.connect(path).execute("SELECT verdict FROM recon"))
            out.append(f"- Fee-regime reconciliation sample ({path}): {dict(rc)}")
    out += _depth_from_books()
    os.makedirs("results", exist_ok=True)
    open("results/h2h.md", "w").write("\n".join(out) + "\n")
    print("\n".join(out))


def _depth_from_books():
    files = glob.glob("data/books/**/*.csv.gz", recursive=True)
    if not files:
        return ["- Polymarket live-book depth: no snapshots available locally."]
    asks, d1 = [], []
    for f in files:
        with gzip.open(f, "rt") as fh:
            for r in csv.DictReader(fh):
                if not r["series"].endswith("15m") or r["series"] not in PM_SERIES:
                    continue
                if not (8 <= float(r["min_left"]) <= 10) or not r["best_ask"]:
                    continue
                if in_band(float(r["best_ask"])):
                    asks.append(float(r["ask_size"] or 0))
                    d1.append(float(r["ask_depth_1c"] or 0))
    if not asks:
        return ["- Polymarket live-book depth: no in-band asks at 8-10 min left in the snapshots yet."]
    return [f"- Polymarket live-book depth (snapshots since 2026-10-08, DIFFERENT PERIOD), in-band ask at 8-10 min "
            f"left: median {statistics.median(asks):,.0f} shares at best ask, {statistics.median(d1):,.0f} within 1c "
            f"({len(asks):,} snapshots)."]


if __name__ == "__main__":
    main()
