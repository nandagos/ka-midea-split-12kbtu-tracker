"""
Judges whether a *pickup-only* listing is realistically reachable without a car.

Two things happen:
  1. Geocode the listing's free-text location (e.g. "76133 Karlsruhe" or
     "Ettlingen") to lat/lon via OpenStreetMap Nominatim (free, no API key,
     but rate-limited to 1 request/second — we sleep accordingly).
  2. Compute straight-line distance from home and classify it.

The AC ships as one ~60kg / 80x70x70cm box, so "on foot" really only makes
sense with a hand trolley over a short distance — we default that threshold
low (4 km) and treat anything up to ~40 km as "reachable via a big/Kombi taxi".
Anything further is flagged so you can decide (courier pickup via a
service like "Otto Pickup"/parcel forwarding, asking a friend, etc.).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from geopy.distance import geodesic
from geopy.geocoders import Nominatim

_geolocator = Nominatim(user_agent="ac-watcher-personal-use")
_last_geocode_call = 0.0


@dataclass
class PickupAssessment:
    reachable: bool
    mode: str          # "walk" | "taxi" | "too_far" | "unknown_location"
    distance_km: Optional[float]
    note: str


def _rate_limited_geocode(query: str):
    global _last_geocode_call
    elapsed = time.time() - _last_geocode_call
    if elapsed < 1.1:
        time.sleep(1.1 - elapsed)
    _last_geocode_call = time.time()
    return _geolocator.geocode(query, country_codes="de", timeout=10)


def assess_pickup(listing_location_text: str, home_lat: float, home_lon: float,
                   max_walk_km: float, max_taxi_km: float) -> PickupAssessment:
    if not listing_location_text:
        return PickupAssessment(False, "unknown_location", None,
                                 "Listing has no location text — check manually.")

    try:
        geocoded = _rate_limited_geocode(listing_location_text)
    except Exception as exc:  # network hiccup, Nominatim down, etc.
        return PickupAssessment(False, "unknown_location", None,
                                 f"Geocoding failed ({exc}) — check manually.")

    if geocoded is None:
        return PickupAssessment(False, "unknown_location", None,
                                 f"Could not geocode '{listing_location_text}' — check manually.")

    dist_km = geodesic((home_lat, home_lon), (geocoded.latitude, geocoded.longitude)).km

    if dist_km <= max_walk_km:
        return PickupAssessment(True, "walk", dist_km,
                                 f"{dist_km:.1f} km away — walkable with a hand trolley.")
    if dist_km <= max_taxi_km:
        return PickupAssessment(True, "taxi", dist_km,
                                 f"{dist_km:.1f} km away — reachable with a big/Kombi taxi.")
    return PickupAssessment(False, "too_far", dist_km,
                             f"{dist_km:.1f} km away — too far without a car. "
                             f"Consider asking the seller about shipping instead.")
