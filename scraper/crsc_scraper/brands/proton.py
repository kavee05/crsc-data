"""Proton: proton.com is a headless Umbraco site with a public Content Delivery API.

Each model on sale has variants; each variant has region price groups (PM = Peninsular,
EM = East Malaysia, LB = Labuan, LW = Langkawi) per paint type, and each group has price
items. We use "On The Road (Without Insurance)" for Peninsular, cheapest paint type.
"""

from __future__ import annotations

import re

from ..common import ScrapeError, Variant, clean, fetch, fetch_json, integer

SITE = "https://www.proton.com"
API = f"{SITE}/umbraco/delivery/api/v2"
BRAND = "Proton"


def _usage(item: dict) -> list[str]:
    return [u.get("name", "") for u in (item.get("properties", {}).get("usage") or [])]


def _model_name(raw: str) -> str:
    name = re.sub(r"(?i)^all-new\s+", "", raw).strip()
    name = re.sub(r"(?i)\s+\d{4}$", "", name)
    name = re.sub(r"(?i)\s+(prime|flagship)&.*$", "", name)
    return name.upper() if re.match(r"^[A-Z]\d", name, re.I) else name.title()


def _body(model: str) -> tuple[str, int]:
    m = model.upper()
    if m.startswith("X90"):
        return "SUV", 7
    if m.startswith("X"):
        return "SUV", 5
    if m.startswith("E.MAS"):
        return "SUV", 5
    return "Sedan", 5


def _tidy(name: str) -> str:
    """"1.5TD PRIME X" -> "1.5TD Prime X": title-case words, keep codes with digits as-is."""
    words = []
    for w in name.replace("​", "").split():
        if any(ch.isdigit() for ch in w) or len(w) <= 2 or w.upper() in ("CVT", "DCT", "AWD", "FWD", "TGDI", "BSG"):
            words.append(w.upper() if not any(ch.isdigit() for ch in w) else w)
        else:
            words.append(w.capitalize())
    return " ".join(words)


_CODES = {"CVT", "DCT", "AWD", "FWD", "TGDI", "BSG", "MT", "AT", "X"}


def _tidy(name: str) -> str:
    """'1.5TD PRIME X' -> '1.5TD Prime X': title-case words, keep codes and numbers as-is."""
    words = []
    for w in name.replace("​", "").split():
        if any(ch.isdigit() for ch in w):
            words.append(w.upper())
        elif w.upper() in _CODES:
            words.append(w.upper())
        else:
            words.append(w.capitalize())
    return " ".join(words)


def parse_model(model: dict, items: list[dict], page_url: str | None = None) -> list[Variant]:
    model_name = _model_name(model["name"])
    body, seats = _body(model_name)
    page_url = page_url or SITE + "/models/" + model["route"]["path"].rstrip("/").split("/")[-1]
    variants = [i for i in items if i["contentType"] == "vehicleVariant" and "Sale" in _usage(i)]
    out: list[Variant] = []
    for v in variants:
        root = v["route"]["path"]
        prices = []
        for p in items:
            path = p["route"]["path"]
            if (
                p["contentType"] == "vehiclePriceItem"
                and path.startswith(root + "prices/")
                and re.match(r"(pm|peni)[a-z]*-", path[len(root + "prices/"):])
                and "without-insurance" in path
                and isinstance(p.get("properties", {}).get("price"), (int, float))
            ):
                prices.append(float(p["properties"]["price"]))
        if not prices:
            continue

        def spec(name_re: str) -> str | None:
            for c in items:
                if c["contentType"] == "vehicleConfigurationItem" and c["route"]["path"].startswith(root) and re.search(name_re, c["name"], re.I):
                    text = (c.get("properties") or {}).get("text") or {}
                    return clean(text.get("markup", ""))
            return None

        cc = integer(spec(r"^displacement") or "")
        power = spec(r"^max(imum)? power") or ""
        kw = integer(power.split("(")[0]) if power else None
        name = v["properties"].get("variantName") or v["name"]
        fuel = "EV" if re.search(r"(?i)\bev\b|electric", name + model_name) else ("Hybrid" if re.search(r"(?i)hybrid|hev", name) else "Petrol")
        poster = (v["properties"].get("poster") or [{}])[0].get("url")
        out.append(
            Variant(
                brand=BRAND,
                model=model_name,
                variant=_tidy(name),
                price=min(prices),
                cc=cc if fuel != "EV" else None,
                kw=float(kw) if (kw and fuel == "EV") else None,
                fuel=fuel,
                body=body,
                seats=seats,
                transmission=((v["properties"].get("transmissionType") or {}).get("name")),
                imageUrl=(SITE + poster) if poster and poster.startswith("/") else poster,
                sourceUrl=page_url,
            )
        )
    return out


def parse_page(page: str) -> tuple[str | None, float | None]:
    """The model id behind a page's price table, and the 'Starting Price' printed on it."""
    model_id = None
    i = page.find('"variantComparison"')
    if i >= 0:
        # The model's own id follows its route object: ..."route":{...,"startItem":{...}},"id":"<model id>"
        m = re.search(r'"model":\{"contentType":"vehicleModel".{0,600}?\}\},"id":"([0-9a-f-]{36})"', page[i:i + 3000], re.S)
        model_id = m.group(1) if m else None
    sm = re.search(r'RM</sup>\s*([0-9,]+)</strong></span></h4>\s*<p class="caption01">[^<]*(?:Starting|Introductory) Price', page)
    start = float(sm.group(1).replace(",", "")) if sm else None
    return model_id, start


def scrape() -> tuple[list[Variant], dict[str, str]]:
    """Returns variants plus models held back (model name -> reason)."""
    sitemap = fetch(f"{SITE}/sitemap.xml")
    pages = sorted({u for u in re.findall(r"<loc>(https://www\.proton\.com/models/[a-z0-9-]+)</loc>", sitemap)})
    models = {m["id"]: m for m in fetch_json(f"{API}/content?filter=contentType:vehicleModel&take=200").get("items", [])}
    out: list[Variant] = []
    held: dict[str, str] = {}
    for url in pages:
        model_id, start_price = parse_page(fetch(url))
        model = models.get(model_id or "")
        if not model or "Sale" not in _usage(model):
            continue
        items = fetch_json(f"{API}/content?fetch=descendants:{model_id}&take=1000").get("items", [])
        variants = parse_model(model, items, page_url=url)
        if not variants:
            continue
        cheapest = min(v.price for v in variants)
        if start_price is not None and abs(cheapest - start_price) > 1:
            held[variants[0].model] = f"price data says from RM{cheapest:,.0f} but {url} says from RM{start_price:,.0f}"
            continue
        out.extend(variants)
    if not out and not held:
        raise ScrapeError("Proton: no priced models found")
    return out, held
