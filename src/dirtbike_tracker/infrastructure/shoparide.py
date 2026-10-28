"""Shop a Ride (#145): Power Go's marketplace of its client dealers' stock, ~120 Ontario dealers.

A static Next.js export with no API: each vehicle type's whole inventory (every province, new
and used) is one lazy webpack chunk, `JSON.parse('[...]')`. Chunk ids and hashes change with
every build, so each run finds them by name: a page script maps
"./vehicles-data-<type>-<uuid>-en.json" to a chunk id, and the webpack runtime maps the id
to its file. The motorcycle chunk is ~13 MB and the data is rebuilt about daily, so the
filtered units are cached on disk by build (the runtime's file name, which changes with
every rebuild): a run that finds the same build downloads only the page.
"""

import json
import os
import random
import re
from datetime import UTC, datetime
from pathlib import Path

import requests

from ..domain.listing import Listing
from ..domain.scoring import is_dealer_new, parse_km, parse_year
from .kijiji_scraper import PRICE_STATE_NUMERIC, PRICE_STATE_UNKNOWN, USER_AGENTS, model_slugs_for
from .run_cache import OncePerRun

CACHE_DIR = Path(
    os.environ.get("SHOPARIDE_CACHE_DIR", "~/.cache/dirtbike-tracker/shoparide")
).expanduser()
TYPES = ("motocross", "motorcycle")  # the dirt and street bikes; ATVs, boats, sleds are others
_SCRIPT_RE = re.compile(r'src="(/_next/static/chunks/[^"]+\.js)"')
_RUNTIME_RE = re.compile(r"/_next/static/chunks/webpack-[0-9a-f]+\.js")
_DATA_RE = re.compile(r'"\./vehicles-data-([a-z-]+)-[0-9a-f-]{36}-en\.json":\[(\d+)')
_JS_ESCAPE_RE = re.compile(r"\\(x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|[\s\S])")
# what parse() reads; the rest of each unit isn't cached
_KEEP = ("vehicle_id", "label", "year", "condition", "url", "sale_price_value", "base_price_value",
         "usage", "tenant_city", "tenant_name", "image")  # fmt: skip
_JS_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}


def _js_unescape(m: re.Match[str]) -> str:
    e = m.group(1)
    return chr(int(e[1:], 16)) if len(e) > 1 else _JS_ESCAPES.get(e, e)


def parse_units(chunk: str) -> list[dict]:
    """The units in an inventory chunk's JSON.parse('...') string. [] if it changed shape."""
    start, end = chunk.find("JSON.parse('"), chunk.rfind("')")
    if start < 0 or end <= start:
        return []
    try:
        units = json.loads(_JS_ESCAPE_RE.sub(_js_unescape, chunk[start + 12 : end]), strict=False)
    except ValueError:
        return []
    return [u for u in units if isinstance(u, dict)] if isinstance(units, list) else []


def data_chunk_ids(script: str) -> dict[str, str]:
    """vehicle type -> chunk id, from the page script that lazy-loads the English data files."""
    return dict(_DATA_RE.findall(script))


def chunk_files(runtime: str) -> dict[str, str]:
    """chunk id -> file name ("1734.21909876154dafc5.js"), from the webpack runtime."""
    table = runtime.split('"static/chunks/"', 1)[-1].split("[e]", 1)[0]
    return {cid: f"{cid}.{h}.js" for cid, h in re.findall(r'(\d+):"([0-9a-f]+)"', table)}


