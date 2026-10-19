"""Chery (Chery Auto Malaysia): chery.my model pages publish prices only as PDF price lists.

Each model page links a "Pricelist WM" PDF (Peninsular Malaysia "CUSTOMER PRICE LIST"), a table
with one Private/Individual and one Corporate/Company column per variant, listing the net selling
price, each registration fee, and "On The Road Price Without Insurance (RM)". We take the
Private/Individual on-the-road figure.

The HTML pages show no prices, so the cross-check is the price list's own arithmetic: for every
variant, net selling price + the listed fees must equal the on-the-road price. Otherwise (or when a
figure cannot be read cleanly) the model is held back. Chery's pages publish no engine specs, so
cc is left empty and body type is only set when the model page calls it an SUV; EV motor kW is taken from the variant name when the price list gives it.
"""

from __future__ import annotations

import re

from .. import pdf
from ..common import ScrapeError, Variant, clean, fetch, fetch_bytes

SITE = "https://www.chery.my"
BRAND = "Chery"
_AMOUNT = re.compile(r"^\d{1,3}(?:,\d{3})*\.\d{2}$")
_CODES = {"PHEV", "CSH", "SUV", "EV"}


def model_menu(page: str) -> list[tuple[str, str]]:
    """(model name, page url) from the site's MODELS menu."""
    i = page.find('itemprop="name">MODELS<')
    menu = page[i: page.find("</ul>", i)] if i >= 0 else ""
    out = []
    for url, name in re.findall(r'<a href="(https://www\.chery\.my/[a-z0-9/-]+)"[^>]*><span itemprop="name">([^<]+)</span>', menu):
        if url not in {u for _, u in out}:
            out.append((model_name(name), url))
    if not out:
        raise ScrapeError("Chery: model menu not found")
    return out


def model_name(raw: str) -> str:
    """'CHERY O5' -> 'O5', 'TIGGO 7 PHEV CSH' -> 'Tiggo 7 PHEV', 'Omoda E5' -> 'Omoda E5'."""
    words = [w for w in clean(raw).split() if w.upper() not in ("CHERY", "CSH")]
    return " ".join(w.upper() if any(c.isdigit() for c in w) or w.upper() in _CODES else w.capitalize() for w in words)


def price_list_url(page: str) -> str | None:
    """The Peninsular ("WM") price list linked from a model page."""
    m = re.search(r'<a href="([^"]+\.pdf)"[^>]*data-cta="price_west"', page) or re.search(r'<a href="([^"]+\.pdf)"[^>]*>\s*WM\s*</a>', page)
    if not m:
        return None
    return m.group(1) if m.group(1).startswith("http") else SITE + m.group(1)


def _rows(pieces: list[pdf.Piece], tol: float = 1.6) -> list[tuple[float, list[pdf.Piece]]]:
    rows: list[tuple[float, list[pdf.Piece]]] = []
    for p in sorted(pieces, key=lambda p: -p.y):
        if rows and abs(rows[-1][0] - p.y) <= tol:
            rows[-1][1].append(p)
        else:
            rows.append((p.y, [p]))
    return [(y, sorted(ps, key=lambda p: p.x)) for y, ps in rows]  # stable: same-x pieces keep drawing order


def _compact(ps: list[pdf.Piece]) -> str:
    return re.sub(r"\s+", "", "".join(p.text for p in ps)).lower()


def _amounts(ps: list[pdf.Piece]) -> list[tuple[float, float | None]]:
    """Money amounts in a row with the x where each starts. Unreadable amounts come back as None."""
    out: list[tuple[float, float | None]] = []
    buf, start = "", 0.0
    for p in ps:
        for ch in p.text:
            if ch.isdigit() or (buf and ch in ",."):
                if not buf:
                    start = p.x
                buf += ch
                if re.search(r"\.\d{2}$", buf):
                    out.append((start, float(buf.replace(",", "")) if _AMOUNT.match(buf) else None))
                    buf = ""
            elif buf and not ch.isspace():
                buf = ""
            elif buf and ch.isspace() and "." not in buf and len(buf) > 3:
                buf = ""
    return out


def parse_price_list(data: bytes) -> tuple[str | None, list[dict], list[str]]:
    """(model name printed on the list, [{variant, price}], problems) from a price-list PDF."""
    return parse_pieces(pdf.pieces(data))


