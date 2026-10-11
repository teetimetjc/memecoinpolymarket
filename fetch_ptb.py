"""Window start prices (Polymarket `priceToBeat`) for the 15m series, for regime signal S1.

    python fetch_ptb.py --start 2026-03-31 --end 2026-10-07

Read-only. One gamma call per series-day. Writes data/ptb.sqlite (series, window_end, price).
Windows with no priceToBeat are counted and reported, not dropped silently.
"""
import argparse
import datetime as dt
import sqlite3

from collect import parse_ts
from pm import api

SERIES = ("btc-up-or-down-15m", "eth-up-or-down-15m", "sol-up-or-down-15m",
          "xrp-up-or-down-15m", "doge-up-or-down-15m")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    a = ap.parse_args()
    db = sqlite3.connect("data/ptb.sqlite")
    db.execute("CREATE TABLE IF NOT EXISTS ptb (series TEXT, window_end INTEGER, price REAL, "
               "PRIMARY KEY (series, window_end))")
    for slug in SERIES:
        sid = api.series_by_slug(slug)["id"]
        day, missing, got = dt.date.fromisoformat(a.start), 0, 0
        while day <= dt.date.fromisoformat(a.end):
            for e in api.closed_events(sid, day.isoformat()):
                p = (e.get("eventMetadata") or {}).get("priceToBeat")
                end = parse_ts(e["markets"][0]["endDate"]) if e.get("markets") else None
                if p is None or end is None:
                    missing += 1
                    continue
                db.execute("INSERT OR REPLACE INTO ptb VALUES (?,?,?)", (slug, end, float(p)))
                got += 1
            db.commit()
            day += dt.timedelta(days=1)
        print(f"{slug}: {got} windows with a start price, {missing} without", flush=True)


if __name__ == "__main__":
    main()
