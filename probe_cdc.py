"""Read-only probe: what does Crypto.com expose publicly for event / prediction contracts?

Run from GitHub Actions (probe_cdc.yml). GET requests only, no keys.
Prints what it found AND what it did not, so an empty answer is explicit.
"""
import collections
import json
import re

import requests

S = requests.Session()
S.headers["User-Agent"] = "pm-research/0.1 (read-only)"


def get(url, **params):
    try:
        r = S.get(url, params=params or None, timeout=20)
        return r.status_code, r
    except Exception as e:
        return None, repr(e)[:150]


print("== 1. Exchange v1 public/get-instruments")
code, r = get("https://api.crypto.com/exchange/v1/public/get-instruments")
print("status", code)
inst = []
if code == 200:
    d = r.json()
    inst = (d.get("result") or {}).get("data") or []
    print("instruments:", len(inst))
    if inst:
        print("fields:", sorted(inst[0].keys()))
    for field in ("inst_type", "product_type", "instrument_type", "type", "category"):
        c = collections.Counter(str(i.get(field)) for i in inst if field in i)
        if c:
            print(f"  {field}: {dict(c.most_common(20))}")
    pat = re.compile(r"event|predict|binary|updown|up_down|outcome|yes|strike|option", re.I)
    hits = [i for i in inst if pat.search(json.dumps(i))]
    print("instruments mentioning event/predict/binary/strike/option:", len(hits))
    for i in hits[:15]:
        print("  ", json.dumps(i)[:300])
    nonspot = [i for i in inst if str(i.get("inst_type", "")).upper() not in ("CCY_PAIR", "PERPETUAL_SWAP", "FUTURE")]
    print("instruments of other inst_types:", len(nonspot))
    for i in nonspot[:10]:
        print("  ", json.dumps(i)[:300])
else:
    print(str(r)[:300] if not hasattr(r, "text") else r.text[:300])

print("\n== 2. Settlement-price endpoint by instrument type")
for t in ("EVENT", "PREDICTION", "BINARY", "OPTION", "FUTURE"):
    code, r = get("https://api.crypto.com/exchange/v1/public/get-expired-settlement-price", instrument_type=t, page=1)
    body = r.text[:200] if hasattr(r, "text") else str(r)
    print(f"  instrument_type={t}: status {code} {body}")

print("\n== 3. Prediction front-ends: API hosts they reference")
for url in ("https://crypto.com/us/prediction", "https://crypto.com/us/predictions", "https://og.com",
            "https://www.og.com", "https://crypto.com/prediction"):
    code, r = get(url)
    if not hasattr(r, "text"):
        print(f"  {url}: {r}")
        continue
    hosts = sorted(set(re.findall(r"https?://[a-z0-9.-]*(?:api|gateway|cdna|predict|event)[a-z0-9.-]*\.[a-z]{2,}", r.text, re.I)))
    print(f"  {url}: status {code}, {len(r.text)} bytes, api-like hosts: {hosts[:12]}")
