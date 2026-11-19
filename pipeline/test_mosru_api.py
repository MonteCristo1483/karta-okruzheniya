"""Тесты клиента data.mos.ru: без сети, через подменяемый fetch."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mosru_api import (  # noqa: E402
    MosRuError,
    column_names,
    dataset_count,
    dataset_info,
    datasets,
    features,
    load_api_key,
    require_key,
    rows,
)


class FakeFetch:
    def __init__(self, payload):
        self.payload = payload
        self.urls: list[str] = []

    def __call__(self, url: str):
        self.urls.append(url)
        return self.payload


class TestApiKey(unittest.TestCase):
    def test_key_from_environment(self):
        with mock.patch.dict(os.environ, {"MOSRU_API_KEY": "abc123"}, clear=False):
            self.assertEqual(load_api_key(), "abc123")

    def test_key_from_dotenv(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text('OTHER=1\nMOSRU_API_KEY="key-42"\n', encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(load_api_key(env), "key-42")

    def test_missing_key_is_explained(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertIsNone(load_api_key(Path(tmp) / ".env"))
                with self.assertRaises(MosRuError) as ctx:
                    require_key(None)
                self.assertIn("MOSRU_API_KEY", str(ctx.exception))


class TestRequests(unittest.TestCase):
    def test_datasets_url_contains_key_and_paging(self):
        fake = FakeFetch([{"Id": 1}])
        datasets(key="k", top=5, skip=10, fetch=fake)
        url = fake.urls[0]
        self.assertIn("/datasets?", url)
        self.assertIn("api_key=k", url)
        self.assertIn("%24top=5", url)
        self.assertIn("%24skip=10", url)

    def test_dataset_info_unwraps_list(self):
        fake = FakeFetch([{"Caption": "Входы метро"}])
        info = dataset_info(62329, key="k", fetch=fake)
        self.assertEqual(info["Caption"], "Входы метро")

    def test_dataset_count_parses_string(self):
        fake = FakeFetch("555")
        self.assertEqual(dataset_count(655, key="k", fetch=fake), 555)

    def test_rows_and_features_build_paths(self):
        rows_fake = FakeFetch([{"global_id": 1}])
        rows(62329, key="k", top=2, fetch=rows_fake)
        self.assertIn("/datasets/62329/rows", rows_fake.urls[0])

        features_fake = FakeFetch({"type": "FeatureCollection", "features": []})
        features(62329, key="k", bbox=(37.5, 55.7, 37.6, 55.8), fetch=features_fake)
        self.assertIn("/datasets/62329/features", features_fake.urls[0])
        self.assertIn("bbox=", features_fake.urls[0])

    def test_column_names_include_nested(self):
        info = {"Columns": [
            {"Name": "A", "SubColumns": [{"Name": "A1"}]},
            {"Name": "B"},
        ]}
        self.assertEqual(column_names(info), ["A", "A1", "B"])


if __name__ == "__main__":
    unittest.main()
