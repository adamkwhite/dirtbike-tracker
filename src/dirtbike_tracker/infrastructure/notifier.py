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


class NotifyConfigError(Exception):
    """Raised when ntfy topic is not configured."""


def notify(
    title: str,
    body: str,
    priority: int = 3,
    tags: list[str] | None = None,
    click_url: str | None = None,
    topic: str | None = None,
    timeout: int = 10,
) -> bool:
    """
    Send a push notification.

    priority: 1=min, 3=default, 4=high, 5=max (bypasses DND)
    tags: ntfy emoji shortcodes, e.g. ["money", "fire"]
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

    url = f"{NTFY_SERVER}/{topic}"
    try:
        response = requests.post(url, data=body.encode("utf-8"), headers=headers, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"ntfy notify failed: {exc}")  # noqa: T201
        return False
    else:
        return True


def notify_deal(listing: dict) -> bool:
    """Send a push notification for a new DEAL-rated listing."""
    price = f"${int(listing['price'])}" if listing.get("price") else "?"
    delta = listing.get("delta_pct")
    delta_str = f" ({delta:+.0f}% vs median)" if delta is not None else ""
    model = listing.get("model_slug") or "bike"
    location = listing.get("location") or "unknown"

    title = f"DEAL: {model.upper()} {price}{delta_str}"
    body = f"{listing.get('title', '')}\n{location}\n{listing.get('url', '')}"

    return notify(
        title=title,
        body=body,
        priority=5,
        tags=["money", "fire"],
        click_url=listing.get("url"),
    )


def notify_good(listing: dict) -> bool:
    """Send a lower-priority notification for a GOOD listing."""
    price = f"${int(listing['price'])}" if listing.get("price") else "?"
    delta = listing.get("delta_pct")
    delta_str = f" ({delta:+.0f}%)" if delta is not None else ""
    model = listing.get("model_slug") or "bike"
    location = listing.get("location") or "unknown"

    title = f"Good: {model.upper()} {price}{delta_str}"
    body = f"{listing.get('title', '')}\n{location}\n{listing.get('url', '')}"

    return notify(
        title=title,
        body=body,
        priority=3,
        tags=["eyes"],
        click_url=listing.get("url"),
    )
