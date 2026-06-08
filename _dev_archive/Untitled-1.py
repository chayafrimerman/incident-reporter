"""
Run this once from the version5 folder:
    python get_form_key.py

It will print the form key for Incident_Report_Test1.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import requests
from credentials import get_cached_token, PROJECT_KEY, DOFORMS_BASE

token = get_cached_token()
headers = {"Authorization": f"Bearer {token}"}

# List all forms in the project
r = requests.get(f"{DOFORMS_BASE}/api/v2/forms", headers=headers,
                 params={"projectKey": PROJECT_KEY, "limit": 200})
r.raise_for_status()

forms = r.json() if isinstance(r.json(), list) else r.json().get("list", r.json())

print(f"\nAll forms in project ({PROJECT_KEY}):\n")
for f in forms:
    name = f.get("name", "")
    key  = f.get("key", "")
    print(f"  {name:<45} key = {key}")
    if "test" in name.lower():
        print(f"    ^^^ MATCH")