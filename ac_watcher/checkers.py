"""
Site checkers.

Availability detection has two layers, tried in order:

  1. schema.org "Product" structured data (JSON-LD), when the page provides
     it. This is the SAME machine-readable data Google uses for search
     result rich snippets, so it's far more reliable than scanning visible
     text: it refers specifically to *this* product's offer, not to some
     "customers also viewed" widget elsewhere on the page that happens to
     have its own "add to cart" button for a different, in-stock item.
  2. Text markers you configure per target ("In den Warenkorb" vs
     "Ausverkauft" etc.), used only as a fallback when no structured data
     is found. This is inherently less reliable (see above) so results
     from this path are tagged as low-confidence in CheckResult.reason.

Two checker types:
  - generic_page:          single product page -> in stock? price?
  - kleinanzeigen_search:   search results page -> list of individual listings

IMPORTANT — before relying on this for a real site, check that site's
robots.txt and Terms of Service. This script is intended for infrequent,
low-volume personal-use polling (every few minutes), not high-frequency
scraping. Some retailers (Akamai/PerimeterX-protected sites in particular)
block requests from well-known cloud/CI IP ranges — including GitHub
Actions — regardless of headers used. When that happens we treat it as a
"blocked" result (not retried, not a crash) rather than hammering the same
block repeatedly. See README "Known limitations" for what to do about it.
"""
from __future__ import annotations

import json
import re
import logging
from dataclasses import dataclass
from typing import Iterator, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

log = logging.getLogger("ac_watcher.checkers")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Connection": "keep-alive",
}

PRICE_RE = re.compile(r"(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2}))\s*€|€\s*(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2}))")


class BlockedError(Exception):
    """Raised for a 403/429 response — treated as a (likely persistent) bot-
    detection block, not a transient error. We deliberately do NOT retry
    these: retrying just repeats the same rejection a few more times,
    wastes CI minutes, and adds nothing."""

    def __init__(self, status_code: int, url: str):
        self.status_code = status_code
        self.url = url
        super().__init__(f"HTTP {status_code} (blocked) for {url}")


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
    if resp.status_code in (403, 429):
        raise BlockedError(resp.status_code, url)
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


def _flatten_jsonld(data) -> Iterator[dict]:
    """JSON-LD can be a single object, a list of objects, or an object with
    an "@graph" list — normalise all three into a flat stream of dicts."""
    if isinstance(data, list):
        for item in data:
            yield from _flatten_jsonld(item)
    elif isinstance(data, dict):
        if "@graph" in data:
            yield from _flatten_jsonld(data["@graph"])
        else:
            yield data


def _read_offer(offers) -> Tuple[Optional[bool], Optional[float]]:
    if offers is None:
        return None, None
    for offer in offers if isinstance(offers, list) else [offers]:
        if not isinstance(offer, dict):
            continue
        avail_raw = str(offer.get("availability", "")).lower()
        available = None
        if "instock" in avail_raw or "limitedavailability" in avail_raw:
            available = True
        elif "outofstock" in avail_raw or "soldout" in avail_raw or "discontinued" in avail_raw:
            available = False

        price = None
        price_raw = offer.get("price")
        if price_raw is not None:
            try:
                price = float(str(price_raw).replace(",", "."))
            except ValueError:
                price = None

        if available is not None or price is not None:
            return available, price
    return None, None


def _extract_jsonld_offer(soup: BeautifulSoup) -> Tuple[Optional[bool], Optional[float]]:
    """Look for schema.org Product/Offer structured data on the page. This
    refers to the specific product the page is about, so it avoids the
    false positives that text-marker scanning gets from unrelated
    "customers also viewed" / cross-sell widgets on the same page."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _flatten_jsonld(data):
            node_type = node.get("@type", "")
            types = node_type if isinstance(node_type, list) else [node_type]
            if "Product" not in types:
                continue
            available, price = _read_offer(node.get("offers"))
            if available is not None or price is not None:
                return available, price
    return None, None


def check_generic_page(target: dict) -> CheckResult:
    name = target["name"]
    url = target["url"]
    pickup_only = target.get("pickup_only", False)

    try:
        resp = _fetch(url)
    except BlockedError as exc:
        return CheckResult(
            name, url, False, None,
            f"blocked (HTTP {exc.status_code}) — likely bot detection against this "
            f"runner's IP, not a real stock signal. See README 'Known limitations'.",
            pickup_only=pickup_only,
        )
    except requests.RequestException as exc:
        return CheckResult(name, url, False, None, f"fetch error: {exc}", pickup_only=pickup_only)

    soup = BeautifulSoup(resp.text, "lxml")
    page_text = soup.get_text(" ", strip=True)

    jsonld_available, jsonld_price = _extract_jsonld_offer(soup)

    if jsonld_available is not None:
        available = jsonld_available
        state = "InStock" if available else "OutOfStock"
        reason = f"structured data (JSON-LD): availability={state}"
        price = jsonld_price if jsonld_price is not None else _parse_price(page_text)
    else:
        # Fallback: no structured data found, scan visible text instead.
        # Lower confidence — a false "in stock" can come from an unrelated
        # cross-sell widget's own "add to cart" button elsewhere on the page.
        in_markers = target.get("in_stock_markers", [])
        out_markers = target.get("out_of_stock_markers", [])

        has_in_marker = any(m.lower() in page_text.lower() for m in in_markers)
        has_out_marker = any(m.lower() in page_text.lower() for m in out_markers)

        if has_out_marker and not has_in_marker:
            available = False
            reason = "text marker (low-confidence, no JSON-LD found): out-of-stock marker matched"
        elif has_in_marker and not has_out_marker:
            available = True
            reason = "text marker (LOW-CONFIDENCE, no JSON-LD found): in-stock marker matched — verify manually before buying"
        else:
            available = False
            reason = "no structured data and no clear text marker match — treat as unknown/unavailable, check manually"

        price = _parse_price(page_text)

    return CheckResult(
        target_name=name, url=url, available=available,
        price_eur=price, reason=reason, pickup_only=pickup_only,
    )


def check_kleinanzeigen_search(target: dict, product_filters: dict) -> List[CheckResult]:
    """Returns one CheckResult per matching listing on the search results page."""
    name = target["name"]
    url = target["url"]
    try:
        resp = _fetch(url)
    except BlockedError as exc:
        return [CheckResult(
            name, url, False, None,
            f"blocked (HTTP {exc.status_code}) — likely bot detection against this "
            f"runner's IP. See README 'Known limitations'.",
        )]
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