class ShopARideSource:
    """A ListingSource over Shop a Ride's used units in one province.

    Every unit is a dealer's (seller_type "dealer"), located at the dealer's city, so the
    dealer's name goes in the description. New units and a dealer's current-model-year
    bikes are skipped, as for the other dealer sources, unless a model sets `include_new`.
    """

    def __init__(
        self,
        name: str,
        base_url: str,
        province: str = "Ontario",
        types: list[str] | tuple[str, ...] = TYPES,
        cache_dir: Path | None = None,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.province = province
        self.types = tuple(types)
        self._cache_dir = cache_dir  # None: CACHE_DIR, looked up per call so tests can move it
        self.last_fetch_ok = False  # read by watch.sources_seen, as for Dealer Spike

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir or CACHE_DIR

    def __call__(
        self,
        model_slug: str,
        region: str,  # noqa: ARG002
        pages: int,  # noqa: ARG002
        *,
        query: str | None = None,  # noqa: ARG002
        include_new: bool = False,
    ) -> list[Listing]:
        try:
            units = _units(self.base_url, self.province, self.types, self.cache_dir)
        except (requests.RequestException, LookupError) as exc:
            print(f"{self.name}: inventory fetch failed: {exc}")  # noqa: T201
            self.last_fetch_ok = False
            return []
        self.last_fetch_ok = bool(units)
        listings = self.parse(units, model_slug, include_new)
        print(f"{self.name} {model_slug}: {len(listings)} listings")  # noqa: T201
        return listings

    def parse(self, units: list[dict], model_slug: str, include_new: bool = False) -> list[Listing]:
        now = datetime.now(UTC).replace(tzinfo=None).isoformat()
        this_year = int(now[:4])
        listings = []
        for u in units:
            year = parse_year(u.get("year"), this_year)
            is_new = u.get("condition") != "Used" or is_dealer_new("dealer", year, this_year)
            if is_new and not include_new:
                continue
            title = str(u.get("label") or "").strip()
            slugs = model_slugs_for(title, model_slug)
            if not slugs or not u.get("vehicle_id") or not u.get("url"):
                continue
            price = u.get("sale_price_value") or u.get("base_price_value")
            usage = str(u.get("usage") or "")
            image = u.get("image")
            listings.append(
                Listing(
                    source=self.name,
                    external_id=str(u["vehicle_id"]),
                    url=f"{self.base_url}{u['url']}",
                    title=title,
                    price=float(price) if price else None,
                    price_state=PRICE_STATE_NUMERIC if price else PRICE_STATE_UNKNOWN,
                    location=f"{u.get('tenant_city') or ''}, {self.province}".strip(", "),
                    posted_text="",
                    description=f"Dealer: {u.get('tenant_name') or '?'}. Usage: {usage or '?'}",
                    model_slugs=slugs,
                    scraped_at=now,
                    year=year,
                    km=parse_km(usage) if usage.endswith("km") else None,
                    image_url=image.get("source") if isinstance(image, dict) else None,
                    seller_type="dealer",
                )
            )
        return listings


def _get(url: str) -> str:
    headers = {"User-Agent": random.choice(USER_AGENTS)}  # NOSONAR - UA rotation, not security
    response = requests.get(url, headers=headers, timeout=60)
    response.raise_for_status()
    return str(response.text)


# ponytail: per-process like the other dealers (every model filters one inventory), plus the
# disk cache by build; one province's units are a few hundred, so the cache stays small
@OncePerRun
def _units(base_url: str, province: str, types: tuple[str, ...], cache_dir: Path) -> list[dict]:
    """The province's units of `types` for the site's current build. LookupError if the
    build's data files can't be found (a site change: never guess a chunk)."""
    page = _get(f"{base_url}/en/{types[-1]}/")
    runtime_m = _RUNTIME_RE.search(page)
    if not runtime_m:
        raise LookupError("no webpack runtime on the page")
    cache = cache_dir / f"{province.lower()}.json"
    try:
        cached = json.loads(cache.read_text())
    except (OSError, ValueError):
        cached = {}
    if cached.get("build") == runtime_m.group(0) and cached.get("types") == list(types):
        return list(cached["units"])

    ids: dict[str, str] = {}
    for script in _SCRIPT_RE.findall(page):
        if ids := data_chunk_ids(_get(base_url + script)):
            break
    files = chunk_files(_get(base_url + runtime_m.group(0)))
    units = []
    for kind in types:
        if kind not in ids or ids[kind] not in files:
            raise LookupError(f"no data chunk for {kind!r} in this build")
        chunk = parse_units(_get(f"{base_url}/_next/static/chunks/{files[ids[kind]]}"))
        if not chunk:
            raise LookupError(f"the {kind!r} data chunk didn't parse")
        units += [
            {k: u.get(k) for k in _KEEP} for u in chunk if u.get("tenant_province") == province
        ]
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps({"build": runtime_m.group(0), "types": list(types), "units": units})
    )
    return units
