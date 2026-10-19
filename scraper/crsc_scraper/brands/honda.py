"""Honda (Honda Malaysia): honda.com.my model pricing pages read a small public JSON API.

* `/model/<slug>/pricing` lists the model's variant ids and server-renders a price table.
* `/asset/api/pricing_variant_<id>_peninsular-malaysia.json` gives "retail-price", which the
  page labels "Retail Price Without Insurance" (selling price + road tax, registration,
  number plate and ownership endorsement fees): the on-the-road price without insurance.
* `/asset/api/fullspec_variant_<id>.json` gives displacement, motor kW and transmission.

A model is held back when the API disagrees with the price table rendered on its own pricing
page, or when the "From RM" shown for it in the site menu is not its cheapest variant.
Only prices and specs are taken; Honda's terms reserve its text and images, so no image URLs.
"""

from __future__ import annotations

import json
import re

from ..common import ScrapeError, Variant, clean, fetch, fetch_json, integer, money, short_transmission

SITE = "https://www.honda.com.my"
API = f"{SITE}/asset/api"
BRAND = "Honda"
REGION = "peninsular-malaysia"
#: Honda's site does not label body types; these are the models it sells.
BODY = {"city": "Sedan", "city-hatchback": "Hatchback", "civic": "Sedan", "crv": "SUV", "hrv": "SUV",
        "wr-v": "SUV", "type-r": "Hatchback", "en1": "SUV", "prelude": "Coupe", "accord": "Sedan", "br-v": "SUV"}


def parse_menu(page: str) -> list[tuple[str, str, float | None]]:
    """(slug, model name, menu 'From RM' price) for each model in the site's model menu."""
    start = page.find('class="dd-content ddmodel"')
    menu = page[start: page.find("</ul>", start)] if start >= 0 else ""
    out = []
    for href, block in re.findall(r'<a href="https://www\.honda\.com\.my/model/([a-z0-9-]+)/?">(.*?)</a>', menu, re.S):
        name = re.search(r'class="model-name">([^<]*)<', block)
        price = re.search(r'class="model-detail">\s*From\s*RM\s*([0-9,.]+)', block)
        if name and href not in {o[0] for o in out}:
            out.append((href, clean(name.group(1)), money(price.group(1)) if price else None))
    if not out:
        raise ScrapeError("Honda: model menu not found")
    return out


def parse_pricing_page(page: str) -> tuple[dict[int, str], dict[int, float]]:
    """(variant id -> name, variant id -> price shown in the page's own table) from a pricing page."""
    if "Retail Price Without Insurance" not in page:
        raise ScrapeError("Honda: pricing page no longer shows 'Retail Price Without Insurance'")
    ids = re.search(r"var __variants = \[([0-9,\s]*)\]", page)
    if not ids:
        raise ScrapeError("Honda: variant list missing on pricing page")
    shown = [int(x) for x in ids.group(1).split(",") if x.strip()]
    names = {int(vid): clean(name) for vid, name in re.findall(r'<li data-vid="(\d+)" data-col="0">([^<]*)</li>', page)}
    for vid in shown:
        names.setdefault(vid, "")
    table = {int(col): money(v) for col, v in re.findall(r'id="col(\d+)-retail-price">([^<]*)<', page)}
    page_prices = {vid: table[i] for i, vid in enumerate(shown) if table.get(i)}
    return names, page_prices


def parse_price(data: dict) -> float | None:
    if (data.get("meta") or {}).get("remarks") != "success":
        return None
    return money((data.get("payload") or {}).get(REGION, {}).get("retail-price") or "")


def parse_specs(data: dict) -> dict:
    spec = (data.get("payload") or {}).get("specitem") or {}
    items = {k.strip(): v for k, v in spec.items() if v}
    cc = integer(items.get("engine-displacement") or "")
    motor = next((v for k, v in items.items() if k.startswith("Motor Maximum Power (kW)")), None)
    trans = next((v for k, v in items.items() if k.startswith("transmission-type")), None)
    power = (items.get("engine-max-power") or "").lower()
    if not cc and motor:
        fuel = "EV"
    elif "e-cvt" in (trans or "").lower() or "motor" in power:
        fuel = "Hybrid"
    else:
        fuel = "Petrol"
    return {"cc": cc, "kw": float(integer(motor)) if motor and integer(motor) else None, "fuel": fuel,
            "transmission": short_transmission(trans) or ("Single-speed" if fuel == "EV" else None)}


