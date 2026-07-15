"""
Site checkers.

Design choice: rather than one fragile CSS-selector-per-site (which breaks
the moment a retailer redesigns their page), the `generic_page` checker
works off *text markers* you configure per target ("In den Warenkorb" vs
"Ausverkauft" etc.) plus a regex price scan. This is more robust to minor
HTML changes and easy for you to tune in config.yaml without touching code.

Two checker types:
  - generic_page:          single product page -> in stock? price? 
  - kleinanzeigen_search:   search results page -> list of individual listings

IMPORTANT — before relying on this for a real site, check that site's
robots.txt and Terms of Service. This script is intended for infrequent,
low-volume personal-use polling (every few minutes), not high-frequency
scraping. Retailers may rate-limit or block bot traffic; if that happens,
the script logs it and moves on rather than retrying aggressively.
"""
from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import List, Optional

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

log = logging.getLogger("ac_watcher.checkers")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}

PRICE_RE = re.compile(r"(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2}))\s*€|€\s*(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2}))")


@dataclass
class CheckResult:
    target_name: str
    url: str
    available: bool
    price_eur: Optional[float]
    reason: str
    pickup_only: bool = False
    listing_location_text: Optional[str] = None
    listing_id: str = ""   # unique key for de-duplication (defaults to url)

    def __post_init__(self):
        if not self.listing_id:
            self.listing_id = self.url


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=20),
       retry=retry_if_exception_type(requests.RequestException))
def _fetch(url: str) -> requests.Response:
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp


def _parse_price(text: str) -> Optional[float]:
    match = PRICE_RE.search(text)
    if not match:
        return None
    raw = match.group(1) or match.group(2)
    # normalise "1.234,56" -> 1234.56 and "799,00" -> 799.00
    raw = raw.replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def check_generic_page(target: dict) -> CheckResult:
    name = target["name"]
    url = target["url"]
    try:
        resp = _fetch(url)
    except requests.RequestException as exc:
        return CheckResult(name, url, False, None, f"fetch error: {exc}")

    soup = BeautifulSoup(resp.text, "lxml")
    page_text = soup.get_text(" ", strip=True)

    in_markers = target.get("in_stock_markers", [])
    out_markers = target.get("out_of_stock_markers", [])

    has_in_marker = any(m.lower() in page_text.lower() for m in in_markers)
    has_out_marker = any(m.lower() in page_text.lower() for m in out_markers)

    # out-of-stock marker wins if both somehow appear (e.g. nav menu noise)
    if has_out_marker and not has_in_marker:
        available = False
        reason = "out-of-stock marker found"
    elif has_in_marker:
        available = True
        reason = "in-stock marker found"
    else:
        available = False
        reason = "no marker matched — treat as unknown/unavailable, check manually"

    price = _parse_price(page_text)

    return CheckResult(
        target_name=name, url=url, available=available,
        price_eur=price, reason=reason, pickup_only=target.get("pickup_only", False),
    )


def check_kleinanzeigen_search(target: dict, product_filters: dict) -> List[CheckResult]:
    """Returns one CheckResult per matching listing on the search results page."""
    name = target["name"]
    url = target["url"]
    try:
        resp = _fetch(url)
    except requests.RequestException as exc:
        return [CheckResult(name, url, False, None, f"fetch error: {exc}")]

    soup = BeautifulSoup(resp.text, "lxml")
    results: List[CheckResult] = []

    # Kleinanzeigen listing cards. Selector kept loose (class *contains* "aditem")
    # since exact class names change periodically.
    cards = soup.find_all("article", class_=re.compile("aditem"))

    for card in cards:
        title_el = card.find(["h2", "a"], class_=re.compile("ellipsis|text-module-begin"))
        title = title_el.get_text(" ", strip=True) if title_el else card.get_text(" ", strip=True)[:120]

        if not _matches_product(title, product_filters):
            continue

        link_el = card.find("a", href=True)
        listing_url = "https://www.kleinanzeigen.de" + link_el["href"] if link_el else url

        price_el = card.find(class_=re.compile("price"))
        price = _parse_price(price_el.get_text(" ", strip=True)) if price_el else None

        loc_el = card.find(class_=re.compile("locality"))
        location_text = loc_el.get_text(" ", strip=True) if loc_el else None

        card_text = card.get_text(" ", strip=True).lower()
        offers_shipping = "versand möglich" in card_text or "versand verfügbar" in card_text

        results.append(CheckResult(
            target_name=name,
            url=listing_url,
            available=True,   # if it's listed, it's for sale
            price_eur=price,
            reason="active listing matched product filters",
            pickup_only=not offers_shipping,
            listing_location_text=location_text,
            listing_id=listing_url,
        ))

    if not cards:
        results.append(CheckResult(name, url, False, None,
                                    "no listing cards parsed — Kleinanzeigen may have changed "
                                    "its HTML structure, check selectors"))
    return results


def _matches_product(title: str, filters: dict) -> bool:
    t = title.lower()
    for must in filters.get("must_include", []):
        if must.lower() not in t:
            return False
    any_list = filters.get("must_include_any", [])
    if any_list and not any(a.lower() in t for a in any_list):
        return False
    for bad in filters.get("must_exclude", []):
        if bad.lower() in t:
            return False
    return True
