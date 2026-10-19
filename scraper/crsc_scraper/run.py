"""Weekly price update.

    python -m crsc_scraper.run --out ..      # writes catalogue.json, version.json, report.md

Exit codes: 0 = published normally, 2 = changes need human review (opened as a PR by the
workflow), 1 = one or more brands failed (their last good data is kept; GitHub emails you).
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import date
from pathlib import Path

from .brands import chery, honda, nissan, perodua, proton, toyota
from .common import Variant

BRANDS = {
    "Perodua": perodua.scrape,
    "Proton": proton.scrape,
    "Toyota": toyota.scrape,
    "Honda": honda.scrape,
    "Nissan": nissan.scrape,
    "Chery": chery.scrape,
    # Mazda and BYD are not scraped: neither publishes a parseable price with a stated basis
    # (see README "Brands covered").
}

#: A single variant's price moving more than this share is held for review.
MAX_PRICE_CHANGE = 0.30
#: More than this share of a brand's variants disappearing at once is held for review.
MAX_REMOVED_SHARE = 0.20
MIN_PRICE, MAX_PRICE = 15_000, 5_000_000


def validate(v: Variant) -> list[str]:
    problems = []
    if not (MIN_PRICE <= v.price <= MAX_PRICE):
        problems.append(f"price {v.price:,.0f} outside RM{MIN_PRICE:,}–RM{MAX_PRICE:,}")
    if v.fuel != "EV" and v.cc is not None and not (600 <= v.cc <= 8000):
        problems.append(f"cc {v.cc} looks wrong")
    if not v.model or not v.variant:
        problems.append("missing model or variant name")
    return problems


def review_flags(brand: str, old: list[dict], new: list[dict]) -> list[str]:
    flags = []
    old_by_id = {v["id"]: v for v in old}
    new_by_id = {v["id"]: v for v in new}
    for vid, nv in new_by_id.items():
        ov = old_by_id.get(vid)
        if ov and ov["price"] and abs(nv["price"] - ov["price"]) / ov["price"] > MAX_PRICE_CHANGE:
            flags.append(f"{brand} {nv['model']} {nv['variant']}: RM{ov['price']:,.0f} → RM{nv['price']:,.0f}")
    removed = [v for vid, v in old_by_id.items() if vid not in new_by_id]
    if old and len(removed) / len(old) > MAX_REMOVED_SHARE:
        flags.append(f"{brand}: {len(removed)} of {len(old)} variants disappeared")
    return flags


def placeholder_models(variants: list[dict]) -> list[str]:
    """Models with 3+ variants that all share one price: usually placeholder CMS data."""
    prices: dict[str, set[float]] = {}
    counts: dict[str, int] = {}
    for v in variants:
        prices.setdefault(v["model"], set()).add(v["price"])
        counts[v["model"]] = counts.get(v["model"], 0) + 1
    return [m for m, p in prices.items() if counts[m] >= 3 and len(p) == 1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="..", help="data-site folder holding catalogue.json")
    parser.add_argument("--only", nargs="*", help="limit to these brands")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    out_dir = Path(args.out)
    cat_path = out_dir / "catalogue.json"
    previous = json.loads(cat_path.read_text(encoding="utf-8")) if cat_path.exists() else {"version": 0, "variants": []}
    old_by_brand: dict[str, list[dict]] = {}
    for v in previous.get("variants", []):
        old_by_brand.setdefault(v["brand"], []).append(v)

    merged: list[dict] = []
    failures: list[str] = []
    flags: list[str] = []
    changes: list[str] = []
    for brand, scrape in BRANDS.items():
        if args.only and brand not in args.only:
            merged.extend(old_by_brand.get(brand, []))
            continue
        try:
            scraped, held = scrape()
            good, bad = [], []
            for v in scraped:
                (bad if validate(v) else good).append(v)
            for v in bad:
                flags.append(f"{brand} {v.model} {v.variant}: " + "; ".join(validate(v)))
            # De-duplicate ids (same variant listed twice on a page).
            seen: dict[str, dict] = {}
            for v in good:
                seen.setdefault(v.id, v.to_json())
            new = list(seen.values())
            old = old_by_brand.get(brand, [])
            # Placeholder CMS data (every variant of a model at one price) is never published:
            # keep last week's figures for that model and flag it for review instead.
            for model in set(placeholder_models(new)) | set(held):
                new = [v for v in new if v["model"] != model] + [v for v in old if v["model"] == model]
            flags.extend(f"{brand} {m}: {why}, so last week's prices were kept" for m, why in held.items())
            # Keep the previous updatedAt when nothing about a variant changed.
            old_by_id = {v["id"]: v for v in old}
            for nv in new:
                ov = old_by_id.get(nv["id"])
                if ov and {k: v for k, v in ov.items() if k != "updatedAt"} == {k: v for k, v in nv.items() if k != "updatedAt"}:
                    nv["updatedAt"] = ov.get("updatedAt", nv["updatedAt"])
                elif ov:
                    changes.append(f"{brand} {nv['model']} {nv['variant']}: RM{ov['price']:,.0f} → RM{nv['price']:,.0f}" if ov["price"] != nv["price"] else f"{brand} {nv['model']} {nv['variant']}: details updated")
                else:
                    changes.append(f"New: {brand} {nv['model']} {nv['variant']} RM{nv['price']:,.0f}")
            for ov in old:
                if ov["id"] not in {n["id"] for n in new}:
                    changes.append(f"Removed: {brand} {ov['model']} {ov['variant']}")
            flags.extend(review_flags(brand, old, new))
            flags.extend(f"{brand} {m}: all variants have the same price, so last week's prices were kept" for m in placeholder_models(list(seen.values())))
            merged.extend(new)
            print(f"{brand}: {len(new)} variants")
        except Exception as exc:  # keep last good data for this brand
            failures.append(f"{brand}: {exc}")
            traceback.print_exc()
            merged.extend(old_by_brand.get(brand, []))

    merged.sort(key=lambda v: (v["brand"], v["model"], v["price"]))
    changed = bool(changes) or len(merged) != len(previous.get("variants", []))
    version = previous.get("version", 0) + (1 if changed else 0)
    catalogue = {"version": version, "updatedAt": date.today().isoformat() if changed else previous.get("updatedAt", date.today().isoformat()), "variants": merged}
    cat_path.write_text(json.dumps(catalogue, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    version_path = out_dir / "version.json"
    versions = json.loads(version_path.read_text(encoding="utf-8")) if version_path.exists() else {}
    roadtax = json.loads((out_dir / "roadtax.json").read_text(encoding="utf-8")).get("version", 1) if (out_dir / "roadtax.json").exists() else 1
    versions.update({"catalogue": version, "roadtax": roadtax, "rates": versions.get("rates", 1)})
    version_path.write_text(json.dumps(versions, indent=1) + "\n", encoding="utf-8")

    report = ["# Weekly price update", "", f"{len(merged)} variants · catalogue version {version}", ""]
    if flags:
        report += ["## Needs your review", *[f"- {f}" for f in flags], ""]
    if failures:
        report += ["## Brands that failed (last good prices kept)", *[f"- {f}" for f in failures], ""]
    report += ["## Changes", *([f"- {c}" for c in changes] or ["- No price changes"]), ""]
    (out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))

    if failures:
        return 1
    if flags:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
