"""Nissan (Edaran Tan Chong Motor): nissan.com.my is a WordPress/Elementor site.

* Each model's `/specifications/` page has one block per variant: "<variant> STARTING PRICE RM x",
  the spec table, then "PRICE PENINSULAR RM y ...". The page states these are "On-the-road price
  with SST ..., without insurance". We use the Peninsular figure (cheapest paint option).
* The model's main page has its own "Choose your <model>" cards with each variant's starting price.

A model is held back when a variant's Peninsular price differs from the starting price shown on
either page, when one variant shows two different prices, or when prices are only "Estimated".
The pages are hand-built, so spec values are only kept when they are unambiguous for the variant.
Only prices and specs are taken; Nissan's disclaimer reserves its text and images.
"""

from __future__ import annotations

import re

from ..common import ScrapeError, Variant, clean, fetch, money, short_transmission

SITE = "https://nissan.com.my/v2"
BRAND = "Nissan"
BODY = {"almera": "Sedan", "kicks": "SUV", "navara": "Pickup", "navara-sc": "Pickup", "serena": "MPV",
        "serena-e-power": "MPV", "x-trail": "SUV", "leaf": "Hatchback", "nv200": "Van", "ariya": "SUV"}
_STOP = re.compile(r"\b(SABAH|SARAWAK|LABUAN|LANGKAWI)\b")


def model_slugs(page: str) -> list[str]:
    """Model page slugs ('almera', 'serena-e-power', ...) linked from a vehicles menu page."""
    slugs = set(re.findall(r'href="https://nissan\.com\.my/v2/nissan-([a-z0-9]+(?:-[a-z0-9]+){0,2})/"', page))
    return sorted(s for s in slugs if s not in ("intelligent-mobility",))


def cards(page: str) -> dict[str, float | None]:
    """Variant name -> 'STARTING PRICE' from the carousel cards on a page. A name shown with two
    different prices maps to None."""
    out: dict[str, float | None] = {}
    for name, price in re.findall(r'carousel-title[^"]*">((?:(?!</h2>).)*)</h2>\s*<div[^>]*>\s*<p[^>]*>\s*STARTING PRICE\s*</p>\s*<h4[^>]*>\s*RM\s*([0-9,.]+)', page, re.S):
        name, value = clean(name), money(price)
        out[name] = value if out.get(name, value) == value else None
    return out


def _litres(name: str) -> float | None:
    m = re.search(r"\b(\d\.\d)\s*L\b", name, re.I)
    return float(m.group(1)) if m else None


def _cc(block: str, name: str) -> int | None:
    m = re.search(r"DISPLACEMENT(?: \(CC\))?:? ((?:[0-9][0-9,]{2,5}\s?)+)", block, re.I)
    if not m:
        return None
    values = [int(v.replace(",", "")) for v in re.findall(r"[0-9][0-9,]{2,5}", m.group(1))]
    litres = _litres(name)
    if litres:
        close = {v for v in values if abs(v / 1000 - litres) <= 0.06}
        return close.pop() if len(close) == 1 else None
    return values[0] if len(set(values)) == 1 else None


def _transmission(block: str, name: str) -> str | None:
    """From the variant name (A/T, M/T), else from the spec block when it names exactly one type."""
    if re.search(r"\bA/T\b", name):
        return "AT"
    if re.search(r"\bM/T\b", name):
        return "MT"
    found = {short_transmission(t) for t in re.findall(
        r"TRANSMISSION(?: / SUSPENSION)?(?: SYSTEM)? TYPE ((?:\d+-speed\s)?(?:manual|automatic|x-tronic cvt|xtronic cvt|cvt))\b", block, re.I)}
    return found.pop() if len(found) == 1 else None


