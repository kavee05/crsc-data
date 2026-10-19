"""Parser tests against saved copies of the brand pages (no network)."""

import json
import unittest
from pathlib import Path

from crsc_scraper import pdf
from crsc_scraper.brands import chery, honda, nissan, perodua, proton, toyota
from crsc_scraper.run import placeholder_models, review_flags

FIXTURES = Path(__file__).parent / "fixtures"


class PeroduaTest(unittest.TestCase):
    def test_myvi_variants(self):
        page = (FIXTURES / "perodua_myvi.html").read_text(encoding="utf-8")
        variants = perodua.parse_model_page(page, "https://www.perodua.com.my/our-models/hatchback/myvi")
        by_name = {v.variant: v for v in variants}
        self.assertEqual(len(variants), 5)
        av = next(v for n, v in by_name.items() if "AV" in n)
        self.assertEqual(av.price, 59900.0)
        self.assertEqual(av.cc, 1496)
        self.assertEqual(av.body, "Hatchback")
        self.assertEqual(av.model, "Myvi")
        g = next(v for n, v in by_name.items() if n.startswith("1.3L G") and "PSDA" not in n)
        self.assertEqual(g.price, 48500.0)
        self.assertEqual(g.cc, 1329)


class ProtonTest(unittest.TestCase):
    def test_saga_variants(self):
        items = json.loads((FIXTURES / "proton_saga_descendants.json").read_text(encoding="utf-8"))["items"]
        model = {"name": "ALL-NEW SAGA", "route": {"path": "/global-content/vehicles/all-new-saga/"}}
        variants = proton.parse_model(model, items)
        self.assertEqual(len(variants), 3)  # service-only variants are skipped
        self.assertTrue(all(v.model == "Saga" and v.body == "Sedan" for v in variants))
        self.assertTrue(all(v.cc == 1499 for v in variants))
        # The CMS currently gives every Saga variant the same price; it must be held back.
        self.assertEqual(placeholder_models([v.to_json() for v in variants]), ["Saga"])

    def test_name_tidy(self):
        self.assertEqual(proton._tidy("1.5TD PRIME X"), "1.5TD Prime X")
        self.assertEqual(proton._tidy("1.5 TGDi BSG"), "1.5 TGDI BSG")


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class ToyotaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.grades = toyota.parse_price_list(fixture("toyota_price_list.html"))

    def test_price_list(self):
        vios_e = self.grades["new-vios-1-5e-at"]
        self.assertEqual(vios_e["price"], 90600.0)
        self.assertEqual(vios_e["body"], "Sedan")
        self.assertEqual(vios_e["fuel"], "Petrol")
        self.assertEqual(self.grades["vios-hybrid-electric"]["fuel"], "Hybrid")
        # The panel van has only company prices, so it is left out.
        self.assertNotIn("hiace-panel-van-3-0-turbo-diesel", self.grades)

    def test_vios_cross_checks_and_specs(self):
        model, start, page_prices = toyota.parse_model_page(fixture("toyota_vios.html"))
        self.assertEqual((model, start), ("Vios", 90600.0))
        specs = {"new-vios-1-5g-at": toyota.parse_specs(fixture("toyota_vios_1_5g_specs.html"))}
        variants, why = toyota.build_variants(model, start, page_prices, self.grades, specs, "u")
        self.assertIsNone(why)
        self.assertEqual(len(variants), 4)
        g = next(v for v in variants if v.variant == "1.5G AT")
        self.assertEqual((g.price, g.cc, g.transmission), (96600.0, 1496, "CVT"))

    def test_mismatched_starting_price_is_held(self):
        model, start, page_prices = toyota.parse_model_page(fixture("toyota_camry.html"))
        variants, why = toyota.build_variants(model, start, page_prices, self.grades, {}, "u")
        self.assertEqual(variants, [])
        self.assertIn("RM248,800", why)

    def test_price_mismatch_is_held(self):
        model, start, page_prices = toyota.parse_model_page(fixture("toyota_vios.html"))
        grades = {k: dict(v) for k, v in self.grades.items()}
        grades["new-vios-1-5g-at"]["price"] = 99999.0
        variants, why = toyota.build_variants(model, start, page_prices, grades, {}, "u")
        self.assertEqual(variants, [])
        self.assertIn("1.5G", why)

    def test_estimated_prices_are_held(self):
        model, start, page_prices = toyota.parse_model_page(fixture("toyota_yaris_cross.html"))
        variants, why = toyota.build_variants(model, start, page_prices, self.grades, {}, "u")
        self.assertEqual(variants, [])
        self.assertIn("estimated", why)

    def test_ev_specs(self):
        specs = toyota.parse_specs(fixture("toyota_bz4x_specs.html"))
        self.assertEqual((specs["kw"], specs["seats"], specs["cc"]), (167.0, 5, None))

    def test_model_name(self):
        self.assertEqual(toyota.model_name("EXPLORE YOUR ALL NEW bZ4X"), "bZ4X")
        self.assertEqual(toyota.model_name("INNOVA ZENIX HYBRID ELECTRIC"), "Innova Zenix")
        self.assertEqual(toyota.model_name("ALL-NEW VIOS HEV GR SPORT"), "Vios")


