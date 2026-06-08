"""
Probe the Opus upload API to find what it accepts.
Run: python test_upload.py
"""
import requests, base64

# Tiny valid PNG
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwADhQGAWjR9awAAAABJRU5ErkJggg=="
)

URL = "https://dev.opusoperations.com/upload/upload-file"

attempts = [
    ("type=image",   {"file": ("test.png", TINY_PNG, "image/png"),  "type": (None, "image")}),
    ("type=png",     {"file": ("test.png", TINY_PNG, "image/png"),  "type": (None, "png")}),
    ("type=jpg",     {"file": ("test.jpg", TINY_PNG, "image/jpeg"), "type": (None, "jpg")}),
    ("type=photo",   {"file": ("test.png", TINY_PNG, "image/png"),  "type": (None, "photo")}),
    ("no type field",{"file": ("test.png", TINY_PNG, "image/png")}),
]

for label, files in attempts:
    r = requests.post(URL, files=files)
    print(f"{label:20s}  status={r.status_code}  response={r.text[:120]}")
