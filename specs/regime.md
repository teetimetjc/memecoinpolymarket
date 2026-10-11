# Regime spec: what distinguishes the good period from the bad one?

Frozen 2026-10-11, before any candidate signal below was computed or compared with P&L.
Scored only by `regime.py`, whose constants copy this file; no command-line thresholds.
A change is a new dated amendment. **Paper only.**

## What is already known (disclosed, not hidden)
On Polymarket, the narrow-slice rule (specs/headtohead.md: Up side, first fill at or after T-9,
0.90 <= p < 0.96, 4 units, actual fees) earned about -$0.02 to -$0.03 per bet from 2026-04-01 to
07-18 and about +$0.02 to +$0.04 per bet from 07-19 to 10-07 (results/h2h_checks.md). That split was
seen before this spec. It is ONE switch, so any slowly trending variable will appear to explain it.
This spec is built to reject that.

## Bets
Exactly the headtohead.md P-fill bets, both sides kept separately (Up = the rule; Down = mirror),
5 assets, Polymarket 15m, NET-all P&L per bet as scored there.

## Candidate signals (all fixed now; all knowable at entry; no others will be tried)
- **S1 trailing volatility**: stdev of 15-minute log returns of the asset over the 96 windows
  (24 h) before the bet's window, from Polymarket's own `priceToBeat` (window start price) series.
  Uses only windows that STARTED at or before the bet's window start.
- **S2 entry spread**: p_up + p_down - 1 from the two sides' first fills when within 15 s
  (amendment 3 of headtohead.md). Bets without a pair are left out of S2 only.
- **S3 time of day**: UTC hour of the close, in four blocks: 00-05, 06-11, 12-17, 18-23.
- **S4 rule momentum**: mean NET-all P&L of all the rule's bets (same side, all 5 assets) that
  CLOSED in the 7 days before the bet's entry time (T-9). Needs at least 50 such bets.

## Windows
- **Discovery**: closes 2026-04-01 .. 07-31 (contains the whole bad stretch and ~2 weeks of good).
- **Holdout**: closes 2026-08-01 .. 10-07. Scored once, after discovery is written up and the
  thresholds below are committed.
- **Forward**: closes from 2026-10-08, collected prospectively; scored after >= 1,500 Up-side bets.

## Thresholds (set from DISCOVERY only, then frozen)
For S1, S2, S4: split at the discovery median of that signal (computed over discovery Up-side bets)
into LOW / HIGH. S3 keeps its four blocks. The discovery medians are written into an amendment to
this file, committed, before the holdout is scored.

## Test, per signal, Up side
Difference in mean NET-all per bet between favourable and unfavourable groups (favourable = the
group with the higher discovery mean; for S3, best block vs the other three), SEs clustered by
close time.

A signal **passes** only if ALL hold:
1. Discovery: difference has clustered t > 3.0 (Bonferroni-style for 4 signals).
2. **Within-period**: same sign of difference separately in 04-01..07-18 AND in 07-19..07-31+holdout,
   so it is not just marking the calendar.
3. **Week-level**: across calendar weeks in discovery+holdout, the weekly share of bets in the
   favourable group correlates positively with weekly NET-all per bet (Spearman rho > 0.3).
4. Holdout: same direction, clustered t > 2.0, and the favourable group's NET-all per bet > 0
   with its 95% interval excluding 0.
5. Mirror (Down side): reported, not required; a signal that helps both sides equally is a
   market-wide state, which is fine, but it is labelled as such.

A pass makes the filtered rule a candidate for forward paper trading only. Nothing here authorises
real money.
