"""Check the fee model against Polymarket's own wallet activity records.

    python verify_fee.py [--n 20]

For the most recent settled BTC 15m market, take taker BUY fills from wallets
with exactly one fill in that market, and compare:
  activity usdcSize  vs  size*price + size*rate*(p(1-p))**exponent
  position / redeem size  vs  fill size (no shares withheld)
Prints every comparison; a mismatch is printed, not hidden.
"""
import argparse
import collections

from pm import api


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    a = ap.parse_args()

    sid = api.series_by_slug("btc-up-or-down-15m")["id"]
    ev = api.get(f"{api.GAMMA}/events", dict(series_id=sid, closed="true", limit=1, order="endDate", ascending="false"))[0]
    m = ev["markets"][0]
    rate, expo = m["feeSchedule"]["rate"], m["feeSchedule"]["exponent"]
    print(f"{m['slug']} fee rate={rate} exponent={expo}")

    allf, off = [], 0
    while True:
        page = api.get(f"{api.DATA}/trades", dict(market=m["conditionId"], limit=500, offset=off, takerOnly="false"))
        allf += page
        off += len(page)
        if len(page) < 500:
            break
    per_wallet = collections.Counter(t["proxyWallet"] for t in allf)
    cands = [t for t in api.taker_trades(m["conditionId"])
             if t["side"] == "BUY" and per_wallet[t["proxyWallet"]] == 1][: a.n]
    if not cands:
        print("NO CANDIDATES: no wallet had exactly one taker BUY fill in this market.")
        return
    worst = 0.0
    for t in cands:
        p, size = float(t["price"]), float(t["size"])
        act = api.get(f"{api.DATA}/activity", dict(user=t["proxyWallet"], market=m["conditionId"]))
        buys = [x for x in act if x.get("type") == "TRADE" and x.get("side") == "BUY"]
        redeems = [x for x in act if x.get("type") == "REDEEM"]
        if len(buys) != 1:
            print(f"skip {t['proxyWallet'][:10]}: {len(buys)} buy records")
            continue
        model = size * p + size * rate * (p * (1 - p)) ** expo
        paid = float(buys[0]["usdcSize"])
        worst = max(worst, abs(paid - model))
        red = float(redeems[0]["size"]) if redeems else None
        print(f"{t['outcome']:>4} {size:>12.4f} @ {p:.4f}  paid {paid:.6f}  model {model:.6f}  "
              f"diff {paid - model:+.6f}  redeemed {red}")
    print(f"largest |paid - model| = ${worst:.6f}")


if __name__ == "__main__":
    main()