class HondaTest(unittest.TestCase):
    """honda_city_pricing_api.json / honda_fullspec_api.json: real API responses keyed by variant id."""

    def setUp(self):
        self.page = fixture("honda_city_pricing.html")
        self.api = {int(k): honda.parse_price(v) for k, v in json.loads(fixture("honda_city_pricing_api.json")).items()}
        self.specs = {int(k): honda.parse_specs(v) for k, v in json.loads(fixture("honda_fullspec_api.json")).items()}

    def test_menu(self):
        menu = {slug: (name, price) for slug, name, price in honda.parse_menu(self.page)}
        self.assertEqual(menu["city"], ("City", 89900.0))
        self.assertEqual(menu["en1"][0], "e:N1")

    def test_city_variants(self):
        names, page_prices = honda.parse_pricing_page(self.page)
        self.assertEqual(names[41], "City 1.5L e:HEV RS")
        self.assertEqual(page_prices[54], 99900.0)
        variants, why = honda.build_model("city", "City", 89900.0, names, page_prices, self.api, {41: self.specs[41]})
        self.assertIsNone(why)
        hev = next(v for v in variants if v.variant == "1.5L e:HEV RS")
        self.assertEqual((hev.price, hev.cc, hev.fuel, hev.transmission, hev.body), (111900.0, 1498, "Hybrid", "e-CVT", "Sedan"))
        self.assertEqual(len(variants), 4)

    def test_api_disagreeing_with_page_is_held(self):
        names, page_prices = honda.parse_pricing_page(self.page)
        api = {**self.api, 3: 79900.0}
        variants, why = honda.build_model("city", "City", 79900.0, names, page_prices, api, {})
        self.assertEqual(variants, [])
        self.assertIn("City 1.5L E", why)

    def test_menu_price_mismatch_is_held(self):
        names, page_prices = honda.parse_pricing_page(self.page)
        variants, why = honda.build_model("city", "City", 88000.0, names, page_prices, self.api, {})
        self.assertEqual(variants, [])
        self.assertIn("RM88,000", why)

    def test_misattached_spec_is_dropped(self):
        # Variant 61 is the petrol City Hatchback RS, but Honda's spec data for it is the e:HEV's.
        spec = honda.consistent_spec("City Hatchback 1.5L RS", self.specs[61])
        self.assertEqual((spec["fuel"], spec["transmission"], spec["cc"]), ("Petrol", None, 1498))


