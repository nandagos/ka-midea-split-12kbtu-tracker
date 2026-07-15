#!/usr/bin/env python3
"""
AC Watcher — Midea PortaSplit 12000 BTU restock monitor for Karlsruhe.

Usage:
    python main.py            # run forever, checking every check_interval_seconds
    python main.py --once     # run a single check pass and exit (good for cron/GitHub Actions)

See README.md for setup, and config.yaml for what it checks and how.
"""
from __future__ import annotations

import argparse
import logging
import os
import random
import time

import yaml

from ac_watcher import checkers, location, notifier
from ac_watcher.state import StateStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("ac_watcher.main")


def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # Let a NTFY_TOPIC environment variable (e.g. a GitHub Actions secret)
    # override config.yaml, so the topic name never has to be committed to
    # a public repo.
    env_topic = os.environ.get("NTFY_TOPIC")
    if env_topic:
        config["notifications"]["ntfy_topic"] = env_topic

    return config


def evaluate_and_notify(result: checkers.CheckResult, config: dict, state: StateStore) -> None:
    max_price = config["product"]["max_price_eur"]
    loc_cfg = config["location"]

    price_ok = (result.price_eur is None) or (result.price_eur <= max_price)
    buyable = result.available and price_ok

    pickup_note = ""
    if buyable and result.pickup_only:
        assessment = location.assess_pickup(
            result.listing_location_text or "",
            loc_cfg["latitude"], loc_cfg["longitude"],
            loc_cfg["max_walk_km"], loc_cfg["max_taxi_km"],
        )
        pickup_note = " | " + assessment.note
        # Still notify even if too far / unknown — you might want to arrange
        # shipping or send someone else, so we don't silently drop it. We just
        # flag it clearly in the message.
        if not assessment.reachable:
            pickup_note = " | ⚠ PICKUP HARD: " + assessment.note

    state_key = f"{result.target_name}::{result.listing_id}"
    was_buyable = state.get(state_key, False)

    price_str = f"{result.price_eur:.2f} €" if result.price_eur else "price unknown"
    confidence_note = ""
    if "LOW-CONFIDENCE" in result.reason:
        confidence_note = " | ⚠ UNVERIFIED — double check the listing before buying"

    if buyable and not was_buyable:
        title = f"🟢 AC AVAILABLE — {result.target_name}"
        message = f"{price_str} — {config['product']['name']}{pickup_note}{confidence_note}"
        log.info("NOTIFY: %s | %s | %s", title, message, result.url)
        notifier.notify_all(config, title, message, url=result.url)
    elif buyable:
        log.info("Still buyable (already notified): %s [%s]", result.target_name, price_str)
    else:
        log.info("Not buyable: %s (%s, %s)", result.target_name, result.reason, price_str)

    state.set(state_key, buyable)


def run_once(config: dict, state: StateStore) -> None:
    product_filters = config["product"]
    for target in config["targets"]:
        try:
            if target["type"] == "generic_page":
                result = checkers.check_generic_page(target)
                evaluate_and_notify(result, config, state)

            elif target["type"] == "kleinanzeigen_search":
                results = checkers.check_kleinanzeigen_search(target, product_filters)
                for result in results:
                    evaluate_and_notify(result, config, state)

            else:
                log.warning("Unknown target type %r for %s", target["type"], target["name"])

        except Exception:
            # A single target failing (site down, HTML changed, network blip)
            # must never kill the whole loop.
            log.exception("Unhandled error checking target %s", target["name"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="run a single pass and exit")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--state", default="state.json")
    args = parser.parse_args()

    config = load_config(args.config)
    state = StateStore(args.state)

    if args.once:
        run_once(config, state)
        return

    interval = config.get("check_interval_seconds", 240)
    jitter = config.get("jitter_seconds", 30)

    log.info("Starting AC watcher. Checking %d targets every ~%ds.",
              len(config["targets"]), interval)

    while True:
        run_once(config, state)
        sleep_for = interval + random.randint(-jitter, jitter)
        sleep_for = max(30, sleep_for)
        log.info("Sleeping %ds until next check...", sleep_for)
        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