def parse_spec_page(page: str) -> tuple[dict[str, dict], list[str]]:
    """(variant name -> {price, start, cc, kw, transmission, fuel}, problems) from a specifications page."""
    text = clean(page)
    page_diesel = bool(re.search(r"diesel|common ?rail", text, re.I)) and not re.search(r"petrol|gasoline", text, re.I)
    if not re.search(r"on-the-road price.{0,80}without insurance", text, re.I):
        raise ScrapeError("Nissan: specifications page no longer says on-the-road without insurance")
    found: dict[str, dict] = {}
    problems: list[str] = []
    for name, start in cards(page).items():
        if start is None:
            problems.append(f"{name} has two different starting prices")
            continue
        for m in re.finditer(r"(?<![\w-])" + re.escape(f"{name} STARTING PRICE RM ") + r"([0-9,.]+) Key:", text):
            block = text[m.end():]
            nxt = re.search(r"STARTING PRICE|Key: Include", block)
            block = block[: nxt.start()] if nxt else block
            pm = re.search(r"PRICE PENINSULAR (.*)", block)
            if not pm:
                continue
            pen_text = _STOP.split(pm.group(1))[0]
            price = money(re.sub(r"(?i)rm", " ", pen_text))
            if price is None:
                continue
            if re.search(r"\bestimated\b", block, re.I):
                problems.append(f"{name} price is only estimated")
            info = {
                "price": price,
                "start": money(m.group(1)),
                "cc": _cc(block, name),
                "transmission": _transmission(block, name),
                "kw": float(k.group(1)) if (k := re.search(r"\b(\d{2,3})\s?kW\b", name)) else None,
                "diesel": page_diesel or bool(re.search(r"diesel|common ?rail", block, re.I)),
            }
            prev = found.get(name)
            if prev and prev["price"] != price:
                problems.append(f"{name} is shown at both RM{prev['price']:,.0f} and RM{price:,.0f}")
            found.setdefault(name, info)
    return found, problems


def build_model(slug: str, model: str, spec: dict[str, dict], problems: list[str],
                model_page_cards: dict[str, float | None], spec_url: str) -> tuple[list[Variant], str | None]:
    if problems:
        return [], f"{spec_url}: " + "; ".join(problems)
    if not spec:
        return [], None
    conflicting = [n for n, p in model_page_cards.items() if p is None]
    if conflicting:
        return [], f"the model page shows two different starting prices for {', '.join(conflicting)}"
    for name, info in spec.items():
        if info["start"] is not None and abs(info["start"] - info["price"]) > 1:
            return [], f"{name} starts at RM{info['start']:,.0f} but its Peninsular price is RM{info['price']:,.0f} on {spec_url}"
        shown = model_page_cards.get(name)
        if shown is not None and abs(shown - info["price"]) > 1:
            return [], f"{name} is RM{info['price']:,.0f} on {spec_url} but RM{shown:,.0f} on the model page"
    if model_page_cards:
        cheapest, page_min = min(i["price"] for i in spec.values()), min(model_page_cards.values())
        if abs(cheapest - page_min) > 1:
            return [], f"specifications say from RM{cheapest:,.0f} but the model page says from RM{page_min:,.0f}"
    out = []
    for name, info in spec.items():
        fuel = "EV" if slug in ("leaf", "ariya") else "Hybrid" if "e-power" in slug else "Diesel" if info["diesel"] else "Petrol"
        out.append(
            Variant(
                brand=BRAND,
                model=model,
                variant=name,
                price=info["price"],
                cc=info["cc"] if fuel != "EV" else None,
                kw=info["kw"] if fuel == "EV" else None,
                fuel=fuel,
                body=BODY.get(slug, ""),
                transmission=info["transmission"] or ("Single-speed" if fuel == "EV" else None),
                sourceUrl=spec_url,
            )
        )
    return out, None


def model_name(slug: str) -> str:
    return {"navara-sc": "Navara Single Cab", "serena-e-power": "Serena e-POWER", "x-trail": "X-Trail",
            "nv200": "NV200"}.get(slug, slug.replace("-", " ").title())


def scrape() -> tuple[list[Variant], dict[str, str]]:
    out: list[Variant] = []
    held: dict[str, str] = {}
    for slug in model_slugs(fetch(f"{SITE}/vehicle-menu/")):
        spec_url = f"{SITE}/nissan-{slug}/specifications/"
        try:
            spec_page = fetch(spec_url)
        except OSError:
            continue  # news posts share the nissan- prefix; only model pages have /specifications/
        try:
            spec, problems = parse_spec_page(spec_page)
        except ScrapeError:
            continue
        model_cards = cards(fetch(f"{SITE}/nissan-{slug}/"))
        variants, why = build_model(slug, model_name(slug), spec, problems, model_cards, spec_url)
        if why:
            held[model_name(slug)] = why
        out.extend(variants)
    if not out and not held:
        raise ScrapeError("Nissan: no priced models found")
    return out, held
