"""
Run this to diagnose the blank Test2 submission.
It fetches the latest submission and the full raw form definition.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
import requests
from credentials import get_cached_token, DOFORMS_BASE, FORM_KEY, PROJECT_KEY

token = get_cached_token()
headers = {"Authorization": f"Bearer {token}"}

# ── 1. Fetch the last submission to see what was stored ──────────
print("=" * 60)
print("LATEST SUBMISSION FIELDS")
print("=" * 60)
r = requests.get(
    f"{DOFORMS_BASE}/api/v2/submissions",
    headers=headers,
    params={"formKey": FORM_KEY, "limit": 1, "orderby": "desc"}
)
print(f"Status: {r.status_code}")
if r.status_code == 200:
    subs = r.json()
    if subs:
        sub = subs[0] if isinstance(subs, list) else subs
        print(json.dumps(sub, indent=2))
    else:
        print("No submissions found")
else:
    print(r.text[:500])

# ── 2. Fetch full form definition to see section structure ───────
print("\n" + "=" * 60)
print("FULL FORM DEFINITION (raw)")
print("=" * 60)
r2 = requests.get(
    f"{DOFORMS_BASE}/api/v2/forms/Incident_Report_Test2",
    headers=headers
)
print(f"Status: {r2.status_code}")
if r2.status_code == 200:
    print(json.dumps(r2.json(), indent=2))
else:
    print(r2.text[:1000])
