"""
Agent 1 - Zillow FSBO Scraper

NOTE: Zillow aggressively blocks automated scraping. This module includes
stealth measures, but may still require additional techniques such as:
  - Residential rotating proxies
  - Real browser fingerprint spoofing beyond navigator.webdriver
  - CAPTCHA solving services (2captcha, Anti-Captcha, etc.)
  - Longer delays / session warm-up browsing
  - Cookie injection from a real authenticated session

Use responsibly and in accordance with Zillow's Terms of Service.
"""

import asyncio
import json
import random
import re
import time
import urllib.parse
from typing import Any

# ---------------------------------------------------------------------------
# Stealth init script injected into every page context
# ---------------------------------------------------------------------------
STEALTH_INIT_SCRIPT = """
(function () {
  // Hide webdriver flag
  Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined,
    configurable: true,
  });

  // Overwrite plugins to look non-empty
  Object.defineProperty(navigator, 'plugins', {
    get: () => {
      const arr = [1, 2, 3, 4, 5];
      arr.__proto__ = PluginArray.prototype;
      return arr;
    },
    configurable: true,
  });

  // Overwrite languages
  Object.defineProperty(navigator, 'languages', {
    get: () => ['en-US', 'en'],
    configurable: true,
  });

  // Spoof Chrome runtime object so Chromium checks pass
  window.chrome = window.chrome || {};
  window.chrome.runtime = window.chrome.runtime || {};

  // Permissions API spoof
  const originalQuery = window.navigator.permissions
    ? window.navigator.permissions.query.bind(window.navigator.permissions)
    : null;
  if (originalQuery) {
    window.navigator.permissions.query = (parameters) =>
      parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : originalQuery(parameters);
  }

  // Remove automation-related properties
  delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
  delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
  delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
})();
"""

# ---------------------------------------------------------------------------
# A pool of realistic desktop User-Agent strings as a fallback
# ---------------------------------------------------------------------------
_FALLBACK_USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
        "Gecko/20100101 Firefox/125.0"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.4.1 Safari/605.1.15"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
]


def _get_user_agent() -> str:
    """Return a random User-Agent, preferring fake_useragent if available."""
    try:
        from fake_useragent import UserAgent  # type: ignore

        ua = UserAgent()
        return ua.chrome
    except Exception:
        return random.choice(_FALLBACK_USER_AGENTS)


def _build_search_url(location: str) -> str:
    """
    Build a Zillow FSBO search URL for the given location string.

    The searchQueryState JSON blob is the canonical way Zillow encodes
    all filter state in its URL.  We set isForSaleByOwner=true.
    """
    # Normalise location into a URL-friendly slug (e.g. "Austin, TX" -> "Austin-TX")
    slug = re.sub(r"[,\s]+", "-", location.strip()).strip("-")

    search_query_state = {
        "pagination": {},
        "usersSearchTerm": location,
        "mapBounds": {},
        "filterState": {
            "fsbo": {"value": True},
            "nc": {"value": False},
            "fore": {"value": False},
            "cmsn": {"value": False},
            "auc": {"value": False},
            "pmf": {"value": False},
            "pf": {"value": False},
            "fr": {"value": False},
            "ah": {"value": True},
        },
        "isListVisible": True,
    }

    params = urllib.parse.urlencode(
        {"searchQueryState": json.dumps(search_query_state, separators=(",", ":"))},
        quote_via=urllib.parse.quote,
    )
    return f"https://www.zillow.com/homes/for_sale/{slug}/?{params}"


