# Meme Coin - Polymarket

Read-only research on Polymarket's short-horizon crypto Up/Down markets.
**Paper only.** There is no wallet, no API key, and no code that places orders.

Order of work (from the Kalshi handoff):
1. Read-only data collection.
2. Measure round-trip cost.
3. Price vs realised win-rate curve, with Wilson intervals.
4. Only if 2 and 3 justify it: frozen spec in `specs/`, then paper trade.
5. Only after a spec passes out of sample: discuss going live.

## Scripts

| script | what it does |
|---|---|
| `collect.py` | Settled markets + every taker fill → `data/pm.sqlite` (gitignored). Checks fills against the market's reported volume. |
| `curve.py` | Entry price bucket → realised win rate, break-even, Wilson CI, edge with SEs clustered by window, ROI, fee, payoff ratio. |
| `roundtrip.py` | Live order-book snapshot: cost of buying both sides (pure trading cost) and of one favourite bet. |
| `verify_fee.py` | Checks the fee model against Polymarket's own wallet activity records. |
| `snapshot.py` | Once a minute, both books of every live 5m/15m market → `books/YYYY-MM-DD.csv`. |
| `book_summary.py` | Session summary (both-sides cost, favourite cost, depth by minutes-left) → Sheet tab `book_summary`. |

## Scheduled collection (`.github/workflows/books.yml`)

Hourly cron at :52 starts a 5h45m session (five 69-minute chunks); the concurrency group
queues the next trigger as successor. After each chunk the raw snapshots are gzipped and
committed to the `data` branch, and a summary row set is appended to the Sheet
"Meme Coin - Polymarket". Needs one secret: `GOOGLE_SERVICE_ACCOUNT_JSON` (the same
`meme-coin@meme-coin-496713` service account as the Kalshi sheet; the new sheet is shared with it).
For a second independent trigger, point cron-job.org at `workflow_dispatch` at :22 with a
fine-grained PAT that has **Actions: read and write only, no contents access**.

```
pip install requests
python collect.py --series btc-up-or-down-15m --start 2026-09-08 --end 2026-10-07
python curve.py --series btc-up-or-down-15m --min-left 8 --max-left 10
python roundtrip.py --within-min 20
```

## Facts verified against the API (2026-10-08)

- Series exist for 5m, 15m, hourly and 4h windows on BTC, ETH, SOL, XRP, DOGE, BNB, HYPE.
  BTC 5m trades ~$10M/day, BTC 15m ~$3.8M/day.
- Resolution: automatic, from the Chainlink TWAP stream (`automaticallyResolved: true`),
  not a UMA vote. Ties resolve **Up** ("greater than or equal").
- Markets are flagged `restricted: true` (geo-restricted; US access needs a decision by the user).
- Taker fee since ~2026-03-30 (`crypto_fees_v2`): `fee = shares × 0.07 × p × (1−p)`, takers only,
  added to the USDC paid. Per $ staked that is `0.07 × (1−p)`: 3.5% at 50c, 0.6% at 92c, 6.9% at 2c.
  Earlier markets used `0.25 × (p(1−p))²`. **Verified** with `verify_fee.py`: the fee is added to
  the USDC paid (`usdcSize = size×price + fee` to within $0.00001 on 13 of 14 wallets) and every share
  redeems for the full $1; no shares are withheld.
- Taker fills from `data-api /trades?takerOnly=true` sum exactly to gamma's `volume` on ~98% of
  markets; the rest are stored as `volume_mismatch` and excluded.
- Order books list bids and asks worst-first. Best prices must be taken with max/min.

## What is re-fetchable and what is not

Settled markets and fills go back to 2025 and can be re-pulled at any time, so they do not need
to live in a Sheet. **Order-book snapshots cannot be re-fetched**, so they are what a scheduled
collector must record.