def consistent_spec(name: str, spec: dict) -> dict:
    """Drop spec fields that contradict the variant's own name (Honda's spec data is sometimes
    attached to the wrong variant, e.g. a petrol RS showing the e:HEV's e-CVT)."""
    spec = dict(spec)
    named_hybrid = "e:hev" in name.lower()
    if spec.get("fuel") != "EV" and (spec.get("fuel") == "Hybrid") != named_hybrid:
        spec["transmission"] = None
        if spec.get("fuel") == "Hybrid" or named_hybrid:
            spec["fuel"] = "Hybrid" if named_hybrid else "Petrol"
    litres = re.search(r"\b(\d\.\d)\s*L\b", name)
    if litres and spec.get("cc") and abs(spec["cc"] / 1000 - float(litres.group(1))) > 0.06:
        spec["cc"] = None
    spec.setdefault("fuel", "Hybrid" if named_hybrid else "Petrol")
    return spec


def variant_name(name: str, model: str, spec: dict) -> str:
    v = re.sub(rf"(?i)^(honda\s+)?{re.escape(model)}\b\s*", "", name).strip()
    if v:
        return v
    if spec.get("cc"):
        return f"{spec['cc'] / 1000:.1f}L" + (" e:HEV" if spec.get("fuel") == "Hybrid" else "")
    return "EV" if spec.get("fuel") == "EV" else "Standard"


def build_model(slug: str, model: str, menu_price: float | None, names: dict[int, str], page_prices: dict[int, float],
                api_prices: dict[int, float | None], specs: dict[int, dict]) -> tuple[list[Variant], str | None]:
    url = f"{SITE}/model/{slug}/pricing"
    for vid, shown in page_prices.items():
        if api_prices.get(vid) is not None and abs(api_prices[vid] - shown) > 1:
            return [], f"price data says {names.get(vid) or vid} is RM{api_prices[vid]:,.0f} but {url} shows RM{shown:,.0f}"
    priced = {vid: p for vid, p in api_prices.items() if p is not None}
    if not priced:
        return [], None
    cheapest = min(priced.values())
    if menu_price is not None and abs(cheapest - menu_price) > 1:
        return [], f"price data says from RM{cheapest:,.0f} but the honda.com.my menu says from RM{menu_price:,.0f}"
    out = []
    for vid, price in priced.items():
        spec = consistent_spec(names.get(vid, ""), specs.get(vid, {}))
        fuel = spec["fuel"]
        out.append(
            Variant(
                brand=BRAND,
                model=model,
                variant=variant_name(names.get(vid, ""), model, spec),
                price=price,
                cc=spec.get("cc") if fuel != "EV" else None,
                kw=spec.get("kw") if fuel == "EV" else None,
                fuel=fuel,
                body=BODY.get(slug, ""),
                transmission=spec.get("transmission"),
                sourceUrl=url,
            )
        )
    return out, None


def scrape() -> tuple[list[Variant], dict[str, str]]:
    out: list[Variant] = []
    held: dict[str, str] = {}
    for slug, model, menu_price in parse_menu(fetch(SITE + "/")):
        names, page_prices = parse_pricing_page(fetch(f"{SITE}/model/{slug}/pricing"))
        api_prices, specs = {}, {}
        for vid in names:
            try:
                api_prices[vid] = parse_price(fetch_json(f"{API}/pricing_variant_{vid}_{REGION}.json"))
            except (OSError, json.JSONDecodeError):
                api_prices[vid] = None  # variant without a Peninsular price
                continue
            specs[vid] = parse_specs(fetch_json(f"{API}/fullspec_variant_{vid}.json"))
        variants, why = build_model(slug, model, menu_price, names, page_prices, api_prices, specs)
        if why:
            held[model] = why
        out.extend(variants)
    if not out and not held:
        raise ScrapeError("Honda: no priced models found")
    return out, held
