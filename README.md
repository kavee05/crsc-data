# CRSC data site

Public JSON the CRSC app reads, plus the robot that keeps car prices up to date.

| File | What it is | Updated by |
|---|---|---|
| `catalogue.json` | Car variants with official on-the-road prices (Peninsular, without insurance) | Weekly robot |
| `version.json` | Version numbers the app checks before downloading | Weekly robot |
| `roadtax.json` | JPJ road tax tables (cc and EV kW) | You, when JPJ changes rates (bump `version`) |
| `rates.json` | Typical hire-purchase rate presets | You, when bank rates move |
| `privacy-policy.html` | Privacy policy linked from the app and Play Store | You |

## One-time setup

1. Create a **public** GitHub repository named `crsc-data` and push the contents of this folder to its `main` branch.
2. Repository **Settings → Pages**: Source "Deploy from a branch", branch `main`, folder `/ (root)`. The site will be at `https://<your-username>.github.io/crsc-data/`.
3. Repository **Settings → Actions → General → Workflow permissions**: choose "Read and write permissions" and tick "Allow GitHub Actions to create and approve pull requests".
4. In the app project's `gradle.properties`, set `crsc.dataBaseUrl=https://<your-username>.github.io/crsc-data/` and rebuild.
5. Replace `CONTACT_EMAIL` in `privacy-policy.html` with the email you want public.

## How the weekly update works

Every Monday 09:00 (Malaysia time) the **Update car prices** workflow:

1. Runs the parser tests against saved copies of the brand pages.
2. Reads each brand's official site (politely, one request every 1.5 seconds).
3. Compares with last week's `catalogue.json`:
   - **Normal changes** are committed and published automatically.
   - **Unusual changes** open a pull request titled "Car prices need your review": a price moving over 30%, over 20% of a brand's variants disappearing, every variant of a model showing the same price, or a brand's price data disagreeing with the "starting price" on its own page. Read the description and press **Merge** if it looks right.
   - **A brand whose website changed** keeps last week's prices, and the workflow fails so GitHub emails you. Ask for that brand's parser to be updated.

You can also run it any time from **Actions → Update car prices → Run workflow**.

## Brands covered

| Brand | Source | Notes |
|---|---|---|
| Perodua | Model pages' price & specification tables | All models |
| Proton | proton.com's public content API, cross-checked against each model page's starting price | Models are held back when the two disagree |
| Toyota | toyota.com.my price list page (Peninsular, Individual Private), cross-checked against each model page's own grade prices and "From" price; specs from each grade's specification page | Held back when the two disagree or a price is only "Estimated" (pre-launch). Commercial-only models (panel van) are skipped |
| Honda | honda.com.my pricing pages' JSON ("Retail Price Without Insurance", Peninsular), cross-checked against the table rendered on the page and the "From RM" in the site menu; specs from Honda's spec JSON | Spec fields that contradict the variant name are dropped (Honda's spec data is sometimes attached to the wrong variant) |
| Nissan | Each model's specifications page ("PRICE PENINSULAR", on-the-road with SST, without insurance), cross-checked against each variant's "Starting price" there and on the model page | Held back when a price is "Estimated" or a page shows two prices for one variant. Spec values are only kept when unambiguous |
| Chery | Peninsular "Customer Price List" PDFs linked from each model page (Private/Individual, on-the-road without insurance), cross-checked by the list's own sum: net selling price + fees = on-the-road price | Pages show no prices, so there is no page starting price to compare. No engine cc is published, so cc is empty. Held back when the sum doesn't add up or a figure can't be read |
| Mazda | Not scraped | Model pages show "Price From RM…" with no stated basis (on-the-road? insurance? region?); the official West Malaysia price list is an encrypted, image-only PDF |
| BYD | Not scraped | byd.simemotors.my shows only a model-level "Starting from RM…" with no stated basis; per-variant Peninsular prices are only in image-only PDFs |

All brand sites' robots.txt allow these pages. Their terms reserve their text and images, so only factual prices and specs are taken (no images or copy) for the newer brands.

To add a brand, create `scraper/crsc_scraper/brands/<brand>.py` with a `scrape()` that returns `(variants, held)`, save a copy of one page in `scraper/tests/fixtures/`, add a test, and register it in `BRANDS` in `run.py`.

## Running locally

```bash
cd scraper
python -m unittest discover -s tests -t .
python -m crsc_scraper.run --out ..
```
