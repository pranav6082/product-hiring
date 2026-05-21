"""Debug: find the right LinkedIn DOM selectors."""
import json, os
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()
COOKIES_FILE = os.environ.get("LINKEDIN_COOKIES_FILE", "linkedin_cookies.json")

with open(COOKIES_FILE) as f:
    raw_cookies = json.load(f)
cookies = [{**c, "domain": ".linkedin.com"} if c.get("domain","").endswith("linkedin.com") else c for c in raw_cookies]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--no-sandbox","--disable-dev-shm-usage"])
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 900},
    )
    ctx.add_cookies(cookies)
    page = ctx.new_page()
    page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    # Wait for full network idle to let LinkedIn JS render
    page.goto("https://www.linkedin.com/feed/", wait_until="networkidle", timeout=90000)
    page.wait_for_timeout(8000)

    print(f"URL: {page.url}")
    print(f"Title: {page.title()}")

    html = page.content()
    print(f"Page size: {len(html)} bytes")

    # Try different selectors
    selectors = [
        'div[data-urn]',
        'div[data-urn*="activity"]',
        'div.feed-shared-update-v2',
        'li.occludable-update',
        '[class*="occludable"]',
        '[class*="feed-shared"]',
        '[class*="update-components"]',
        'article',
        'main',
        '.scaffold-layout__main',
    ]
    for sel in selectors:
        try:
            els = page.query_selector_all(sel)
            if els:
                print(f"  FOUND {len(els):3d} elements: {sel}")
                cls = els[0].get_attribute("class") or ""
                urn = els[0].get_attribute("data-urn") or ""
                print(f"    class={cls[:100]}  urn={urn[:60]}")
        except Exception as e:
            print(f"  ERROR {sel}: {e}")

    # Save full HTML
    with open("/tmp/linkedin_feed.html", "w") as f:
        f.write(html[:100000])
    print(f"\nFirst 100KB saved to /tmp/linkedin_feed.html")
    browser.close()
