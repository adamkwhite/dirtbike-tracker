"""
Push notifications via ntfy.sh.

ntfy is free, self-hostable, and takes a simple HTTP POST. No account
required. Choose a hard-to-guess topic name and subscribe on your phone
via the ntfy app (iOS/Android/web).
"""

import os

import requests

NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
NTFY_STREET_TOPIC = os.environ.get("NTFY_STREET_TOPIC", "")  # street bikes' alerts, if set
HEALTHCHECK_URL = os.environ.get("HEALTHCHECK_URL", "")


class NotifyConfigError(Exception):
    """Raised when ntfy topic is not configured."""


def notify(
    title: str,
    body: str,
    priority: int = 3,
    tags: list[str] | None = None,
    click_url: str | None = None,
    attach_url: str | None = None,
    topic: str | None = None,
    timeout: int = 10,
) -> bool:
    """
    Send a push notification.

    priority: 1=min, 3=default, 4=high, 5=max (bypasses DND)
    tags: ntfy emoji shortcodes, e.g. ["money", "fire"]
    attach_url: an image URL the ntfy app shows inline (the listing photo, #10)
    """
    topic = topic or NTFY_TOPIC
    if not topic:
        raise NotifyConfigError(
            "No ntfy topic configured. Set NTFY_TOPIC env var, "
            "e.g. 'export NTFY_TOPIC=adam-dirtbike-xyz123'. "
            "Then subscribe to that topic in the ntfy app."
        )

    headers = {
        "Title": title,
        "Priority": str(priority),
    }
    if tags:
        headers["Tags"] = ",".join(tags)
    if click_url:
        headers["Click"] = click_url
    if attach_url:
        headers["Attach"] = attach_url

    url = f"{NTFY_SERVER}/{topic}"
    try:
        response = requests.post(url, data=body.encode("utf-8"), headers=headers, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"ntfy notify failed: {exc}")  # noqa: T201
        return False
    else:
        return True


def ping_healthcheck(url: str | None = None, timeout: int = 10) -> bool:
    """Ping the dead-man's switch (healthchecks.io) after a completed watch run.

    The external service alerts when pings stop, which catches watch never
    running at all. No-op when HEALTHCHECK_URL is unset; never raises.
    """
    url = url or HEALTHCHECK_URL
    if not url:
        return False
    try:
        requests.get(url, timeout=timeout).raise_for_status()
    except requests.RequestException as exc:
        print(f"healthcheck ping failed: {exc}")  # noqa: T201
        return False
    else:
        return True
