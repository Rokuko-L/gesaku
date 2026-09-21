"""Offline tests for closed-pass continuity helpers."""

import unittest

from core import continuity_text as ct


class TestProperNouns(unittest.TestCase):
    def test_extracts_title_case(self):
        names = ct.proper_nouns("Danny met Mateo at Blackthorn Academy.")
        self.assertIn("Danny", names)
        self.assertIn("Mateo", names)
        self.assertTrue(any("Blackthorn" in n for n in names))

    def test_skips_the(self):
        names = ct.proper_nouns("The boy left.")
        self.assertNotIn("The", names)


class TestUnknownEntities(unittest.TestCase):
    def test_flags_unknown(self):
        hits = ct.unknown_entities("Alice walked with Bob.", "Danny Mateo Choir")
        claims = " ".join(h["claim"] for h in hits)
        self.assertIn("Alice", claims)
        self.assertIn("Bob", claims)
        self.assertEqual(hits[0]["trust"], "low")

    def test_known_not_flagged(self):
        hits = ct.unknown_entities("Danny walked.", "Danny Mateo")
        self.assertEqual(hits, [])


class TestSealLeaks(unittest.TestCase):
    def test_leak_before_reveal_multiword(self):
        leaks = ct.seal_leaks(
            "The Resonance Bleed spread.",
            ["resonance bleed"],
            5,
            2,
            sealed_fact_phrases=["Resonance Bleed is irreversible"],
        )
        self.assertGreaterEqual(len(leaks), 1)
        self.assertEqual(leaks[0]["trust"], "high")
        self.assertTrue(leaks[0].get("author_only"))

    def test_always_on_frames_not_high_trust(self):
        leaks = ct.seal_leaks(
            "She was actually fine. The real problem waited. A puppet hung.",
            ["actually", "the real", "puppet"],
            None,
            2,
            sealed_fact_phrases=[],
        )
        self.assertEqual(leaks, [])

    def test_no_sealed_facts_no_leaks(self):
        leaks = ct.seal_leaks("Anything.", [], None, 1, sealed_fact_phrases=[])
        self.assertEqual(leaks, [])

    def test_no_leak_after_reveal(self):
        leaks = ct.seal_leaks(
            "The Resonance Bleed spread.",
            ["resonance bleed"],
            5,
            5,
            sealed_fact_phrases=["Resonance Bleed is irreversible"],
        )
        self.assertEqual(leaks, [])

    def test_make_finding_trust_map(self):
        f = ct.make_finding(kind="unknown_entity", claim="x")
        self.assertEqual(f["trust"], "low")
        self.assertEqual(f["severity"], "warn")
        f2 = ct.make_finding(kind="seal_leak", claim="y", severity="structural")
        self.assertEqual(f2["severity"], "structural")
        self.assertEqual(f2["trust"], "high")
        self.assertTrue(f2["author_only"])


class TestOverlapMetric(unittest.TestCase):
    def test_overlap_on_structured_query_tokens(self):
        findings_a = [{
            "id": "A1",
            "claim": "Mateo laugh delayed",
            "chapters": [3],
            "evidence": ["Mateo"],
        }]
        trace = [
            {"tool": "search_prior_chapters", "input": {"query": "Mateo laugh"}, "ok": True},
            {"tool": "search_canon", "input": {"query": "unrelated"}, "ok": True},
        ]
        score = ct.overlap_a_tool_targets(findings_a, trace)
        self.assertGreater(score, 0.0)
        self.assertLess(score, 1.0)

    def test_chapter_id_not_substring(self):
        findings_a = [{"id": "A1", "claim": "x", "chapters": [3], "evidence": []}]
        trace = [
            {"tool": "read_chapter", "input": {"chapter": 13}, "ok": True},
            {"tool": "read_chapter", "input": {"chapter": 30}, "ok": True},
        ]
        self.assertEqual(ct.overlap_a_tool_targets(findings_a, trace), 0.0)

    def test_claim_word_chapter_not_schema_key_hit(self):
        findings_a = [{
            "id": "A1",
            "claim": "Chapter uses sealed term Resonance",
            "chapters": [],
            "evidence": ["Resonance"],
        }]
        trace = [
            {"tool": "read_chapter", "input": {"chapter": 2}, "ok": True},
            {"tool": "read_outline_chapter", "input": {"chapter": 2}, "ok": True},
        ]
        # "chapter" is a stopword/schema key — must not mark every read as overlap
        self.assertEqual(ct.overlap_a_tool_targets(findings_a, trace), 0.0)

    def test_zero_overlap_on_novel_targets(self):
        findings_a = [{
            "id": "A1",
            "claim": "Zebra fountain sealed",
            "chapters": [9],
            "evidence": ["Zebra fountain"],
        }]
        trace = [
            {"tool": "read_chapter", "input": {"chapter": 2}, "ok": True},
            {"tool": "search_world", "input": {"query": "Blackthorn"}, "ok": True},
        ]
        self.assertEqual(ct.overlap_a_tool_targets(findings_a, trace), 0.0)

    def test_empty_trace(self):
        self.assertEqual(ct.overlap_a_tool_targets([{"claim": "x"}], []), 0.0)

    def test_overlap_on_empty_a(self):
        self.assertEqual(ct.overlap_a_tool_targets([], [{"tool": "t", "input": {"q": "x"}}]), 0.0)


class TestClosedPassOffline(unittest.TestCase):
    def test_run_closed_pass_on_fixture_project(self):
        """Smoke: closed pass runs on an existing project if present."""
        from core import paths
        from pipeline import continuity_closed
        import os
        root = paths.get_root_dir()
        sample = root / "projects" / "smoke_4ch"
        if not (sample / "state.json").exists() and not (sample / "characters.md").exists():
            self.skipTest("no smoke_4ch project")
        orig = paths._project_name
        try:
            paths.set_project_name("smoke_4ch")
            # chapter may or may not exist — result structure still required
            result = continuity_closed.run_closed_pass(1)
            self.assertIn("findings", result)
            self.assertIn("trust_note", result)
            self.assertIn("structural", result["trust_note"])
            for f in result["findings"]:
                self.assertIn("trust", f)
                self.assertIn("severity", f)
        finally:
            paths._project_name = orig
            if orig:
                paths.set_project_name(orig)


if __name__ == "__main__":
    unittest.main()
