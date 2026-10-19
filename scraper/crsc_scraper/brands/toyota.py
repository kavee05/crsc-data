"""Toyota (UMW Toyota): toyota.com.my is an AEM site that embeds its data as `window.data_*` JSON.

* The price list page (`data_price_list`) gives every grade's on-the-road price including SST,
  without insurance, per state and buyer type. We use Peninsular Malaysia, Individual Private.
  "From" prices are the cheapest paint option.
* Each model page has its own grade price table (`data_explore`) and a starting price
  (`data_mlp_overview`). A model is held back when a grade's price there differs from the
  price list, when the starting price is not its cheapest grade, or when a price is only
  "Estimated" (pre-launch).
* Each grade's specification page (`data_model_specs`) gives cc / motor kW, transmission and,
  for some models, seating capacity.

Only prices and specs are taken; Toyota's terms reserve its text and images, so no image URLs.
"""

from __future__ import annotations

import re

from ..common import ScrapeError, Variant, clean, fetch, integer, js_object, money, short_transmission, table_rows

SITE = "https://www.toyota.com.my"
PRICE_LIST = f"{SITE}/en/price-and-model-tools/price-list.html"
BRAND = "Toyota"
STATE = "Peninsular Malaysia"
BUYER = "Individual Private"
#: Body-type tabs are read before the "GR & GR-S" and "ELECTRIFIED" tabs, which repeat grades.
BODY_TABS = {"sedan": "Sedan", "hatchback": "Hatchback", "suv": "SUV", "mpv": "MPV", "pick-up-and-van": None}
BODY_DESC = {"coupe": "Coupe", "hatchback": "Hatchback", "sedan": "Sedan", "pickup": "Pickup", "van": "Van",
             "crossover": "SUV", "suv": "SUV", "premium suv": "SUV", "mpv": "MPV"}
_CODES = {"GR", "SLWB", "HEV", "BEV", "SUV"}


def _price(text: str | None) -> float | None:
    """'From RM90,600.00' -> 90600.0. 'Estimated Price ...' is not an official price -> None."""
    if not text or "estimat" in text.lower():
        return None
    return money(text.replace("RM", " "))


def model_name(header: str) -> str:
    """'ALL-NEW VIOS HEV GR SPORT' -> 'Vios', 'INNOVA ZENIX HYBRID ELECTRIC' -> 'Innova Zenix'."""
    name = clean(header)
    name = re.sub(r"(?i)^explore your\s+", "", name)
    name = re.sub(r"(?i)^(all[- ]new|new)\s+", "", name)
    while True:
        stripped = re.sub(r"(?i)\s+(hybrid electric|hev|bev|gr sport)$", "", name)
        if stripped == name:
            break
        name = stripped
    words = []
    for w in name.split():
        if any(ch.isdigit() for ch in w) or w.upper() in _CODES:
            words.append(w if any(ch.islower() for ch in w) else w.upper())
        else:
            words.append(w.capitalize())
    return " ".join(words)


def variant_name(grade: str, model: str, fuel: str) -> str:
    name = re.sub(rf"(?i)^{re.escape(model)}\b\s*", "", clean(grade)).strip()
    if not name:
        name = {"EV": "EV", "Hybrid": "HEV"}.get(fuel, "Standard")
    return name


def _fuel(classes: list[str], name: str) -> str | None:
    text = " ".join(classes).lower()
    if "battery" in text or re.search(r"\bbev\b", name, re.I):
        return "EV"
    if "hybrid" in text or re.search(r"(?i)\bhev\b|hybrid", name):
        return "Hybrid"
    if "diesel" in text:
        return "Diesel"
    if "petrol" in text:
        return "Petrol"
    return None


def parse_price_list(page: str) -> dict[str, dict]:
    """Grade id -> {name, price, url, body, fuel} for Peninsular, Individual Private buyers."""
    data = js_object(page, "window.data_price_list")
    if not data or "states" not in data:
        raise ScrapeError("Toyota: price list data missing")
    if "without insurance" not in (data.get("desc") or "").lower():
        raise ScrapeError("Toyota: price list no longer says 'without insurance'")
    state = next((s for s in data["states"] if s.get("id") == STATE), None)
    if not state:
        raise ScrapeError(f"Toyota: no {STATE} prices")
    tabs = sorted(state["tabs"], key=lambda t: t.get("id") not in BODY_TABS)
    grades: dict[str, dict] = {}
    for tab in tabs:
        for item in tab.get("items", []):
            body = BODY_TABS.get(tab.get("id")) or BODY_DESC.get((item.get("desc") or "").lower())
            for cat in item.get("categories", []):
                for g in cat.get("gradeLists", []):
                    gid = g.get("id")
                    if not gid or gid in grades:
                        continue
                    ip = next((p for p in g.get("priceTypes", []) if p.get("type") == BUYER), None)
                    price = _price(ip and ip.get("price"))
                    if price is None:
                        continue  # commercial-only (e.g. panel van) or estimated
                    grades[gid] = {
                        "name": clean(g.get("header", "")),
                        "price": price,
                        "url": g.get("url"),
                        "body": body,
                        "fuel": _fuel(g.get("class") or [], g.get("header", "")),
                    }
    if not grades:
        raise ScrapeError("Toyota: no grades on the price list")
    return grades


