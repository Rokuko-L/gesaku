#!/usr/bin/env python3
"""Smoke tests for the webui FastAPI bridge (webui/server.py).

Import-level test: the module pulls in fastapi, core.paths and the fixture
generators, so a bad import or syntax error fails here. The pure helpers are
checked against the contract's phase mapping.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "webui"))

SERVER_PATH = ROOT / "webui" / "server.py"
spec = importlib.util.spec_from_file_location("webui_server", SERVER_PATH)
webui_server = importlib.util.module_from_spec(spec)

SETTINGS_PATH = ROOT / "webui" / "routes" / "settings.py"
_settings_spec = importlib.util.spec_from_file_location("webui_settings", SETTINGS_PATH)
webui_settings = importlib.util.module_from_spec(_settings_spec)


def setUpModule():
    spec.loader.exec_module(webui_server)
    _settings_spec.loader.exec_module(webui_settings)


class ServerHelpersTest(unittest.TestCase):
    def test_norm_phase_maps_completed_runs_to_export(self):
        self.assertEqual(webui_server.norm_phase({"phase": "complete_no_pdf"}), "export")
        self.assertEqual(webui_server.norm_phase({"phase": "complete"}), "export")

    def test_norm_phase_done_focus_is_idle(self):
        self.assertEqual(webui_server.norm_phase({"current_focus": "done"}), "idle")
        self.assertEqual(webui_server.norm_phase({"phase": "drafting"}), "drafting")
        self.assertEqual(webui_server.norm_phase({}), "idle")

    def test_mask_never_exposes_full_key(self):
        self.assertEqual(webui_settings._mask(None), "")
        self.assertEqual(webui_settings._mask(""), "")
        masked = webui_settings._mask("sk-ant-0123456789abcdef")
        self.assertNotIn("0123456789abcdef", masked)
        self.assertTrue(masked.startswith("sk-ant-"))


if __name__ == "__main__":
    unittest.main()
