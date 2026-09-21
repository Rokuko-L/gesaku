"""Offline tests for open-pass continuity (MockLLM tool loop)."""

import json
import unittest

from core import continuity_text as ct
from core.mock_llm import MockLLM


class TestContinuityToolsExecutor(unittest.TestCase):
    def test_unknown_tool_error(self):
        from pipeline.continuity_open import ContinuityTools
        tools = ContinuityTools(2)
        out = tools("nope", {})
        self.assertIn("ERROR", out)

    def test_path_escape_denied(self):
        from pipeline.continuity_open import ContinuityTools, _safe_project_path
        from core import paths
        with self.assertRaises(ValueError):
            _safe_project_path(paths.get_root_dir().parent / "secrets.txt")
        # Sibling-prefix escape must fail (…/foo vs …/foo2)
        proj = paths.get_project_dir().resolve()
        sibling = proj.parent / (proj.name + "2" / "x.md") if False else proj.parent / (proj.name + "2")
        with self.assertRaises(ValueError):
            _safe_project_path(sibling / "secrets.md")


class TestOpenPassMocked(unittest.TestCase):
    def test_open_pass_with_mock_tools(self):
        from core import paths
        from pipeline import continuity_open
        sample = paths.get_root_dir() / "projects" / "smoke_4ch"
        if not (sample / "characters.md").exists():
            self.skipTest("no smoke_4ch project")

        verdict = {
            "findings": [{
                "kind": "continuity",
                "claim": "Mock novel finding",
                "chapters": [1],
                "evidence": ["e"],
                "severity": "warn",
                "author_only": True,
            }],
            "leads_exhausted": True,
            "notes": "ok",
        }
        script = [
            {
                "text": "",
                "tool_calls": [
                    {"id": "1", "name": "search_canon", "input": {"query": "Choir"}},
                ],
            },
            {"text": json.dumps(verdict), "tool_calls": None},
        ]
        mock = MockLLM()
        orig = paths._project_name
        try:
            paths.set_project_name("smoke_4ch")
            with mock.install_tools(script):
                result = continuity_open.run_open_pass(1, budget=5)
            self.assertEqual(result["agent_stop"], "leads_exhausted")
            self.assertGreaterEqual(result["tool_calls_used"], 1)
            self.assertIn("overlap_a_tool_targets", result)
            self.assertTrue(result["trace"])
            self.assertEqual(result["budget"], 5)
            self.assertTrue(result["findings_b"])
            self.assertTrue(result["findings_b"][0]["author_only"])
        finally:
            paths._project_name = orig

    def test_budget_stop_recorded(self):
        from core import paths
        from pipeline import continuity_open
        sample = paths.get_root_dir() / "projects" / "smoke_4ch"
        if not (sample / "characters.md").exists():
            self.skipTest("no smoke_4ch project")
        script = [{
            "text": "",
            "final_text": json.dumps({"findings": [], "leads_exhausted": True, "notes": "budget"}),
            "tool_calls": [
                {"id": "1", "name": "search_canon", "input": {"query": "a"}},
                {"id": "2", "name": "search_canon", "input": {"query": "b"}},
                {"id": "3", "name": "search_canon", "input": {"query": "c"}},
            ],
        }]
        mock = MockLLM()
        orig = paths._project_name
        try:
            paths.set_project_name("smoke_4ch")
            with mock.install_tools(script):
                result = continuity_open.run_open_pass(1, budget=1)
            self.assertEqual(result["agent_stop"], "budget_exhausted")
            self.assertEqual(result["tool_calls_used"], 1)
        finally:
            paths._project_name = orig

    def test_overlap_metric_when_tools_touch_a(self):
        findings_a = [{"id": "A1", "claim": "Mateo delayed", "chapters": [1], "evidence": ["Mateo"]}]
        trace = [{"tool": "search_characters", "input": {"query": "Mateo"}, "ok": True}]
        score = ct.overlap_a_tool_targets(findings_a, trace)
        self.assertGreater(score, 0.0)

    def test_sister_prefix_path_escape(self):
        from pipeline.continuity_open import _safe_project_path
        from core import paths
        proj = paths.get_project_dir().resolve()
        with self.assertRaises(ValueError):
            _safe_project_path(str(proj) + "2/secrets.md")


if __name__ == "__main__":
    unittest.main()
