from core import paths
import unittest
import os
import shutil
import json
import time
import threading
from pathlib import Path

class TestUtilsStress(unittest.TestCase):
    def setUp(self):
        # Store original environment and project name
        self.orig_env_project = os.environ.get("GESAKU_PROJECT")
        self.orig_project_name = paths._project_name
        paths._project_name = None

        self.root = paths.get_root_dir()
        self.test_projects_dir = self.root / "projects"
        self.test_project_path = self.test_projects_dir / "test_stress_project"
        
        # Capture root directory files/folders before test to verify no pollution
        self.initial_root_contents = self._get_root_contents()

    def tearDown(self):
        # Restore environment and project name
        if self.orig_env_project is not None:
            os.environ["GESAKU_PROJECT"] = self.orig_env_project
        elif "GESAKU_PROJECT" in os.environ:
            del os.environ["GESAKU_PROJECT"]
        paths._project_name = self.orig_project_name

        # Clean up any temporary folders created in projects
        if self.test_project_path.exists():
            shutil.rmtree(self.test_project_path)
            
        # Assert no files were created in the root codebase directory (excluding projects/ and tests/)
        final_root_contents = self._get_root_contents()
        new_files = final_root_contents - self.initial_root_contents
        
        # Exclude expected dirs/files created during test run in projects/ or tests/
        new_root_files = [f for f in new_files if f not in ("projects", "tests")]
        self.assertEqual(len(new_root_files), 0, f"Pollution detected in root directory: {new_root_files}")

    def _get_root_contents(self):
        return {item.name for item in self.root.iterdir()}

    def test_concurrent_project_names(self):
        """Verify thread-safety of project names (known limitation of global state)."""
        results = []
        threads = []
        
        def worker(project_id):
            try:
                # Set project name
                paths.set_project_name(f"proj_{project_id}")
                # Brief sleep to allow context switching/interleaving
                time.sleep(0.05)
                # Retrieve project name
                retrieved = paths.get_project_name()
                # Check get_project_dir() is consistent
                proj_dir = paths.get_project_dir()
                results.append({
                    "id": project_id,
                    "retrieved": retrieved,
                    "dir_matches": f"proj_{project_id}" in str(proj_dir),
                    "error": None
                })
            except Exception as e:
                results.append({
                    "id": project_id,
                    "retrieved": None,
                    "dir_matches": False,
                    "error": str(e)
                })

        # Launch 5 concurrent threads setting different project names
        for i in range(5):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Check if concurrency caused mismatch/pollution
        successes = 0
        for r in results:
            if r["retrieved"] == f"proj_{r['id']}" and r["dir_matches"]:
                successes += 1
            else:
                print(f"[Concurrency Issue] Thread {r['id']} set proj_{r['id']} but got retrieved={r['retrieved']}, dir_matches={r['dir_matches']}")

        # Since paths.py uses a single global variable _project_name, we expect failures under concurrency.
        self.assertLess(successes, 5, "Expected concurrency failures due to global project name state")

    def test_directory_existence_checks_file_blocking(self):
        """Test behavior when a file exists with the name of a helper directory."""
        self.test_project_path.mkdir(parents=True, exist_ok=True)
        paths.set_project_name("test_stress_project")
        
        # Create a file where 'chapters' folder should be
        chapters_file = self.test_project_path / "chapters"
        chapters_file.write_text("not a directory", encoding="utf-8")
        
        # Calling get_chapters_dir() should fail with FileExistsError
        with self.assertRaises(FileExistsError):
            paths.get_chapters_dir()

    def test_save_registry_directory_blocking(self):
        """Test behavior of save_registry when target path is blocked by a directory."""
        self.test_project_path.mkdir(parents=True, exist_ok=True)
        paths.set_project_name("test_stress_project")
        
        registry_path = self.test_project_path / "registry.json"
        registry_path.mkdir(parents=True, exist_ok=True)  # make it a directory
        
        # Trying to save to a directory path should fail
        with self.assertRaises(Exception):
            paths.save_registry({"data": 123}, registry_path)
            
        # Ensure no .tmp file exists
        tmp_path = registry_path.with_suffix(".json.tmp")
        self.assertFalse(tmp_path.exists())

    def test_format_prompt_order_dependency(self):
        """Test how format_prompt handles nested templates and order dependency of kwargs."""
        template = "{a} {b}"
        
        # 1. When 'a' contains '{b}' and is replaced first (dict order 'a' then 'b')
        res = paths.format_prompt(template, a="{b}", b="value")
        self.assertEqual(res, "value value")

        # 2. When 'b' is defined first in insertion order
        kwargs = {}
        kwargs["b"] = "value"
        kwargs["a"] = "{b}"
        res2 = paths.format_prompt(template, **kwargs)
        print(f"[Template Ordering] Insertion order b first: '{res2}'")
        self.assertEqual(res2, "{b} value")

    def test_get_novel_title_malformed_json(self):
        """Test get_novel_title behaves correctly with malformed json in state.json."""
        self.test_project_path.mkdir(parents=True, exist_ok=True)
        paths.set_project_name("test_stress_project")
        
        state_path = paths.get_state_path()
        # Write non-JSON content
        state_path.write_text("not json at all", encoding="utf-8")
        self.assertEqual(paths.get_novel_title(), "the novel")
        
        # Write valid JSON but without title key
        state_path.write_text(json.dumps({"phase": "draft"}), encoding="utf-8")
        self.assertEqual(paths.get_novel_title(), "the novel")

    def test_project_name_path_traversal(self):
        """Test if path traversal project names are allowed (vulnerability/limitation)."""
        with self.assertRaises(ValueError):
            paths.set_project_name("../traversal_test")

if __name__ == "__main__":
    unittest.main()
