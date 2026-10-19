"""Perodua: model pages carry a 'Price & Specification' table per variant.

Prices are "On the road price with Sales Tax and without insurance", Peninsular Malaysia
(the page's default region).
"""

from __future__ import annotations

import json
import re

from ..common import ScrapeError, Variant, clean, fetch, integer, money

BASE = "https://www.perodua.com.my"
BRAND = "Perodua"
SKIP = ("choose-model", "smart-drive-assist")
BODY = {"hatchback": "Hatchback", "sedan": "Sedan", "suv": "SUV", "mpv": "MPV"}


def model_urls() -> list[str]:
    sitemap = fetch(f"{BASE}/sitemap.xml")
    urls = re.findall(r"<loc>(https://www\.perodua\.com\.my/our-models/[a-z-]+/[a-z0-9-]+)</loc>", sitemap)
    return sorted({u for u in urls if not any(s in u for s in SKIP)})


def parse_model_page(page: str, url: str) -> list[Variant]:
    if "PRICE & SPECIFICATION" not in page.upper():
        raise ScrapeError(f"No price table on {url}")
    segment = url.rstrip("/").split("/")[-2]
    body = BODY.get(segment, segment.title())

    ld = {}
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") in ("Car", "Product", "Vehicle"):
            ld = data
            break
    model_name = re.sub(r"^Perodua\s+", "", ld.get("name") or url.rstrip("/").split("/")[-1].replace("-", " ").title()).strip()
    seats = ld.get("vehicleSeatingCapacity")
    fuel = ld.get("fuelType") or "Petrol"

    head = re.search(r'<table class="table-head">(.*?)</table>', page, re.S)
    if not head:
        raise ScrapeError(f"Spec labels missing on {url}")
    labels = [clean(v) for v in re.findall(r'<td class="hide-[a-z]+"[^>]*>(.*?)</td>', head.group(1), re.S)]

    starts = [m.start() for m in re.finditer(r'class="car-name"', page)]
    variants: list[Variant] = []
    for i, start in enumerate(starts):
        seg = page[start: starts[i + 1] if i + 1 < len(starts) else start + 30000]
        price_m = re.search(r'id="price-model-\d+" class="font-price">([^<]+)<', seg)
        if not price_m:
            continue  # nav/menu cards share the class but have no price id
        name = clean(re.search(r'class="car-name">(.*?)</span>', seg, re.S).group(1))
        trans_m = re.search(r'class="mycar-type">(.*?)</span>', seg, re.S)
        transmission = clean(trans_m.group(1)).strip("() ") if trans_m else None
        values = [clean(v) for v in re.findall(r'<td class="hide-[a-z]+"[^>]*>(.*?)</td>', seg, re.S)]
        specs = dict(zip(labels, values)) if len(values) == len(labels) else {}
        cc = next((integer(v) for k, v in specs.items() if "displacement" in k.lower()), None)
        kw_text = next((v for k, v in specs.items() if "output" in k.lower() and "kw" in k.lower()), None)
        kw = float(integer(kw_text.split("/")[0])) if kw_text and integer(kw_text.split("/")[0]) else None
        img_m = re.search(r'<img src="([^"]+)"[^>]*class="car-img"', page[max(0, start - 600): start])
        price = money(price_m.group(1))
        if price is None:
            continue
        variant_name = re.sub(rf"^{re.escape(model_name)}\s*", "", name, flags=re.I).strip() or name
        variants.append(
            Variant(
                brand=BRAND,
                model=model_name,
                variant=variant_name,
                price=price,
                cc=cc,
                kw=kw if fuel.upper() == "EV" else None,
                fuel="EV" if fuel.upper() in ("EV", "ELECTRIC") else fuel.title(),
                body=body,
                seats=int(seats) if seats else None,
                transmission=transmission or None,
                imageUrl=(BASE + img_m.group(1).replace("&amp;", "&")) if img_m and img_m.group(1).startswith("/") else None,
                sourceUrl=url,
            )
        )
    if not variants:
        raise ScrapeError(f"No variants parsed on {url}")
    return variants


def scrape() -> tuple[list[Variant], dict[str, str]]:
    out: list[Variant] = []
    for url in model_urls():
        out.extend(parse_model_page(fetch(url), url))
    return out, {}
