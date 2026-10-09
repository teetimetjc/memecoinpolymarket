# Head-to-head: post-scoring checks (2026-10-09)

These are checks run AFTER `h2h.py` (results/h2h.md), to find out what the table means.
They do not change any scored number.

Overlap window: 2026-07-19 to 2026-10-07 (the Kalshi sheet starts 2026-07-19).

## 1. Same windows on both venues (asset + close time), YES/Up, band 90-96c
| | bets | avg fill | win rate | break-even | NET all $/bet ± 95% |
|---|---|---|---|---|---|
| Kalshi K-fill | 1,395 | 0.9232 | 94.98% | 92.95% | +0.082 ± 0.053 |
| Polymarket P-fill | 1,395 | 0.9288 | 94.77% | 93.35% | +0.057 ± 0.054 |

- Settlement agreed on 1,388 / 1,395 (99.5%): same outcome, mapping of YES/Up verified both ways.
  NO/Down: 1,194 / 1,205.
- Win rates are the same (these are the same events). The difference is the price paid.

## 2. The price gap is mostly timing, not venue
Median Kalshi first fill is 0 s after T-9; Polymarket's is ~7 s after, and favourites drift up
toward the close. Kalshi minus Polymarket fill price, by gap between the two fill times:

| fill-time gap | pairs | median K - P |
|---|---|---|
| 0-3 s | 388 | -0.2c |
| 3-10 s | 514 | -0.6c |
| 10-30 s | 242 | -0.6c |
| 30 s+ | 251 | -1.1c |

At the same moment Kalshi is ~0.2c cheaper (~$0.008 per 4-unit bet): a small venue difference.

## 3. Period, not venue, drives the result
Polymarket, same rule, all bets, NET all $/bet:
| period | Up | Down |
|---|---|---|
| 2026-04-01 .. 07-18 (no Kalshi data exists) | -0.021 ± 0.045 | -0.030 ± 0.047 |
| 2026-07-19 .. 10-07 (overlap) | +0.038 ± 0.044 | +0.019 ± 0.051 |

The Kalshi backtest only covers the second period, which was good for 90-96c favourites on
BOTH venues. The earlier period, visible only on Polymarket, lost money on both sides.
