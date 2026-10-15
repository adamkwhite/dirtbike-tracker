"""DEAL / GOOD push messages, sent through the Notifier port (ports.py)."""

from ..domain.scoring import specs_label, vs_label
from .ports import Notifier


def _drop_prefix(listing: dict) -> tuple[str, str]:
    """("PRICE DROP: ", "Was $X. ") for a listing that just dropped, else empty strings."""
    was = listing.get("price_drop_from")
    if was is None:
        return "", ""
    return "PRICE DROP: ", f"Was ${int(was)}. "


def _specs_line(listing: dict) -> str:
    """'2018 · 16,672 km' plus a newline when year or km is known, else ''."""
    specs = specs_label(listing)
    return f"{specs}\n" if specs else ""


def notify_deal(notify: Notifier, listing: dict) -> bool:
    """Send a push notification for a new DEAL-rated listing."""
    price = f"${int(listing['price'])}" if listing.get("price") else "?"
    delta = listing.get("delta_pct")
    delta_str = f" ({delta:+.0f}% {vs_label(listing)})" if delta is not None else ""
    model = listing.get("model_slug") or "bike"
    location = listing.get("location") or "unknown"

    urgent = listing.get("urgency") == "URGENT"
    prefix, was = _drop_prefix(listing)
    title = f"{prefix}{'URGENT DEAL' if urgent else 'DEAL'}: {model.upper()} {price}{delta_str}"
    body = f"{was}{listing.get('title', '')}\n{_specs_line(listing)}{location}\n{listing.get('url', '')}"

    return notify(
        title=title,
        body=body,
        priority=5 if urgent else 4,
        tags=["money", "fire"] if urgent else ["money"],
        click_url=listing.get("url"),
        attach_url=listing.get("image_url"),
    )


def notify_good(notify: Notifier, listing: dict) -> bool:
    """Send a lower-priority notification for a GOOD listing."""
    price = f"${int(listing['price'])}" if listing.get("price") else "?"
    delta = listing.get("delta_pct")
    delta_str = f" ({delta:+.0f}%)" if delta is not None else ""
    model = listing.get("model_slug") or "bike"
    location = listing.get("location") or "unknown"

    prefix, was = _drop_prefix(listing)
    body = f"{was}{listing.get('title', '')}\n{_specs_line(listing)}{location}\n{listing.get('url', '')}"
    negotiable = listing.get("urgency") == "NEGOTIABLE" and listing.get("price")
    if negotiable:
        # ponytail: flat 10% under asking, rounded to $50; tune once #22 sold data shows real discounts
        offer = round(listing["price"] * 0.9 / 50) * 50
        age = int(listing.get("age_days") or 0)
        title = f"{prefix}Negotiable: {model.upper()} {price}{delta_str}, listed {age}d"
        body = f"Listed {age} days. Try ~${offer}.\n{body}"
    else:
        title = f"{prefix}Good: {model.upper()} {price}{delta_str}"

    return notify(
        title=title,
        body=body,
        priority=3 if negotiable else 2,
        tags=["handshake"] if negotiable else ["eyes"],
        click_url=listing.get("url"),
        attach_url=listing.get("image_url"),
    )


def notify_match(notify: Notifier, listing: dict) -> bool:
    """A watchlist match (models.yml `alert: all`): every new listing, deal or not."""
    price = f"${int(listing['price'])}" if listing.get("price") else "?"
    model = listing.get("model_slug") or "bike"
    prefix, was = _drop_prefix(listing)
    seller = f" ({listing['seller_type']})" if listing.get("seller_type") else ""
    body = (
        f"{was}{listing.get('title', '')}\n{_specs_line(listing)}"
        f"{listing.get('location') or 'unknown'}{seller}\n{listing.get('url', '')}"
    )
    return notify(
        title=f"{prefix or 'New: '}{model.upper()} {price}",
        body=body,
        priority=3,
        tags=["motorcycle"],
        click_url=listing.get("url"),
        attach_url=listing.get("image_url"),
    )
