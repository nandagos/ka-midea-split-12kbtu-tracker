#!/usr/bin/env python3
"""
Sends one fake "AC available" push notification through the exact same
code path main.py uses in production (config loading, env var override,
ntfy + optional Pushover) — without needing any real site to be in stock.

Use this to confirm your config.yaml / NTFY_TOPIC secret / Pushover creds
are correct before trusting the watcher to run unattended.

Usage:
    python test_notification.py
    python test_notification.py --config config.yaml
"""
from __future__ import annotations

import argparse

from main import load_config
from ac_watcher import notifier


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    config = load_config(args.config)

    title = "🟢 TEST — AC AVAILABLE — Amazon.de"
    message = (
        f"749.00 € — {config['product']['name']} "
        f"| This is a TEST notification, nothing is actually in stock."
    )
    test_url = "https://www.amazon.de/dp/B0GX16LKSC"

    print(f"Sending test notification via ntfy topic "
          f"'{config['notifications']['ntfy_topic']}' "
          f"(server: {config['notifications']['ntfy_server']})...")

    notifier.notify_all(config, title, message, url=test_url)

    print("Sent. Check your phone. If nothing arrived within ~10 seconds:")
    print("  - double-check the ntfy topic name matches exactly (case-sensitive)")
    print("  - make sure the ntfy app is actually subscribed to that topic")
    print("  - try the raw curl test from the README as a sanity check")
    print("  - if Pushover is enabled, check pushover_user_key / api_token are correct")


if __name__ == "__main__":
    main()
