"""
Run this ONCE locally to export your LinkedIn session cookies.
Requires: pip install playwright && playwright install chromium

Usage: python export_cookies.py
It opens a visible browser, you log in to LinkedIn, press Enter, cookies are saved.
"""

import json
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto("https://www.linkedin.com/login")

    print("\nLog into LinkedIn in the browser window that just opened.")
    print("Once you are on your feed and posts are visible, come back here and press Enter...")
    input()

    # Save full storage state (cookies + localStorage + sessionStorage)
    ctx.storage_state(path="linkedin_storage.json")

    # Also save cookies separately for backward compat
    cookies = ctx.cookies()
    with open("linkedin_cookies.json", "w") as f:
        json.dump(cookies, f, indent=2)

    browser.close()
    print(f"Saved {len(cookies)} cookies to linkedin_cookies.json")
    print("Saved full storage state to linkedin_storage.json")
    print("Copy both files to the scraper directory on the Oracle VM.")