class ZillowScraper:
    """
    Playwright-based scraper that collects FSBO listings from Zillow.

    Config keys used
    ----------------
    location      : str  - city/state string, e.g. "Austin, TX"
    max_pages     : int  - maximum search-result pages to visit (default 3)
    min_delay     : float - minimum random delay in seconds (default 2.0)
    max_delay     : float - maximum random delay in seconds (default 5.0)
    headless      : bool - run browser headlessly (default True)
    """

    def __init__(self, config: dict[str, Any], logger: Any) -> None:
        self.config = config
        self.logger = logger

        self.location: str = config.get("location", "Austin, TX")
        self.max_pages: int = int(config.get("max_pages", 3))
        self.min_delay: float = float(config.get("min_delay", 2.0))
        self.max_delay: float = float(config.get("max_delay", 5.0))
        self.headless: bool = bool(config.get("headless", True))

    # ------------------------------------------------------------------
    # Public entry-point
    # ------------------------------------------------------------------

    def run(self) -> list[dict[str, Any]]:
        """Launch the browser and collect FSBO listings. Returns raw dicts."""
        from playwright.sync_api import sync_playwright  # type: ignore

        listings: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=self.headless)
                context = self._create_context(pw, browser)
                page = context.new_page()
                self._configure_page(page)

                listings = self._scrape_all_pages(page, seen_urls)

                context.close()
                browser.close()
        except Exception as exc:
            self.logger.error("Fatal error during scrape session: %s", exc)

        self.logger.info("Scraper finished. Total listings collected: %d", len(listings))
        return listings

    # ------------------------------------------------------------------
    # Browser / context helpers
    # ------------------------------------------------------------------

    def _create_context(self, pw: Any, browser: Any) -> Any:  # noqa: ARG002
        """Create a browser context with randomised viewport and headers."""
        viewport_width = random.randint(1200, 1400)
        viewport_height = random.randint(800, 900)
        user_agent = _get_user_agent()

        context = browser.new_context(
            viewport={"width": viewport_width, "height": viewport_height},
            user_agent=user_agent,
            locale="en-US",
            timezone_id="America/Chicago",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;"
                    "q=0.9,image/avif,image/webp,*/*;q=0.8"
                ),
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Upgrade-Insecure-Requests": "1",
            },
            java_script_enabled=True,
        )
        return context

    def _configure_page(self, page: Any) -> None:
        """Inject stealth script and attach request interception if needed."""
        page.add_init_script(STEALTH_INIT_SCRIPT)

    # ------------------------------------------------------------------
    # Core scraping logic
    # ------------------------------------------------------------------

    def _scrape_all_pages(
        self, page: Any, seen_urls: set[str]
    ) -> list[dict[str, Any]]:
        """Navigate through search result pages and collect listings."""
        all_listings: list[dict[str, Any]] = []
        start_url = _build_search_url(self.location)

        self.logger.info("Starting FSBO search for '%s'", self.location)
        self.logger.info("Initial URL: %s", start_url)

        try:
            page.goto(start_url, wait_until="domcontentloaded", timeout=30_000)
        except Exception as exc:
            self.logger.error("Failed to load initial search page: %s", exc)
            return all_listings

        self._random_delay()

        if self._is_blocked(page):
            self.logger.warning(
                "Zillow appears to have blocked or CAPTCHA'd this session on page 1."
            )
            return all_listings

        for page_num in range(1, self.max_pages + 1):
            self.logger.info("Scraping page %d of %d", page_num, self.max_pages)

            self._human_scroll(page)
            self._random_mouse_movements(page)
            self._random_delay()

            card_listings = self._extract_cards(page)
            self.logger.info(
                "Page %d: found %d listing cards", page_num, len(card_listings)
            )

            new_cards = [c for c in card_listings if c.get("url") not in seen_urls]
            for card in new_cards:
                if card.get("url"):
                    seen_urls.add(card["url"])

            # Enrich each new card with detail-page data
            for idx, card in enumerate(new_cards, 1):
                self.logger.info(
                    "  Fetching detail %d/%d: %s",
                    idx,
                    len(new_cards),
                    card.get("address", "unknown"),
                )
                detail = self._extract_detail(page, card.get("url"))
                merged = {**card, **detail}
                all_listings.append(merged)
                self._random_delay()

            # Try to advance to next page
            if page_num < self.max_pages:
                advanced = self._go_to_next_page(page, page_num)
                if not advanced:
                    self.logger.info("No more pages found after page %d.", page_num)
                    break

                self._random_delay()

                if self._is_blocked(page):
                    self.logger.warning(
                        "Zillow appears to have blocked this session on page %d.",
                        page_num + 1,
                    )
                    break

        return all_listings

    # ------------------------------------------------------------------
    # Card extraction (search results page)
    # ------------------------------------------------------------------

    def _extract_cards(self, page: Any) -> list[dict[str, Any]]:
        """
        Extract basic listing info from search result cards.

        Zillow renders listings inside article elements or list items with
        data-test="property-card".  Selectors may need updating if Zillow
        changes its markup.
        """
        cards: list[dict[str, Any]] = []

        try:
            page.wait_for_selector(
                "[data-test='property-card'], article.list-card",
                timeout=15_000,
            )
        except Exception:
            self.logger.warning("No property cards found on page (selector timeout).")
            return cards

        try:
            card_elements = page.query_selector_all(
                "[data-test='property-card'], article.list-card"
            )
        except Exception as exc:
            self.logger.error("Error querying card elements: %s", exc)
            return cards

        for el in card_elements:
            card: dict[str, Any] = {
                "address": None,
                "price": None,
                "url": None,
                "bedrooms": None,
                "bathrooms": None,
                "sqft": None,
                "phone": None,
                "seller_name": None,
                "description": None,
                "full_details": {},
            }

            try:
                # Address
                addr_el = el.query_selector(
                    "[data-test='property-card-addr'], address, .list-card-addr"
                )
                if addr_el:
                    card["address"] = addr_el.inner_text().strip()

                # Price
                price_el = el.query_selector(
                    "[data-test='property-card-price'], .list-card-price"
                )
                if price_el:
                    card["price"] = price_el.inner_text().strip()

                # Listing URL
                link_el = el.query_selector("a[href*='/homedetails/'], a.list-card-link")
                if link_el:
                    href = link_el.get_attribute("href") or ""
                    if href.startswith("/"):
                        href = "https://www.zillow.com" + href
                    card["url"] = href

                # Beds / baths / sqft from the detail summary line
                detail_items = el.query_selector_all(
                    "[data-test='property-card-details'] li, .list-card-details li"
                )
                for item in detail_items:
                    text = item.inner_text().strip().lower()
                    if "bd" in text or "bed" in text:
                        card["bedrooms"] = text
                    elif "ba" in text or "bath" in text:
                        card["bathrooms"] = text
                    elif "sqft" in text or "sq ft" in text:
                        card["sqft"] = text

            except Exception as exc:
                self.logger.error("Error extracting card data: %s", exc)

            cards.append(card)

        return cards

    # ------------------------------------------------------------------
    # Detail page extraction
    # ------------------------------------------------------------------

    def _extract_detail(self, page: Any, url: str | None) -> dict[str, Any]:
        """
        Visit a listing detail page and extract enrichment data.
        Returns a dict of fields to merge into the card dict.
        """
        detail: dict[str, Any] = {
            "phone": None,
            "seller_name": None,
            "description": None,
            "full_details": {},
        }

        if not url:
            return detail

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            self._random_delay(multiplier=0.5)
            self._human_scroll(page)
        except Exception as exc:
            self.logger.error("Failed to load detail page %s: %s", url, exc)
            return detail

        try:
            # Phone number via tel: links
            tel_links = page.query_selector_all("a[href^='tel:']")
            phones = []
            for link in tel_links:
                href = link.get_attribute("href") or ""
                phone_raw = href.replace("tel:", "").strip()
                if phone_raw:
                    phones.append(phone_raw)
            if phones:
                detail["phone"] = phones[0]

            # Also scan page text for phone patterns
            if not detail["phone"]:
                page_text = page.inner_text("body")
                phone_match = re.search(
                    r"(\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4})", page_text
                )
                if phone_match:
                    detail["phone"] = phone_match.group(1)

            # Seller / agent name
            seller_selectors = [
                "[data-testid='attribution-LISTING_AGENT'] span",
                ".ds-listing-agent-info .zsg-h5",
                "[data-testid='listing-agent-name']",
                ".listing-agent-name",
            ]
            for sel in seller_selectors:
                try:
                    el = page.query_selector(sel)
                    if el:
                        detail["seller_name"] = el.inner_text().strip()
                        break
                except Exception:
                    pass

            # Property description
            desc_selectors = [
                "[data-testid='listing-description']",
                ".ds-overview-section p",
                "#ds-data-view .ds-overview-section",
            ]
            for sel in desc_selectors:
                try:
                    el = page.query_selector(sel)
                    if el:
                        detail["description"] = el.inner_text().strip()
                        break
                except Exception:
                    pass

            # Full details table / fact section
            fact_items: dict[str, str] = {}
            fact_rows = page.query_selector_all(
                "[data-testid='facts-and-features'] li, "
                ".ds-home-facts-and-features li, "
                ".fact-group-container li"
            )
            for row in fact_rows:
                try:
                    text = row.inner_text().strip()
                    if ":" in text:
                        k, _, v = text.partition(":")
                        fact_items[k.strip()] = v.strip()
                    elif text:
                        fact_items[text] = ""
                except Exception:
                    pass
            if fact_items:
                detail["full_details"] = fact_items

        except Exception as exc:
            self.logger.error("Error extracting detail from %s: %s", url, exc)

        return detail

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------

    def _go_to_next_page(self, page: Any, current_page: int) -> bool:
        """
        Attempt to advance to the next search results page.
        Returns True if navigation occurred, False otherwise.
        """
        # Strategy 1: aria-label="Next page" button
        try:
            next_btn = page.query_selector(
                "a[aria-label='Next page'], "
                "button[aria-label='Next page'], "
                "a[title='Next page'], "
                "li.zsg-pagination-next a"
            )
            if next_btn:
                next_btn.scroll_into_view_if_needed()
                self._random_delay(multiplier=0.3)
                next_btn.click()
                page.wait_for_load_state("domcontentloaded", timeout=20_000)
                return True
        except Exception as exc:
            self.logger.debug("Next-button strategy failed: %s", exc)

        # Strategy 2: numbered page link for current_page + 1
        try:
            next_num = current_page + 1
            page_link = page.query_selector(
                f"a[aria-label='Page {next_num}'], "
                f"button[aria-label='Page {next_num}']"
            )
            if page_link:
                page_link.scroll_into_view_if_needed()
                self._random_delay(multiplier=0.3)
                page_link.click()
                page.wait_for_load_state("domcontentloaded", timeout=20_000)
                return True
        except Exception as exc:
            self.logger.debug("Page-number strategy failed: %s", exc)

        return False

    # ------------------------------------------------------------------
    # Bot-detection helpers
    # ------------------------------------------------------------------

    def _is_blocked(self, page: Any) -> bool:
        """
        Heuristic check: return True if Zillow is showing a CAPTCHA or
        an access-denied page instead of normal content.
        """
        try:
            content = page.content().lower()
            blocked_signals = [
                "captcha",
                "access denied",
                "robot",
                "automated access",
                "unusual traffic",
                "please verify",
                "recaptcha",
                "hcaptcha",
            ]
            return any(sig in content for sig in blocked_signals)
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Human-behaviour helpers
    # ------------------------------------------------------------------

    def _random_delay(self, multiplier: float = 1.0) -> None:
        """Sleep for a random duration within [min_delay, max_delay]."""
        delay = random.uniform(self.min_delay, self.max_delay) * multiplier
        time.sleep(delay)

    def _random_mouse_movements(self, page: Any, moves: int = 5) -> None:
        """Perform random mouse movements to simulate human activity."""
        try:
            vp = page.viewport_size or {"width": 1280, "height": 800}
            width = vp.get("width", 1280)
            height = vp.get("height", 800)

            for _ in range(moves):
                x = random.randint(100, width - 100)
                y = random.randint(100, height - 100)
                page.mouse.move(x, y)
                time.sleep(random.uniform(0.05, 0.2))
        except Exception as exc:
            self.logger.debug("Mouse movement error (non-fatal): %s", exc)

    def _human_scroll(self, page: Any) -> None:
        """
        Scroll the page in a human-like fashion: scroll down in chunks,
        pause, maybe scroll back up a little, then continue.
        """
        try:
            scroll_steps = random.randint(4, 8)
            for _ in range(scroll_steps):
                delta = random.randint(200, 600)
                page.mouse.wheel(0, delta)
                time.sleep(random.uniform(0.3, 0.9))

            # Occasionally scroll back up a bit
            if random.random() < 0.4:
                page.mouse.wheel(0, -random.randint(100, 300))
                time.sleep(random.uniform(0.2, 0.5))
        except Exception as exc:
            self.logger.debug("Scroll error (non-fatal): %s", exc)
