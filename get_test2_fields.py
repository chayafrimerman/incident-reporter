import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import requests
from credentials import get_cached_token, DOFORMS_BASE

token = get_cached_token()
r = requests.get(f"{DOFORMS_BASE}/api/v2/forms/Incident_Report_Test2",
                 headers={"Authorization": f"Bearer {token}"})
import json
fields = r.json().get("fields", [])
print(f"Status: {r.status_code}  |  {len(fields)} fields\n")
def print_fields(fields, indent=0):
    for f in fields:
        label = f.get('options', {}).get('label', {})
        label_str = label.get('en') or ''
        print(" " * indent + f"name={f['name']:<35} type={f['data']:<10} label={label_str}")
        # Recurse into nested fields
        if f.get('fields'):
            print_fields(f['fields'], indent + 4)

print_fields(fields)
