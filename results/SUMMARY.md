# Results, 2026-10-08

Data: 7 coins x 15-minute Up/Down markets, 2026-09-08 to 2026-10-07 (UTC).
20,160 settled markets, 4.26M taker fills. 288 markets (1.4%; HYPE 6%) excluded as
`volume_mismatch` (fills do not sum to reported volume).

## Round trip (step 2)
Live books 2026-10-08 18:34 UTC (`roundtrip_20261008T1834.csv`): buying both sides of a ~50/50
15m market costs ~4.5% of stake (3.5% fee + 1c spread). Kalshi: ~25%.
One favourite bet at ~90c gives up ~1% versus the midpoint. A single snapshot; the `books`
workflow measures it continuously.

Fee verified against wallet records (`verify_fee.py`): fee = shares x 0.07 x p x (1-p), added to USDC paid.

## Curve (step 3), one bet per market side at T-9 (`curve_15m_T9_20260908_20261007.csv`)
Realised win rate = price paid in every 5c bucket, within noise. Overall ROI -1.3% = the fee.

| entry price | bets | win rate | break-even | edge (pts) | ROI |
|---|---|---|---|---|---|
| 0-5c | 999 | 3.4% | 3.1% | +0.3 +/- 0.7 | +10% (noise) |
| 5-10c | 2,099 | 7.4% | 7.6% | -0.2 +/- 0.7 | -2% |
| 10-15c | 2,253 | 11.1% | 12.8% | -1.7 +/- 0.8 | -13% |
| 90-95c | 2,317 | 92.4% | 92.3% | +0.1 +/- 0.7 | +0.1% |
| 95-100c | 2,101 | 97.8% | 97.5% | +0.3 +/- 0.5 | +0.3% |

At 92c a win pays ~$0.08 per $1 and a loss costs $1 (12:1). Up and Down sides both ~0.
Fills method (every real taker fill): takers lose 1.3% overall; fills at 70-90c lose 3-4% (t~-3).

## Verdict
Trading is ~5x cheaper than Kalshi, but there is no detectable mispricing at T-9 in one month
of data. The Kalshi favourite-longshot curve does not reproduce here. Steps 2-3 do not yet
justify a frozen spec.
