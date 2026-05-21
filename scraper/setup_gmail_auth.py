"""
One-time setup: generates GMAIL_REFRESH_TOKEN for the scraper.

Run ONCE on your Mac:
  pip install google-auth-oauthlib
  python setup_gmail_auth.py

It will open a browser, ask you to sign in with your Google account,
and print the refresh token to paste into GitHub Secrets.
"""

from google_auth_oauthlib.flow import InstalledAppFlow
import json
import os

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

CLIENT_CONFIG = {
    "installed": {
        "client_id": input("Paste GMAIL_CLIENT_ID: ").strip(),
        "client_secret": input("Paste GMAIL_CLIENT_SECRET: ").strip(),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
    }
}

flow = InstalledAppFlow.from_client_config(CLIENT_CONFIG, SCOPES)
creds = flow.run_local_server(port=0)

print("\n" + "="*60)
print("Add these to GitHub Actions secrets:")
print(f"  GMAIL_REFRESH_TOKEN = {creds.refresh_token}")
print("="*60)
