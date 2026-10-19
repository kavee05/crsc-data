"""Shared helpers for the brand price scrapers. Standard library only."""

from __future__ import annotations

import gzip
import html
import json
import re
import time
import urllib.request
import zlib
from dataclasses import asdict, dataclass, field
from datetime import date

USER_AGENT = "CRSC-price-bot/1.0 (+https://github.com/; weekly official price check)"
#: Seconds between requests to the same site. Brand sites are fetched about once a week.
POLITE_DELAY = 1.5

_last_request: dict[str, float] = {}


def fetch_bytes(url: str, *, timeout: int = 60) -> bytes:
    """GET a binary file (e.g. a PDF price list) politely."""
    host = url.split("/")[2]
    wait = POLITE_DELAY - (time.time() - _last_request.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    finally:
        _last_request[host] = time.time()


def fetch(url: str, *, accept: str = "*/*", timeout: int = 40) -> str:
    """GET a URL politely (rate-limited per host) and return the decoded body."""
    host = url.split("/")[2]
    wait = POLITE_DELAY - (time.time() - _last_request.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            body = resp.read()
            # Some sites gzip responses even without Accept-Encoding; urllib won't undo it.
            encoding = (resp.headers.get("Content-Encoding") or "").lower()
            if encoding == "gzip" or body[:2] == b"\x1f\x8b":
                body = gzip.decompress(body)
            elif encoding == "deflate":
                body = zlib.decompress(body)
            return body.decode(charset, errors="replace")
    finally:
        _last_request[host] = time.time()


def fetch_json(url: str) -> dict:
    return json.loads(fetch(url, accept="application/json"))


def clean(fragment: str) -> str:
    """Strip tags and collapse whitespace."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", fragment or "")).replace("​", "").replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip()


def money(text: str) -> float | None:
    m = re.search(r"([0-9][0-9,]*(?:\.[0-9]+)?)", text or "")
    return float(m.group(1).replace(",", "")) if m else None


def integer(text: str) -> int | None:
    m = re.search(r"([0-9][0-9,]*)", text or "")
    return int(m.group(1).replace(",", "")) if m else None


def js_object(page: str, marker: str):
    """Decode the JSON literal that follows `marker` (e.g. 'window.data_price_list'), or None."""
    i = page.find(marker)
    if i < 0:
        return None
    starts = [j for j in (page.find("{", i), page.find("[", i)) if j >= 0]
    if not starts:
        return None
    try:
        return json.JSONDecoder().raw_decode(page, min(starts))[0]
    except json.JSONDecodeError:
        return None


def table_rows(fragment: str) -> list[tuple[str, str]]:
    """(label, value) pairs from a two-column spec table."""
    rows = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", fragment or "", re.S | re.I):
        cells = [clean(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S | re.I)]
        if len(cells) >= 2 and cells[0]:
            rows.append((cells[0], cells[1]))
    return rows


def short_transmission(text: str | None) -> str | None:
    """'7-Speed CVT with Sequential Shiftmatic' -> 'CVT', '6-Speed Automatic' -> '6-speed AT'."""
    if not text:
        return None
    t = text.lower()
    speeds = re.search(r"(\d+)\s*-?\s*speed", t)
    prefix = f"{speeds.group(1)}-speed " if speeds else ""
    if "e-cvt" in t or "ecvt" in t:
        return "e-CVT"
    if "cvt" in t:
        return "CVT"
    if "dct" in t or "dual clutch" in t or "dual-clutch" in t:
        return prefix + "DCT"
    if re.search(r"\bmanual\b|\bm/?t\b", t):
        return prefix + "MT"
    if re.search(r"automatic|\ba/?t\b", t):
        return prefix + "AT"
    if "single" in t or "1-speed" in t or "reduction" in t:
        return "Single-speed"
    return " ".join(text.split()) or None


def slug(*parts: str) -> str:
    joined = "-".join(p for p in parts if p)
    return re.sub(r"[^a-z0-9]+", "-", joined.lower()).strip("-")


@dataclass
class Variant:
    brand: str
    model: str
    variant: str
    price: float
    cc: int | None = None
    kw: float | None = None
    fuel: str = "Petrol"
    body: str = ""
    seats: int | None = None
    transmission: str | None = None
    imageUrl: str | None = None
    sourceUrl: str | None = None
    updatedAt: str = field(default_factory=lambda: date.today().isoformat())
    id: str = ""

    def __post_init__(self) -> None:
        for name in ("model", "variant", "transmission"):
            value = getattr(self, name)
            if isinstance(value, str):
                setattr(self, name, " ".join(value.replace("​", "").split()))
        if not self.id:
            self.id = slug(self.brand, self.model, self.variant)

    def to_json(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


class ScrapeError(Exception):
    """Raised when a brand's page no longer looks the way its parser expects."""
