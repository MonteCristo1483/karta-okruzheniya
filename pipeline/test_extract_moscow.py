#!/usr/bin/env python3
"""Тесты выбора границы города в extract_moscow (без PBF и БД)."""

from __future__ import annotations

import unittest

import extract_moscow


class MatchesCityTest(unittest.TestCase):
    def test_moscow(self) -> None:
        tags = {
            "boundary": "administrative",
            "admin_level": "4",
            "name": "Москва",
            "ref": "RU-MOW",
        }
        self.assertTrue(extract_moscow.matches_city(tags))

    def test_name_ru_and_no_ref(self) -> None:
        tags = {"boundary": "administrative", "admin_level": "4", "name:ru": "Москва"}
        self.assertTrue(extract_moscow.matches_city(tags))

    def test_region_is_not_city(self) -> None:
        tags = {
            "boundary": "administrative",
            "admin_level": "4",
            "name": "Московская область",
            "ref": "RU-MOS",
        }
        self.assertFalse(extract_moscow.matches_city(tags))

    def test_other_admin_level(self) -> None:
        tags = {"boundary": "administrative", "admin_level": "8", "name": "Москва"}
        self.assertFalse(extract_moscow.matches_city(tags))

    def test_wrong_ref(self) -> None:
        tags = {
            "boundary": "administrative",
            "admin_level": "4",
            "name": "Москва",
            "ref": "RU-SPE",
        }
        self.assertFalse(extract_moscow.matches_city(tags))

    def test_not_boundary(self) -> None:
        tags = {"place": "city", "admin_level": "4", "name": "Москва"}
        self.assertFalse(extract_moscow.matches_city(tags))


if __name__ == "__main__":
    unittest.main()
