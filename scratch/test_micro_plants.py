"""Offline tests for micro-plant store, clustering, and extract soft-fail.

Run: uv run python -m unittest scratch.test_micro_plants
"""

import json
import tempfile
import unittest
from pathlib import Path

from core.micro_plants import (
    add_plants,
    content_tokens,
    drop_source_chapter,
    expire_stale,
    jaccard,
    load_callbacks,
    mark_harvested,
    match_plant_harvest_threads,
    open_items,
    save_callbacks,
    soft_inject_block,
)
from core.validation import MicroPlantExtract, OutputValidationError, parse_validated


class TokenMatchTest(unittest.TestCase):
    def test_content_tokens_drop_stopwords(self):
        toks = content_tokens("The cold spot in Lily's chest")
        self.assertIn("cold", toks)
        self.assertIn("lily", toks)
        self.assertNotIn("the", toks)

    def test_jaccard_overlap(self):
        a = content_tokens("the silver hairbrush on the vanity")
        b = content_tokens("she still carried the silver hairbrush")
        self.assertGreater(jaccard(a, b), 0.3)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.path = Path(self._td.name) / "open_callbacks.json"

    def tearDown(self):
        self._td.cleanup()

    def test_load_missing_is_empty(self):
        data = load_callbacks(self.path)
        self.assertEqual(data["callbacks"], [])

    def test_add_dedupes_and_caps(self):
        data = {"callbacks": []}
        data = add_plants(data, 2, [{"text": "the silver hairbrush on the vanity", "kind": "object"}])
        data = add_plants(data, 3, [{"text": "silver hairbrush on vanity", "kind": "object"}])
        self.assertEqual(len(open_items(data)), 1)
        data = add_plants(
            data, 4,
            [{"text": f"unique prop number {i}", "kind": "object"} for i in range(5)],
            max_new=5,
        )
        self.assertLessEqual(len(open_items(data)), 8)

    def test_harvest_and_expire(self):
        data = {"callbacks": []}
        data = add_plants(data, 1, [{"text": "a locked diary under the floorboard", "kind": "object"}])
        cid = data["callbacks"][0]["id"]
        data = mark_harvested(data, 5, [cid])
        self.assertEqual(data["callbacks"][0]["status"], "harvested")
        self.assertEqual(data["callbacks"][0]["harvested_chapter"], 5)

        data = add_plants(data, 1, [{"text": "a cracked teacup used as a key dish", "kind": "object"}])
        open_id = [c for c in data["callbacks"] if c["status"] == "open"][0]["id"]
        data = expire_stale(data, 20, window=12)
        status = next(c["status"] for c in data["callbacks"] if c["id"] == open_id)
        self.assertEqual(status, "expired")

    def test_drop_source_chapter(self):
        data = {"callbacks": []}
        data = add_plants(data, 3, [{"text": "a bone flute left on the windowsill", "kind": "object"}])
        data = drop_source_chapter(data, 3)
        self.assertEqual(data["callbacks"], [])

    def test_soft_inject_block(self):
        data = {"callbacks": []}
        data = add_plants(data, 2, [{"text": "the silver hairbrush", "kind": "object"}])
        block = soft_inject_block(data, 8)
        self.assertIn("hairbrush", block)
        self.assertIn("prefer nothing over a forced", block.lower())
        self.assertEqual(soft_inject_block(data, 2), "")
        self.assertEqual(soft_inject_block({"callbacks": []}, 8), "")

    def test_atomic_roundtrip(self):
        data = {"callbacks": []}
        data = add_plants(data, 1, [{"text": "a sealed letter with a broken crest", "kind": "object"}])
        save_callbacks(data, self.path)
        loaded = load_callbacks(self.path)
        self.assertEqual(len(loaded["callbacks"]), 1)
        self.assertEqual(loaded["callbacks"][0]["source_chapter"], 1)


class ClusterTest(unittest.TestCase):
    def test_near_duplicates_collapse(self):
        plants = [
            {"text": "The cold spot in Lily's chest from the Word of Unmaking", "chapter": 22},
            {"text": "The cold spot in Lily's chest is emphasized", "chapter": 23},
            {"text": "The cold spot throbbing as an indicator", "chapter": 25},
        ]
        harvests = [
            {"text": "The cold spot in Lily's chest remnant of her infernal past", "chapter": 27},
        ]
        threads = match_plant_harvest_threads(plants, harvests)
        self.assertLess(len(threads), len(plants) + len(harvests))
        paid = [t for t in threads if t["status"] == "paid off"]
        self.assertTrue(paid)

    def test_reworded_harvest_links_by_overlap(self):
        plants = [{
            "text": "The cold spot in Lily's chest from using the Word, indicating long-term cost",
            "chapter": 22,
        }]
        harvests = [{
            "text": "The cold spot in Lily's chest, a remnant of her infernal pact, is reiterated",
            "chapter": 27,
        }]
        threads = match_plant_harvest_threads(plants, harvests)
        self.assertEqual(len(threads), 1)
        self.assertEqual(threads[0]["status"], "paid off")
        self.assertEqual(threads[0]["planted"], 22)
        self.assertEqual(threads[0]["harvest"], 27)

    def test_unrelated_remain_open(self):
        plants = [{"text": "Zephyr's investigation ledger of missing jewels", "chapter": 10}]
        harvests = [{"text": "Leo confesses his love at the tournament", "chapter": 39}]
        threads = match_plant_harvest_threads(plants, harvests)
        self.assertEqual(len(threads), 2)
        statuses = sorted(t["status"] for t in threads)
        # plant-only stays open; harvest-only is a recalled payoff (no plant recorded)
        self.assertEqual(statuses, ["open", "recalled"])
        open_t = next(t for t in threads if t["status"] == "open")
        self.assertEqual(open_t["planted"], 10)
        self.assertIsNone(open_t["harvest"])

    def test_plant_after_harvest_does_not_link(self):
        plants = [{"text": "the silver hairbrush", "chapter": 20}]
        harvests = [{"text": "the silver hairbrush", "chapter": 5}]
        threads = match_plant_harvest_threads(plants, harvests)
        self.assertEqual(len(threads), 2)
        statuses = sorted(t["status"] for t in threads)
        self.assertEqual(statuses, ["open", "recalled"])


class ValidationTest(unittest.TestCase):
    def test_parse_extract(self):
        raw = json.dumps({
            "new_plants": [{"text": "a cracked teacup", "kind": "object"}],
            "harvested_ids": ["abc123"],
        })
        parsed = parse_validated(MicroPlantExtract, raw, context="test")
        self.assertEqual(parsed.new_plants[0].text, "a cracked teacup")
        self.assertEqual(parsed.harvested_ids, ["abc123"])

    def test_empty_plants_ok(self):
        parsed = parse_validated(MicroPlantExtract, '{"new_plants": [], "harvested_ids": []}')
        self.assertEqual(parsed.new_plants, [])

    def test_invalid_raises(self):
        with self.assertRaises(OutputValidationError):
            parse_validated(MicroPlantExtract, '{"new_plants": "nope"}')


class ExtractSoftFailTest(unittest.TestCase):
    def test_script_imports_without_llm(self):
        import importlib
        mod = importlib.import_module("pipeline.extract_micro_plants")
        self.assertTrue(hasattr(mod, "extract_for_chapter"))
        self.assertTrue(hasattr(mod, "main"))


if __name__ == "__main__":
    unittest.main()