def parse_model_page(page: str) -> tuple[str, float | None, dict[str, float | None]]:
    """(model name, starting price, grade id -> Peninsular Individual Private price) from a model page."""
    explore = js_object(page, "window.data_explore")
    overview = js_object(page, "window.data_mlp_overview") or {}
    if not explore:
        raise ScrapeError("Toyota: model page has no price table")
    prices: dict[str, float | None] = {}
    for tab in explore.get("tabs", []):
        for g in tab.get("lists", []):
            opt = next((o for o in g.get("priceOptionList", []) if o.get("state") == STATE and o.get("priceType") == BUYER), None)
            prices[g.get("id")] = _price(opt.get("price")) if opt else None
            if opt and "estimat" in (opt.get("price") or "").lower():
                prices[g.get("id")] = float("nan")  # marks a pre-launch estimate
    start = _price((overview.get("footer") or {}).get("price"))
    return model_name(explore.get("title", "")), start, prices


def parse_specs(page: str) -> dict:
    """cc, motor kW, seats and transmission from a grade's specification page."""
    data = js_object(page, "window.data_model_specs") or {}
    rows: list[tuple[str, str, str]] = []
    for acc in data.get("accordionList", []):
        for label, value in table_rows(acc.get("freeHTML") or acc.get("freeHtml") or ""):
            rows.append((acc.get("header", ""), label, value))

    def find(label_re: str, section_re: str = "") -> str | None:
        for section, label, value in rows:
            if re.search(label_re, label, re.I) and re.search(section_re, section, re.I):
                return value
        return None

    trans = find(r"^transmission( type)?$") or next((v for s, l, v in rows if re.search(r"transmission", s, re.I)), None)
    motor = find(r"^max(imum)?\.? ?(power|output)", r"motor")
    seats = find(r"seating capacity|^seats?$")
    return {
        "cc": integer(find(r"^displacement") or ""),
        "kw": float(integer(motor)) if motor and "kw" in motor.lower() and integer(motor) else None,
        "seats": integer(seats or "") or None,
        "transmission": short_transmission(trans),
    }


def build_variants(model: str, start: float | None, page_prices: dict[str, float | None], grades: dict[str, dict],
                   specs: dict[str, dict], page_url: str) -> tuple[list[Variant], str | None]:
    """Variants for one model page, or a reason to hold the model back."""
    listed = {gid: g for gid, g in grades.items() if gid in page_prices}
    if any(p != p for p in page_prices.values() if p is not None):  # NaN = estimated
        return [], f"{page_url} only shows estimated prices"
    for gid, g in listed.items():
        if page_prices[gid] is not None and abs(page_prices[gid] - g["price"]) > 1:
            return [], f"price list says {g['name']} is RM{g['price']:,.0f} but {page_url} says RM{page_prices[gid]:,.0f}"
    if not listed:
        return [], None
    cheapest = min(g["price"] for g in listed.values())
    if start is not None and abs(cheapest - start) > 1:
        return [], f"price list says from RM{cheapest:,.0f} but {page_url} says from RM{start:,.0f}"
    out = []
    for gid, g in listed.items():
        s = specs.get(gid, {})
        fuel = g["fuel"] or ("EV" if s.get("kw") and not s.get("cc") else "Petrol")
        out.append(
            Variant(
                brand=BRAND,
                model=model,
                variant=variant_name(g["name"], model, fuel),
                price=g["price"],
                cc=s.get("cc") if fuel != "EV" else None,
                kw=s.get("kw") if fuel == "EV" else None,
                fuel=fuel,
                body=g["body"] or "",
                seats=s.get("seats"),
                transmission=s.get("transmission"),
                sourceUrl=page_url,
            )
        )
    return out, None


def model_urls() -> list[str]:
    sitemap = fetch(f"{SITE}/sitemap.xml")
    return sorted(set(re.findall(r"<loc>(https://www\.toyota\.com\.my/en/models/[a-z0-9-]+\.html)</loc>", sitemap)))


def scrape() -> tuple[list[Variant], dict[str, str]]:
    grades = parse_price_list(fetch(PRICE_LIST))
    out: list[Variant] = []
    held: dict[str, str] = {}
    for url in model_urls():
        try:
            model, start, page_prices = parse_model_page(fetch(url))
        except ScrapeError:
            continue  # not a model page with prices (e.g. discontinued)
        specs = {gid: parse_specs(fetch(grades[gid]["url"])) for gid in page_prices if gid in grades and grades[gid].get("url")}
        variants, why = build_variants(model, start, page_prices, grades, specs, url)
        if why:
            held[model] = why
        out.extend(variants)
    if not out and not held:
        raise ScrapeError("Toyota: no priced models found")
    return out, held
