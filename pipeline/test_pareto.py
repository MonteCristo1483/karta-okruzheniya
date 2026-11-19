"""Тесты ранговой статистики Pareto-проверки (без базы и сети)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_pareto import pearson, ranks, spearman  # noqa: E402


class TestRanks(unittest.TestCase):
    def test_ranks_with_ties(self):
        self.assertEqual(ranks([10, 20, 20, 30]), [1.0, 2.5, 2.5, 4.0])

    def test_single_value(self):
        self.assertEqual(ranks([5]), [1.0])


class TestSpearman(unittest.TestCase):
    def test_perfect_positive(self):
        self.assertAlmostEqual(spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)

    def test_perfect_negative(self):
        self.assertAlmostEqual(spearman([1, 2, 3, 4], [40, 30, 20, 10]), -1.0)

    def test_constant_is_zero(self):
        self.assertEqual(spearman([1, 2, 3], [5, 5, 5]), 0.0)

    def test_pearson_matches_manual(self):
        self.assertAlmostEqual(pearson([1, 2, 3], [2, 4, 6]), 1.0)


if __name__ == "__main__":
    unittest.main()
