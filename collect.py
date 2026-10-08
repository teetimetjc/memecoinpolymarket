"""Collect settled Polymarket crypto Up/Down markets and their taker fills.

Read-only. Writes to data/pm.sqlite (gitignored).

    python collect.py --series btc-up-or-down-15m eth-up-or-down-15m --start 2026-09-08 --end 2026-10-07

Every market fetched is recorded, including ones that are excluded from
analysis, with the reason in `markets.status`. Nothing is dropped silently.
Fills are checked against the market's own reported volume; a mismatch is
stored as status='volume_mismatch' rather than trusted.
"""
import argparse
import datetime as dt
import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor

from pm import api

DB = "data/pm.sqlite"  # override with --db

SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
    market_id     TEXT PRIMARY KEY,
    condition_id  TEXT NOT NULL,
    series        TEXT NOT NULL,
    slug          TEXT NOT NULL,
    window_start  INTEGER,          -- unix seconds
    window_end    INTEGER NOT NULL, -- unix seconds
    outcomes      TEXT,             -- JSON, e.g. ["Up","Down"]
    winner        INTEGER,          -- outcome index that paid $1; NULL if not cleanly resolved
    volume        REAL,             -- gamma-reported volume (shares)
    taker_shares  REAL,             -- sum of taker fill sizes we collected
    n_fills       INTEGER,
    fee_type      TEXT,
    fee_rate      REAL,
    fee_exponent  REAL,
    uma_status    TEXT,
    auto_resolved INTEGER,
    closed_time   INTEGER,          -- unix seconds the market was closed/resolved
    fee_regime    TEXT,             -- usdc | shares | none | NULL (unreconciled)
    status        TEXT NOT NULL     -- ok | unresolved | volume_mismatch | error:...
);
CREATE TABLE IF NOT EXISTS fills (
    market_id     TEXT NOT NULL,
    outcome_index INTEGER NOT NULL,
    side          TEXT NOT NULL,    -- BUY or SELL, from the taker's point of view
    price         REAL NOT NULL,
    size          REAL NOT NULL,    -- shares
    ts            INTEGER NOT NULL,
    tx            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS fills_market ON fills(market_id);
"""


def parse_ts(s):
    """Accepts '2026-10-08T18:15:56Z' and gamma's '2026-10-08 18:15:56+00'."""
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    if s.endswith(("+00", "-00")):
        s += ":00"
    return int(dt.datetime.fromisoformat(s).timestamp())


def winner_of(m):
    """Index of the outcome that settled at $1, or None if not a clean 1/0 resolution."""
    try:
        prices = [float(p) for p in json.loads(m["outcomePrices"])]
    except (KeyError, TypeError, ValueError):
        return None
    if sorted(prices) != [0.0, 1.0]:
        return None
    return prices.index(1.0)


def process(series_slug, ev):
    (m,) = ev["markets"]  # Up/Down events have exactly one market
    fee = m.get("feeSchedule") or {}
    row = dict(
        market_id=m["id"], condition_id=m["conditionId"], series=series_slug, slug=m["slug"],
        window_start=parse_ts(m.get("eventStartTime") or ev.get("startTime")),
        window_end=parse_ts(m["endDate"]), outcomes=m.get("outcomes"),
        winner=winner_of(m), volume=float(m.get("volume") or 0),
        taker_shares=None, n_fills=None,
        fee_type=m.get("feeType"), fee_rate=fee.get("rate"), fee_exponent=fee.get("exponent"),
        uma_status=m.get("umaResolutionStatus"), auto_resolved=int(bool(m.get("automaticallyResolved"))),
        closed_time=parse_ts(ev.get("closedTime") or m.get("closedTime")), fee_regime=None,
        status="ok",
    )
    fills = []
    try:
        trades = api.taker_trades(m["conditionId"])
    except Exception as e:  # recorded, not swallowed
        row["status"] = f"error:{e}"[:300]
        return row, fills
    for t in trades:
        fills.append((m["id"], int(t["outcomeIndex"]), t["side"], float(t["price"]),
                      float(t["size"]), int(t["timestamp"]), t["transactionHash"]))
    row["n_fills"] = len(fills)
    row["taker_shares"] = sum(f[4] for f in fills)
    # Fee regime, identified per market by which accounting reconciles to gamma's volume
    # (both verified against wallet /activity records, see verify_fee.py):
    #   usdc  : fee = 0.07*p*(1-p) per share added to USDC paid; trade sizes are gross.
    #   shares: 0.072*(1-p) of each bought share withheld; the data API reports BUY sizes
    #           net of twice that, so gross = size / (1 - 0.144*(1-p)). Until ~late April 2026.
    tol = max(1.0, 0.001 * row["volume"])
    gross_shares = sum(f[4] / (1 - 0.144 * (1 - f[3])) if f[2] == "BUY" else f[4] for f in fills)
    if not row["fee_rate"]:
        row["fee_regime"] = "none" if abs(row["taker_shares"] - row["volume"]) <= tol else None
    elif abs(row["taker_shares"] - row["volume"]) <= tol:
        row["fee_regime"] = "usdc"
    elif abs(gross_shares - row["volume"]) <= tol:
        row["fee_regime"] = "shares"
    else:
        row["fee_regime"] = None
    if row["winner"] is None:
        row["status"] = "unresolved"
    elif row["fee_regime"] is None:
        row["status"] = "volume_mismatch"
    return row, fills


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", nargs="+", required=True)
    ap.add_argument("--start", required=True, help="first UTC day, YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="last UTC day, inclusive")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--db", default=DB)
    a = ap.parse_args()

    db = sqlite3.connect(a.db)
    db.executescript(SCHEMA)
    cols = {r[1] for r in db.execute("PRAGMA table_info(markets)")}
    for col, typ in (("closed_time", "INTEGER"), ("fee_regime", "TEXT")):
        if col not in cols:  # databases created before these columns existed
            db.execute(f"ALTER TABLE markets ADD COLUMN {col} {typ}")
    have = {r[0] for r in db.execute("SELECT market_id FROM markets WHERE status NOT LIKE 'error:%'")}

    d0, d1 = dt.date.fromisoformat(a.start), dt.date.fromisoformat(a.end)
    with ThreadPoolExecutor(a.workers) as pool:
        for slug in a.series:
            sid = api.series_by_slug(slug)["id"]
            day = d0
            while day <= d1:
                evs = [e for e in api.closed_events(sid, day.isoformat())
                       if e["markets"] and e["markets"][0]["id"] not in have]
                results = list(pool.map(lambda e: process(slug, e), evs))
                for row, fills in results:
                    db.execute("DELETE FROM fills WHERE market_id=?", (row["market_id"],))
                    db.execute(f"INSERT OR REPLACE INTO markets ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
                               list(row.values()))
                    db.executemany("INSERT INTO fills VALUES (?,?,?,?,?,?,?)", fills)
                    have.add(row["market_id"])
                db.commit()
                bad = [r["status"] for r, _ in results if r["status"] != "ok"]
                # Always say what happened, including when nothing did.
                print(f"{slug} {day}: {len(evs)} new markets, {sum(len(f) for _, f in results)} fills, "
                      f"{len(bad)} not ok {sorted(set(bad))[:3] if bad else ''}", flush=True)
                day += dt.timedelta(days=1)
    db.close()


if __name__ == "__main__":
    sys.exit(main())
