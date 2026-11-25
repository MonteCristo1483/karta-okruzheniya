#!/usr/bin/env python3
"""Тесты config/buildings_overrides.json и применения ручных исключений.

Формат проверяется без БД; is_home(tags, osm_id) обязан перекрывать теги
в обе стороны, а запись без reason — недопустима.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import buildings_filter

ROOT = Path(__file__).resolve().parent.parent
OVERRIDES = ROOT / "config" / "buildings_overrides.json"

OFFICE = {
    "building": "office",
    "addr:street": "Пресненская набережная",
    "addr:housenumber": "8 с1",
}
HOUSE = {
    "building": "house",
    "addr:street": "Тверская",
    "addr:housenumber": "1",
}


def entries() -> list[dict]:
    return json.loads(OVERRIDES.read_text(encoding="utf-8"))["overrides"]


class OverridesFileTest(unittest.TestCase):
    def test_version(self) -> None:
        payload = json.loads(OVERRIDES.read_text(encoding="utf-8"))
        self.assertEqual(payload.get("v"), 1)

    def test_entries_are_confirmed(self) -> None:
        items = entries()
        self.assertGreaterEqual(len(items), 1)
        ids = [int(item["osm_id"]) for item in items]
        self.assertEqual(len(ids), len(set(ids)), "osm_id не должны повторяться")
        for item in items:
            self.assertIsInstance(item.get("is_home"), bool, item)
            self.assertTrue(str(item.get("reason", "")).strip(), item)

    def test_source_mentioned(self) -> None:
        # Каждая запись подтверждена источником, а не догадкой.
        for item in entries():
            self.assertRegex(item["reason"], r"wikipedia|сайт|реестр", item)

    def test_start_list_is_moscow_city(self) -> None:
        names = " ".join(item["reason"] for item in entries()).lower()
        self.assertIn("neva towers", names)
        self.assertIn("город столиц", names)


class LoadOverridesTest(unittest.TestCase):
    def write(self, payload: dict) -> Path:
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        with handle as fh:
            json.dump(payload, fh, ensure_ascii=False)
        self.addCleanup(Path(handle.name).unlink)
        return Path(handle.name)

    def test_missing_file_is_empty(self) -> None:
        self.assertEqual(buildings_filter.load_overrides(Path("__no_such__.json")), {})

    def test_reason_required(self) -> None:
        path = self.write({"v": 1, "overrides": [{"osm_id": 1, "is_home": True, "reason": " "}]})
        with self.assertRaises(ValueError):
            buildings_filter.load_overrides(path)

    def test_boolean_required(self) -> None:
        path = self.write({"v": 1, "overrides": [{"osm_id": 1, "is_home": "yes", "reason": "сайт"}]})
        with self.assertRaises(ValueError):
            buildings_filter.load_overrides(path)

    def test_duplicate_ids_rejected(self) -> None:
        path = self.write({"v": 1, "overrides": [
            {"osm_id": 1, "is_home": True, "reason": "сайт"},
            {"osm_id": 1, "is_home": False, "reason": "сайт"},
        ]})
        with self.assertRaises(ValueError):
            buildings_filter.load_overrides(path)


class IsHomeWithOverridesTest(unittest.TestCase):
    def setUp(self) -> None:
        items = entries()
        self.raise_id = next(int(i["osm_id"]) for i in items if i["is_home"])
        self.false_id = next((int(i["osm_id"]) for i in items if not i["is_home"]), None)

    def test_override_true_beats_office_tag(self) -> None:
        self.assertFalse(buildings_filter.is_home(OFFICE))
        self.assertTrue(buildings_filter.is_home(OFFICE, self.raise_id))

    def test_override_true_beats_missing_address(self) -> None:
        self.assertFalse(buildings_filter.is_home({"building": "office"}))
        self.assertTrue(buildings_filter.is_home({"building": "office"}, self.raise_id))

    def test_override_false_beats_address(self) -> None:
        if self.false_id is None:
            self.skipTest("в стартовом списке нет записей is_home=false")
        self.assertTrue(buildings_filter.is_home(HOUSE))
        self.assertFalse(buildings_filter.is_home(HOUSE, self.false_id))

    def test_unknown_id_uses_tags(self) -> None:
        self.assertFalse(buildings_filter.is_home(OFFICE, 1234567890))
        self.assertTrue(buildings_filter.is_home(HOUSE, 1234567890))


class IsHomeWithoutAddressTest(unittest.TestCase):
    """HOMES-V2: жилой тип или addr:housenumber важнее адреса на полигоне."""

    def test_residential_type_without_address(self) -> None:
        for building in (
            "apartments",
            "residential",
            "house",
            "detached",
            "semidetached_house",
            "terrace",
            "dormitory",
        ):
            self.assertTrue(buildings_filter.is_home({"building": building}), building)

    def test_residential_type_with_place_instead_of_street(self) -> None:
        # Зеленоград и СНТ: addr:place вместо addr:street.
        self.assertTrue(
            buildings_filter.is_home(
                {"building": "house", "addr:place": "СНТ «Лесное»", "addr:housenumber": "5"}
            )
        )

    def test_housenumber_without_street(self) -> None:
        self.assertTrue(
            buildings_filter.is_home({"building": "yes", "addr:housenumber": "7 к1"})
        )

    def test_street_without_housenumber_is_not_a_home(self) -> None:
        self.assertFalse(
            buildings_filter.is_home(
                {"building": "yes", "addr:street": "Тверская улица"}
            )
        )

    def test_non_home_tag_beats_residential_type(self) -> None:
        self.assertFalse(
            buildings_filter.is_home(
                {
                    "building": "residential",
                    "amenity": "kindergarten",
                    "addr:housenumber": "1",
                }
            )
        )

    def test_non_home_building_beats_housenumber(self) -> None:
        self.assertFalse(
            buildings_filter.is_home(
                {"building": "garage", "addr:housenumber": "3"}
            )
        )
        self.assertFalse(
            buildings_filter.is_home(
                {
                    "building": "office",
                    "addr:street": "Пресненская набережная",
                    "addr:housenumber": "8 с1",
                }
            )
        )

    def test_empty_tags_are_not_home(self) -> None:
        self.assertFalse(buildings_filter.is_home(None))
        self.assertFalse(buildings_filter.is_home({}))


class ClassifyYearMatchFlagTest(unittest.TestCase):
    """Второй флаг разрешает подъём по паспорту ГИС ЖКХ только неявным."""

    def test_residential_can_be_raised(self) -> None:
        self.assertEqual(
            buildings_filter.classify({"building": "residential"}), (True, True)
        )

    def test_untagged_can_be_raised_by_passport(self) -> None:
        # Дом без адреса/типа может получить is_home из building_year_match.
        self.assertEqual(buildings_filter.classify({"building": "yes"}), (False, True))

    def test_explicit_non_home_is_blocked(self) -> None:
        self.assertEqual(buildings_filter.classify({"building": "garage"}), (False, False))
        self.assertEqual(
            buildings_filter.classify({"building": "yes", "shop": "mall"}),
            (False, False),
        )


if __name__ == "__main__":
    unittest.main()