class NissanTest(unittest.TestCase):
    def test_almera(self):
        spec, problems = nissan.parse_spec_page(fixture("nissan_almera_specifications.html"))
        self.assertEqual(problems, [])
        cards = nissan.cards(fixture("nissan_almera.html"))
        self.assertEqual(cards["1.0L Turbo VL"], 83888.0)
        variants, why = nissan.build_model("almera", "Almera", spec, problems, cards, "u")
        self.assertIsNone(why)
        vlt = next(v for v in variants if v.variant == "1.0L Turbo VLT")
        self.assertEqual((vlt.price, vlt.cc, vlt.transmission, vlt.body), (95888.0, 999, "CVT", "Sedan"))
        self.assertEqual(len(variants), 3)

    def test_model_page_mismatch_is_held(self):
        spec, problems = nissan.parse_spec_page(fixture("nissan_almera_specifications.html"))
        cards = dict(nissan.cards(fixture("nissan_almera.html")), **{"1.0L Turbo VL": 81888.0})
        variants, why = nissan.build_model("almera", "Almera", spec, problems, cards, "u")
        self.assertEqual(variants, [])
        self.assertIn("RM81,888", why)

    def test_estimated_prices_are_held(self):
        spec, problems = nissan.parse_spec_page(fixture("nissan_kicks_specifications.html"))
        self.assertTrue(problems and all("estimated" in p for p in problems))
        self.assertEqual(nissan.build_model("kicks", "Kicks", spec, problems, {}, "u")[0], [])

    def test_conflicting_prices_are_held(self):
        # The NV200 page shows the Semi-Panel Van at both RM88,888 and RM91,888.
        spec, problems = nissan.parse_spec_page(fixture("nissan_nv200_specifications.html"))
        self.assertTrue(any("Semi-Panel Van" in p for p in problems))
        self.assertNotIn("Panel Van 1.6L", [p.split(" is shown")[0] for p in problems])


class CheryTest(unittest.TestCase):
    def setUp(self):
        self.pieces = pdf.pieces((FIXTURES / "chery_tiggo_7_pro_pm_pricelist.pdf").read_bytes())

    def test_tiggo_7_pro_price_list(self):
        model, variants, problems = chery.parse_pieces(self.pieces)
        self.assertEqual(problems, [])
        self.assertEqual(model, "TIGGO 7 PRO")
        self.assertEqual(variants, [{"variant": "1.6 TGDI", "price": 123800.0}])
        out, why = chery.build_model(chery.model_name(model), variants, problems, "u", "SUV")
        self.assertIsNone(why)
        self.assertEqual((out[0].model, out[0].fuel, out[0].body), ("Tiggo 7 Pro", "Petrol", "SUV"))

    def test_figures_that_do_not_add_up_are_held(self):
        tampered = [pdf.Piece(p.y, p.x, "124,800.00") if p.text == "123,800.00" else p for p in self.pieces]
        _, variants, problems = chery.parse_pieces(tampered)
        self.assertTrue(problems and "does not add up" in problems[0])
        self.assertEqual(chery.build_model("Tiggo 7 Pro", variants, problems, "u")[0], [])

    def test_model_name(self):
        self.assertEqual(chery.model_name("CHERY O5"), "O5")
        self.assertEqual(chery.model_name("TIGGO 7 PHEV CSH"), "Tiggo 7 PHEV")


class ReviewFlagsTest(unittest.TestCase):
    def test_big_price_jump_is_flagged(self):
        old = [{"id": "a", "model": "X", "variant": "1", "price": 50000.0}]
        new = [{"id": "a", "model": "X", "variant": "1", "price": 90000.0}]
        self.assertTrue(review_flags("B", old, new))

    def test_small_change_is_not_flagged(self):
        old = [{"id": "a", "model": "X", "variant": "1", "price": 50000.0}]
        new = [{"id": "a", "model": "X", "variant": "1", "price": 51000.0}]
        self.assertFalse(review_flags("B", old, new))


if __name__ == "__main__":
    unittest.main()
