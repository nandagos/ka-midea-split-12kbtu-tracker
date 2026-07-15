"""
Sends the actual phone push notification.

Primary channel: ntfy.sh — free, no account, no API key. Install the ntfy
app (iOS: https://apps.apple.com/app/ntfy/id1625396347,
Android: https://play.google.com/store/apps/details?id=io.heckel.ntfy)
and subscribe to the topic name set in config.yaml. Treat that topic name
like a shared secret — anyone who knows it can read/post to it, so pick
something random (the default config already has a random-looking one;
change it anyway).

Optional secondary channel: Pushover — more guaranteed delivery, costs a
small one-time fee per platform, needs an account + API token.
"""
from __future__ import annotations

import logging
from typing import Optional

import requests

log = logging.getLogger("ac_watcher.notifier")


def send_ntfy(server: str, topic: str, title: str, message: str,
              url: Optional[str] = None, priority: str = "urgent") -> bool:
    headers = {
        "Title": title.encode("utf-8"),
        "Priority": priority,       # "urgent" makes the phone buzz/sound insistently
        "Tags": "tada,fire",
    }
    if url:
        headers["Click"] = url
        headers["Actions"] = f"view, Open listing, {url}"
    try:
        resp = requests.post(f"{server.rstrip('/')}/{topic}", data=message.encode("utf-8"),
                              headers=headers, timeout=10)
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        log.error("ntfy send failed: %s", exc)
        return False


def send_pushover(user_key: str, api_token: str, title: str, message: str,
                   url: Optional[str] = None) -> bool:
    payload = {
        "token": api_token,
        "user": user_key,
        "title": title,
        "message": message,
        "priority": 1,     # high priority
    }
    if url:
        payload["url"] = url
        payload["url_title"] = "Open listing"
    try:
        resp = requests.post("https://api.pushover.net/1/messages.json", data=payload, timeout=10)
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        log.error("Pushover send failed: %s", exc)
        return False


def notify_all(config: dict, title: str, message: str, url: Optional[str] = None) -> None:
    n_cfg = config["notifications"]
    ok = send_ntfy(n_cfg["ntfy_server"], n_cfg["ntfy_topic"], title, message, url)
    if not ok:
        log.warning("ntfy delivery failed — check your topic/network.")

    if n_cfg.get("pushover_enabled"):
        send_pushover(n_cfg["pushover_user_key"], n_cfg["pushover_api_token"], title, message, url)
