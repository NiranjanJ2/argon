"""Quick test — run with: .venv/bin/python3 test_google.py"""
from pathlib import Path
from nanobot.google.auth import GoogleAuth

auth = GoogleAuth(Path.home() / ".nanobot/workspace")
for account in ("school", "work", "personal"):
    try:
        creds = auth.get_credentials(account)
        print(f"{account}: OK (valid={creds.valid})")
    except Exception as e:
        print(f"{account}: ERROR — {e}")