def parse_pieces(pieces: list[pdf.Piece]) -> tuple[str | None, list[dict], list[str]]:
    rows = _rows(pieces)
    texts = [(y, ps, _compact(ps)) for y, ps in rows]
    whole = "".join(t for _, _, t in texts)
    if not rows:
        raise ScrapeError("Chery: price list has no readable text")
    if "peninsularmalaysia" not in whole or "customerpricelist" not in whole:
        raise ScrapeError("Chery: not a Peninsular Malaysia customer price list")
    find = lambda key: next(((y, ps) for y, ps, t in texts if key in t), (None, None))
    otr_y, _ = find("theroadpricewithout")
    ins_y = next((y for y, _, t in texts if otr_y is not None and y < otr_y and t.startswith("insurance")), None)
    net_y, _ = find("netsellingprice")
    desc_y, _ = find("description")
    cpl_y, _ = find("customerpricelist")
    if None in (otr_y, ins_y, net_y, desc_y, cpl_y):
        raise ScrapeError("Chery: price list layout not recognised")

    otr = [a for y, ps, _ in texts if ins_y - 1 < y < otr_y + 1 for a in _amounts(ps)]
    if not otr or len(otr) % 2 or any(v is None for _, v in otr):
        return None, [], ["on-the-road prices could not be read"]
    columns = sorted(x for x, _ in otr)
    values = {x: v for x, v in otr}

    header = sorted((p for y, ps, _ in texts if net_y + 3 < y < desc_y - 2 for p in ps), key=lambda p: p.x)
    order = re.sub(r"\s+", "", "".join(p.text for p in header)).lower()
    ind = re.search(r"private|individual", order)
    corp = re.search(r"corporate|company", order)
    if not ind or not corp or ind.start() > corp.start():
        return None, [], ["Private/Individual column not found before the Corporate column"]

    def column(x: float) -> int:
        return min(range(len(columns)), key=lambda i: abs(columns[i] - x))

    net_amounts = [a for y, ps, _ in texts if abs(y - net_y) <= 3 for a in _amounts(ps)]
    fee_amounts = [a for y, ps, _ in texts if otr_y + 1 < y < net_y - 3 for a in _amounts(ps)]
    model_line = " ".join(clean("".join(p.text for p in ps)) for y, ps, _ in texts if desc_y + 8 < y < cpl_y - 1).strip()

    names: dict[int, str] = {}
    boundaries = [(columns[2 * k + 1] + columns[2 * k + 2]) / 2 for k in range(len(columns) // 2 - 1)]
    for p in sorted((p for y, ps, _ in texts if desc_y - 2 <= y <= desc_y + 8 for p in ps), key=lambda p: p.x):
        if p.text.strip().lower() == "description":
            continue
        k = sum(p.x > b for b in boundaries)
        names[k] = names.get(k, "") + p.text
    variants, problems = [], []
    for k in range(len(columns) // 2):
        name = re.sub(r"\s*-\s*", " - ", re.sub(r"\s+", " ", names.get(k, ""))).strip()
        ix = columns[2 * k]
        price = values[ix]
        net = [v for x, v in net_amounts if column(x) == 2 * k]
        fees = [v for x, v in fee_amounts if column(x) == 2 * k]
        if not name:
            problems.append(f"variant {k + 1} has no name")
        elif len(net) != 1 or None in net or None in fees or not fees:
            problems.append(f"{name}: net price or fees could not be read")
        elif abs(net[0] + sum(fees) - price) > 1:
            problems.append(f"{name}: RM{net[0]:,.2f} + fees RM{sum(fees):,.2f} does not add up to RM{price:,.2f}")
        variants.append({"variant": name, "price": price})
    return model_line or None, variants, problems


def build_model(model: str, variants: list[dict], problems: list[str], url: str, body: str = "") -> tuple[list[Variant], str | None]:
    if problems:
        return [], f"{url}: " + "; ".join(problems)
    out = []
    for v in variants:
        name = v["variant"]
        kw = re.search(r"(\d{2,3})\s?kW\b", name, re.I)
        if "PHEV" in model.upper():
            fuel = "PHEV"
        elif re.search(r"(?i)hybrid|\bdht\b", name):
            fuel = "Hybrid"
        elif kw and not re.search(r"(?i)turbo|tgdi", name):
            fuel = "EV"
        else:
            fuel = "Petrol"
        dct = re.search(r"(?i)\b(\d*)\s?DCT\b", name)
        transmission = (f"{dct.group(1)}-speed DCT" if dct.group(1) else "DCT") if dct else ("Single-speed" if fuel == "EV" else None)
        out.append(
            Variant(
                brand=BRAND,
                model=model,
                variant=name,
                price=v["price"],
                kw=float(kw.group(1)) if kw and fuel == "EV" else None,
                fuel=fuel,
                body=body,
                transmission=transmission,
                sourceUrl=url,
            )
        )
    return out, None


def scrape() -> tuple[list[Variant], dict[str, str]]:
    out: list[Variant] = []
    held: dict[str, str] = {}
    for model, page_url in model_menu(fetch(SITE + "/")):
        page = fetch(page_url)
        url = price_list_url(page)
        body = "SUV" if re.search(r"[^a-z]SUV[^a-z]", page) else ""
        if not url:
            held[model] = f"no Peninsular price list link found on {page_url}"
            continue
        try:
            _, variants, problems = parse_price_list(fetch_bytes(url))
        except ScrapeError as exc:
            held[model] = f"{url}: {exc}"
            continue
        variants_out, why = build_model(model, variants, problems, url, body)
        if why:
            held[model] = why
        out.extend(variants_out)
    if not out and not held:
        raise ScrapeError("Chery: no priced models found")
    return out, held
