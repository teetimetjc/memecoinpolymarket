"""Lean collector for the head-to-head spec (specs/headtohead.md). Read-only.

    python collect_t9.py --series btc-up-or-down-15m ... --start 2026-04-01 --end 2026-10-07 --db data/t9.sqlite

Per settled market, per side (Up=0, Down=1):
  the first taker BUY fill at or after close-540s (oldest fill in [T-9, close)).
The midpoint is NOT taken from clob /prices-history: on 2026-04-28 it sat at 0.50
while trades printed at 0.01/0.99 (median |fill - history| 5.5c). The scorer
estimates it from the two sides' first fills instead (amendment 3).
Only the trades in [T-9, T-8) are fetched, widening to [T-8, close) only for a side
with no BUY in the first minute, so this costs ~4 requests per market instead of
paging every fill.

Fee regime is assigned by close time (switch found by reconciliation on 2026-04-28,
see specs/headtohead.md amendment 3), and CHECKED by fully reconciling a sample of
markets per day (`recon` table). Every market is recorded with a status; nothing is
dropped silently.
"""
import argparse
import datetime as dt
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor

from collect import parse_ts, winner_of
from pm import api

SWITCH_SHARES_LAST = parse_ts("2026-04-28T11:30:00Z")  # last close in regime 'shares'
SWITCH_USDC_FIRST = parse_ts("2026-04-28T11:45:00Z")   # first close in regime 'usdc'
RECON_EVERY = 48  # fully reconcile every 48th market of a day (2 per series-day)

SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
    market_id TEXT PRIMARY KEY, condition_id TEXT, series TEXT, slug TEXT,
    window_end INTEGER, closed_time INTEGER, winner INTEGER, uma_status TEXT,
    auto_resolved INTEGER, fee_rate REAL, fee_exponent REAL, fee_regime TEXT,
    volume REAL, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entries (
    market_id TEXT, outcome_index INTEGER, fill_ts INTEGER, price REAL, size REAL,
    mid REAL, mid_ts INTEGER, PRIMARY KEY (market_id, outcome_index)
);
CREATE TABLE IF NOT EXISTS recon (
    market_id TEXT PRIMARY KEY, fee_regime TEXT, volume REAL, plain REAL, gross REAL, verdict TEXT
);
"""


def regime_for(close):
    if close <= SWITCH_SHARES_LAST:
        return "shares"
    if close >= SWITCH_USDC_FIRST:
        return "usdc"
    return "transition"


def trades_between(cid, t0, t1):
    out, off = [], 0
    while True:
        page = api.get(f"{api.DATA}/trades", dict(market=cid, limit=500, offset=off, takerOnly="true",
                                                  start=t0, end=t1))
        out += page
        if len(page) < 500:
            return [t for t in out if t0 <= int(t["timestamp"]) < t1]
        off += len(page)


def process(slug, ev, idx):
    (m,) = ev["markets"]
    fee = m.get("feeSchedule") or {}
    close = parse_ts(m["endDate"])
    row = dict(market_id=m["id"], condition_id=m["conditionId"], series=slug, slug=m["slug"],
               window_end=close, closed_time=parse_ts(ev.get("closedTime") or m.get("closedTime")),
               winner=winner_of(m), uma_status=m.get("umaResolutionStatus"),
               auto_resolved=int(bool(m.get("automaticallyResolved"))),
               fee_rate=fee.get("rate"), fee_exponent=fee.get("exponent"), fee_regime=regime_for(close),
               volume=float(m.get("volume") or 0), status="ok")
    entries, recon = [], None
    try:
        t9 = close - 540
        first = {}
        for t in sorted(trades_between(m["conditionId"], t9, t9 + 60), key=lambda t: int(t["timestamp"])):
            if t["side"] == "BUY":
                first.setdefault(int(t["outcomeIndex"]), t)
        if len(first) < 2:
            for t in sorted(trades_between(m["conditionId"], t9 + 60, close), key=lambda t: int(t["timestamp"])):
                if t["side"] == "BUY":
                    first.setdefault(int(t["outcomeIndex"]), t)
        for oi, t in first.items():
            entries.append((m["id"], oi, int(t["timestamp"]), float(t["price"]), float(t["size"]), None, None))
        if idx % RECON_EVERY == 0 and row["volume"] > 0:
            allt = api.taker_trades(m["conditionId"])
            plain = sum(float(t["size"]) for t in allt)
            gross = sum(float(t["size"]) / (1 - 0.144 * (1 - float(t["price"]))) if t["side"] == "BUY"
                        else float(t["size"]) for t in allt)
            tol = max(0.05, 0.001 * row["volume"])
            fits = [k for k, v in (("usdc", plain), ("shares", gross)) if abs(v - row["volume"]) <= tol]
            seen = fits[0] if len(fits) == 1 else "ambiguous" if fits else "neither"
            recon = (m["id"], row["fee_regime"], row["volume"], plain, gross,
                     "agree" if seen == row["fee_regime"] else "ambiguous" if seen == "ambiguous" else f"DISAGREE:{seen}")
    except Exception as e:
        row["status"] = f"error:{e}"[:300]
    if row["status"] == "ok" and row["winner"] is None:
        row["status"] = "unresolved"
    return row, entries, recon


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", nargs="+", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--db", default="data/t9.sqlite")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    db = sqlite3.connect(a.db)
    db.executescript(SCHEMA)
    have = {r[0] for r in db.execute("SELECT market_id FROM markets WHERE status NOT LIKE 'error:%'")}
    d0, d1 = dt.date.fromisoformat(a.start), dt.date.fromisoformat(a.end)
    with ThreadPoolExecutor(a.workers) as pool:
        for slug in a.series:
            sid = api.series_by_slug(slug)["id"]
            day = d0
            while day <= d1:
                evs = sorted(api.closed_events(sid, day.isoformat()), key=lambda e: e["endDate"])
                todo = [(i, e) for i, e in enumerate(evs) if e["markets"] and e["markets"][0]["id"] not in have]
                res = list(pool.map(lambda ie: process(slug, ie[1], ie[0]), todo))
                for row, entries, recon in res:
                    db.execute(f"INSERT OR REPLACE INTO markets ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
                               list(row.values()))
                    db.execute("DELETE FROM entries WHERE market_id=?", (row["market_id"],))
                    db.executemany("INSERT INTO entries VALUES (?,?,?,?,?,?,?)", entries)
                    if recon:
                        db.execute("INSERT OR REPLACE INTO recon VALUES (?,?,?,?,?,?)", recon)
                    have.add(row["market_id"])
                db.commit()
                bad = sorted({r["status"][:40] for r, _, _ in res if r["status"] != "ok"})
                rc = [x[5] for _, _, x in res if x]
                print(f"{slug} {day}: {len(todo)} markets, {sum(len(e) for _, e, _ in res)} entries, "
                      f"{sum(r['status'] != 'ok' for r, _, _ in res)} not ok {bad[:2]}, recon {rc}", flush=True)
                day += dt.timedelta(days=1)


if __name__ == "__main__":
    sys.exit(main())
