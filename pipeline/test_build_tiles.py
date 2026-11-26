#!/usr/bin/env python3
"""Compact v2 breakdown written into buildings.pmtiles (Tiles-B2).

Schema: {"v":2,"i":7.8,"f":[transport, industry, access, green, home,
density, wind],"fl":["h:nc"]}. `fl` is omitted when there are no flags.
Legacy base/bonuses/penalties must never reach the tiles.
"""

from __future__ import annotations

import json
import unittest

import build_tiles


def v2_block(values: dict[str, float] | None = None,
             flags: list[str] | None = None,
             index: float = 7.84) -> dict:
    values = values or {}
    factors = {
        name: {"value": values.get(name, 0.1234)}
        for name in build_tiles.FACTOR_ORDER
    }
    return {
        "v2": {"index": index, "factors": factors, "flags": flags or []},
        "base": 5.0,
        "index": index,
        "bonuses": {"parks": {"bonus": 1.0}},
        "penalties": {"road": {"penalty": -0.2}},
        "version": 2,
    }


class CompactBreakdownTest(unittest.TestCase):
    def test_factor_order_is_fixed(self) -> None:
        self.assertEqual(
            build_tiles.FACTOR_ORDER,
            ("transport", "industry", "access", "green", "home", "density", "wind"),
        )

    def test_values_follow_factor_order_and_round_to_two(self) -> None:
        values = {
            "transport": 0.111,
            "industry": 0.222,
            "access": 0.333,
            "green": 0.444,
            "home": 0.555,
            "density": 0.666,
            "wind": 0.777,
        }
        compact = build_tiles.compact_breakdown(v2_block(values))
        self.assertIsNotNone(compact)
        assert compact is not None
        self.assertEqual(compact["v"], 2)
        self.assertEqual(compact["f"], [0.11, 0.22, 0.33, 0.44, 0.56, 0.67, 0.78])
        self.assertNotIn("fl", compact)

    def test_index_has_one_decimal(self) -> None:
        compact = build_tiles.compact_breakdown(v2_block(index=7.84))
        assert compact is not None
        self.assertEqual(compact["i"], 7.8)

    def test_legacy_buckets_are_dropped(self) -> None:
        compact = build_tiles.compact_breakdown(v2_block())
        assert compact is not None
        for legacy in ("base", "bonuses", "penalties", "version", "index"):
            self.assertNotIn(legacy, compact)

    def test_flags_become_short_codes(self) -> None:
        compact = build_tiles.compact_breakdown(v2_block(flags=[
            "home:not_classified",
            "industry:unknown_status",
            "access:metro:not_classified",
        ]))
        assert compact is not None
        self.assertEqual(compact["fl"], ["h:nc", "i:us", "a:m:nc"])

    def test_empty_flags_are_omitted(self) -> None:
        compact = build_tiles.compact_breakdown(v2_block(flags=[]))
        assert compact is not None
        self.assertNotIn("fl", compact)
        self.assertNotIn('"fl"', json.dumps(compact, separators=(",", ":")))

    def test_breakdown_without_v2_is_not_written(self) -> None:
        legacy_only = {
            "base": 5.0,
            "index": 7.8,
            "bonuses": {"parks": {"bonus": 1.0}},
            "penalties": {"road": {"penalty": -0.2}},
        }
        self.assertIsNone(build_tiles.compact_breakdown(legacy_only))
        self.assertIsNone(build_tiles.compact_breakdown(None))

    def test_breakdown_lod_starts_at_z14(self) -> None:
        self.assertEqual(build_tiles.BREAKDOWN_MIN_ZOOM, 14)
        self.assertIn('"$zoom",14', build_tiles.TILE_FILTER)
        self.assertNotIn('"$zoom",13]]', build_tiles.TILE_FILTER)


if __name__ == "__main__":
    unittest.main()
