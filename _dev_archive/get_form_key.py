"""
Run this once from the version5 folder:
    python get_form_key.py

It will print the form key for Incident_Report_Test1.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import requests
from credentials import get_cached_token,  DOFORMS_BASE
from utils import PROJECT_KEY

token = get_cached_token()
print(f"Token: {token[:20]}...")
headers = {"Authorization": f"Bearer {token}"}

endpoints = [
    # Try project-scoped forms list (recommended for specific projects)
    (f"{DOFORMS_BASE}/api/v2/projects/{PROJECT_KEY}/forms", {}),
    # Try fetching form directly by name
    (f"{DOFORMS_BASE}/api/v2/forms/Incident_Report_Test1", {}),
    (f"{DOFORMS_BASE}/api/v2/forms/Incident_Report_Test", {}),
]

for url, params in endpoints:
    print(f"\nGET {url}")
    r = requests.get(url, headers=headers, params=params)
    print(f"Status: {r.status_code}  |  Body: {r.text[:300]}")
    if r.status_code == 200 and r.text.strip():
        try:
            data = r.json()
            # Could be a list of forms or a single form object
            if isinstance(data, list):
                print(f"\nForms found ({len(data)}):")
                for f in data:
                    name = f.get("name", "")
                    key  = f.get("key", "")
                    print(f"  {name:<45}  key = {key}")
            elif isinstance(data, dict):
                print(f"\nForm: {data.get('name')}  key = {data.get('key')}")
        except Exception as e:
            print(f"Parse error: {e}")

