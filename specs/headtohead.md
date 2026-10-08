# Head-to-head spec: Kalshi "narrow slice" vs Polymarket, measured identically

Frozen 2026-10-08, before the April-September Polymarket data was pulled and before
any Kalshi data was scored with this code. Scoring is done by `h2h.py`, which has
NO command-line thresholds: every number below is a constant in that file, and a
change to either one is a new spec, committed separately, with a new date.

**Paper only.** Nothing in this spec authorises real money on Polymarket.

## 1. The rule being replicated

| | Kalshi (as live) | Polymarket (this test) |
|---|---|---|
| windows | 15-minute crypto | 15-minute crypto Up/Down (exists: `{btc,eth,sol,xrp,doge}-up-or-down-15m`) — **same horizon, no substitution** |
| assets | BTC ETH SOL XRP DOGE | BTC ETH SOL XRP DOGE |
| side | YES | **Up** (outcome index 0). Mapping YES→Up is a structural choice, see §6 |
| entry | T-9 min before close | first taker BUY fill on the side with `ts >= close - 540s` and `ts < close` |
| band | fill 90-96c | fill price p with `0.895 <= p < 0.965` (rounds to 90..96c) |
| hold | to settlement | to settlement |
| stake | $4, whole contracts | **units = floor(4.00 / p)** whole shares, both venues (4 units everywhere in the band) |

Other horizons that exist on Polymarket (5m, hourly, 4h) are NOT part of this test.

## 2. Calendar windows

- Polymarket fee regime `crypto_fees_v2` (rate 0.07, exponent 1) applies from ~2026-03-30.
  Polymarket sample: windows closing **2026-04-01 00:00 UTC to 2026-10-07 23:59 UTC**.
- Already seen before this spec (disclosed): 2026-09-08 to 2026-10-07, T-9 curve, all 7
  assets (results/SUMMARY.md: 90-95c edge +0.1 +/- 0.7 pts). **2026-04-01 to 2026-09-07 is
  the unseen holdout** and is reported as its own row.
- Head-to-head = the calendar overlap of the two venues' histories within the dates above,
  reported as such. Any longer Kalshi series is reported separately as context.
- The Kalshi live record (23 bets) is never used in the comparison.

## 3. Three numbers per venue per bucket

Per bet, with u units, fill p, mid m, fee f, won w in {0,1}:

1. **GROSS**: payoff `u*w - u*m`. Win rate vs the *midpoint* at entry; no fee, no spread.
   - Polymarket mid: last `clob /prices-history` point (fidelity 1 min) at or before the fill,
     no older than 120 s. Else the bet is excluded from GROSS only, and counted.
   - Kalshi mid: (yes_bid + yes_ask)/2 from the 1-minute candlestick containing the fill.
2. **NET of fees**: `u*w - u*m - f`.
3. **NET of fees and spread**: `u*w - u*p - f`, p = the actual fill price.

Fees, in dollars:
- Kalshi taker: `ceil(100 * 0.07 * u * p * (1-p)) / 100` per order (rounded UP to the cent).
- Polymarket: `u * 0.07 * p * (1-p)`, added to USDC paid (verified, verify_fee.py; records
  agree to $0.00001). Gas: none appears in USDC on trade or redeem records; counted as $0
  and stated as "observed in records", not assumed.

## 4. Buckets and statistics (identical both venues)

- Buckets: one per cent, 90, 91, ..., 96 (by round(100*p)), plus a band total.
- Per bucket: bets, distinct windows, avg fill, avg mid, **break-even win rate = (u*p + f)/u
  averaged = cost per unit, printed beside the win rate**, win rate, **Wilson 95%** on win rate,
  GROSS / NET-fee / NET-all per bet ($) and as % of stake (stake = u*p + f).
- SEs of per-bet P&L clustered by close time (window_end).
- n needed: bets required for a 95% interval on NET-all to exclude 0 at the observed mean,
  `(1.96 * sd_clustered_per_bet / |mean|)^2`, sd including the observed design effect.

## 5. Mirror test (run before any edge claim)

The same table for the opposite side (Polymarket Down / Kalshi NO) in the same band and rule.
If both sides lose by about the cost, the result is trading cost, not a market view.

## 6. Exclusions and structural differences, fixed in advance

Excluded and counted (never silently):
- Polymarket market not a clean 1/0 resolution, `umaResolutionStatus != resolved`, or
  `closedTime - endDate > 3600 s` (delayed). Also `volume_mismatch` markets (fills do not
  reconcile to reported volume).
- Kalshi markets not settled yes/no.

Reported, not papered over:
- Resolution: Polymarket 15m uses the Chainlink BTC/USD TWAP stream, auto-resolved, ties go Up;
  Kalshi settles on its own index. YES and Up are different propositions near the strike.
- Tick: Kalshi 1c. Polymarket book tick 0.01 in-band (0.001 near the extremes) but fills are
  reported at volume-averaged prices, so in-band fill prices are effectively continuous.
- Minimum size: Polymarket limit orders need >= 5 shares; 4 units may not be placeable live.
  The backtest uses 4 units for comparability; this is a feasibility caveat.
- Depth: neither venue's history has order books. Polymarket depth in-band at 8-10 min left is
  reported from `books` snapshots (from 2026-10-08 on) and labelled as a different period.
  The first fill's size is reported (median), since a large first fill's average price is worse
  than a 4-unit order would get, which makes NET-all conservative.
- Timestamps: all unix UTC; close = Polymarket `endDate` / Kalshi `close_time`.

## 7. Pass / fail for "Polymarket replicates the slice"

On the holdout (2026-04-01 to 2026-09-07), band total, Up side:
- PASS if NET-all per bet > 0 AND its clustered 95% interval excludes 0.
- Otherwise FAIL (including "not distinguishable from zero"). The n-needed figure is reported.
No other row decides it.

## Amendment 1 — 2026-10-08, before any holdout scoring

Found while validating the April data (no results had been computed): Polymarket changed
**how** the crypto taker fee is charged inside the window, between 2026-04-15 and 2026-04-29.

- Regime `shares` (earlier): no USDC fee; `0.072 * u * (1-p)` of the bought shares are withheld,
  so a win pays `u * (1 - 0.072*(1-p))`. Verified exactly on 5 of 5 wallets (activity BUY size
  vs REDEEM size). The data API reports these BUY sizes net of twice the withheld amount.
- Regime `usdc` (later): as §3, fee `u * 0.07 * p * (1-p)` added to USDC paid.

Each market's regime is identified from its own records (which accounting reconciles its fills
to its reported volume, `collect.py`); a market that reconciles under neither is excluded as
`volume_mismatch` and counted. In §3, for regime `shares`, the fee f in dollars is
`0.072 * u * (1-p) * w` (it is only lost when the bet wins), so NET-fee = `u*w*(1-0.072*(1-p)) - u*m`
and NET-all = `u*w*(1-0.072*(1-p)) - u*p`. Break-even win rate for a `shares` bet is
`p / (1 - 0.072*(1-p))`. Results are also reported split by regime.
Nothing else in the spec changes.
