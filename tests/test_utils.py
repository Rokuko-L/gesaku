from core import paths
import unittest
import os
import shutil
import json
from pathlib import Path


class TestUtils(unittest.TestCase):
    def setUp(self):
        # Store original environment and project name
        self.orig_env_project = os.environ.get("GESAKU_PROJECT")
        self.orig_project_name = paths._project_name
        paths._project_name = None

        # Determine workspace root
        self.root = paths.get_root_dir()
        self.test_projects_dir = self.root / "projects"

    def tearDown(self):
        # Restore environment and project name
        if self.orig_env_project is not None:
            os.environ["GESAKU_PROJECT"] = self.orig_env_project
        elif "GESAKU_PROJECT" in os.environ:
            del os.environ["GESAKU_PROJECT"]
        paths._project_name = self.orig_project_name

        # Clean up any temporary folders created in projects
        test_project_path = self.test_projects_dir / "test_temp_project"
        if test_project_path.exists():
            shutil.rmtree(test_project_path)

    def test_get_root_dir(self):
        root_dir = paths.get_root_dir()
        self.assertTrue(root_dir.exists())
        self.assertTrue((root_dir / "pyproject.toml").exists() or (root_dir / ".env").exists())

    def test_project_name_get_set(self):
        # Default fallback
        if "GESAKU_PROJECT" in os.environ:
            del os.environ["GESAKU_PROJECT"]
        self.assertEqual(paths.get_project_name(), "default")

        # Fallback to env var
        os.environ["GESAKU_PROJECT"] = "env_project"
        self.assertEqual(paths.get_project_name(), "env_project")

        # Explicit set overrides env var
        paths.set_project_name("explicit_project")
        self.assertEqual(paths.get_project_name(), "explicit_project")

    def test_save_registry_success(self):
        test_project_path = self.test_projects_dir / "test_temp_project"
        test_project_path.mkdir(parents=True, exist_ok=True)
        registry_file = test_project_path / "registry.json"

        data = {"projects": ["project1", "project2"]}
        paths.save_registry(data, registry_file)

        self.assertTrue(registry_file.exists())
        with open(registry_file, "r", encoding="utf-8") as f:
            read_data = json.load(f)
        self.assertEqual(read_data, data)

        # Ensure no leftover temp files
        tmp_file = registry_file.with_suffix(".json.tmp")
        self.assertFalse(tmp_file.exists())

    def test_save_registry_failure_cleanup(self):
        test_project_path = self.test_projects_dir / "test_temp_project"
        test_project_path.mkdir(parents=True, exist_ok=True)
        registry_file = test_project_path / "registry.json"

        # Pass something that cannot be serialized to JSON (like a set)
        bad_data = {"projects": {1, 2, 3}}

        with self.assertRaises(Exception):
            paths.save_registry(bad_data, registry_file)

        # The target file shouldn't have been created
        self.assertFalse(registry_file.exists())

        # The temp file should have been cleaned up
        tmp_file = registry_file.with_suffix(".json.tmp")
        self.assertFalse(tmp_file.exists())

    def test_folder_helpers_create_dirs(self):
        paths.set_project_name("test_temp_project")
        project_path = self.test_projects_dir / "test_temp_project"

        # Before helper runs, the folders do not exist
        self.assertFalse((project_path / "chapters").exists())
        self.assertFalse((project_path / "edit_logs").exists())
        self.assertFalse((project_path / "eval_logs").exists())
        self.assertFalse((project_path / "briefs").exists())
        self.assertFalse((project_path / "typeset").exists())

        # Call folder helpers
        chapters_dir = paths.get_chapters_dir()
        edit_logs_dir = paths.get_edit_logs_dir()
        eval_logs_dir = paths.get_eval_logs_dir()
        briefs_dir = paths.get_briefs_dir()
        typeset_dir = paths.get_typeset_dir()

        # Check paths match and directories are created
        self.assertEqual(chapters_dir, project_path / "chapters")
        self.assertTrue(chapters_dir.exists())

        self.assertEqual(edit_logs_dir, project_path / "edit_logs")
        self.assertTrue(edit_logs_dir.exists())

        self.assertEqual(eval_logs_dir, project_path / "eval_logs")
        self.assertTrue(eval_logs_dir.exists())

        self.assertEqual(briefs_dir, project_path / "briefs")
        self.assertTrue(briefs_dir.exists())

        self.assertEqual(typeset_dir, project_path / "typeset")
        self.assertTrue(typeset_dir.exists())

    def test_pure_file_helpers_no_side_effects(self):
        paths.set_project_name("test_temp_project")
        project_path = self.test_projects_dir / "test_temp_project"

        # Check file helper returns correct path, but does NOT create files or directories
        self.assertEqual(paths.get_outline_path(), project_path / "outline.md")
        self.assertFalse(paths.get_outline_path().exists())

        self.assertEqual(paths.get_state_path(), project_path / "state.json")
        self.assertFalse(paths.get_state_path().exists())

        self.assertEqual(paths.get_results_path(), project_path / "results.tsv")
        self.assertFalse(paths.get_results_path().exists())

        self.assertEqual(paths.get_registry_path(), self.test_projects_dir / "registry.json")

        self.assertEqual(paths.get_world_path(), project_path / "world.md")
        self.assertFalse(paths.get_world_path().exists())

        self.assertEqual(paths.get_voice_path(), project_path / "voice.md")
        self.assertFalse(paths.get_voice_path().exists())

        self.assertEqual(paths.get_characters_path(), project_path / "characters.md")
        self.assertFalse(paths.get_characters_path().exists())

        self.assertEqual(paths.get_canon_path(), project_path / "canon.md")
        self.assertFalse(paths.get_canon_path().exists())

        self.assertEqual(paths.get_manuscript_path(), project_path / "manuscript.md")
        self.assertFalse(paths.get_manuscript_path().exists())

        self.assertEqual(paths.get_reviews_path(), project_path / "reviews.md")
        self.assertFalse(paths.get_reviews_path().exists())

        self.assertEqual(paths.get_arc_summary_path(), project_path / "arc_summary.md")
        self.assertFalse(paths.get_arc_summary_path().exists())

        # Ensure project folder itself wasn't created either
        self.assertFalse(project_path.exists())

    def test_get_novel_title(self):
        paths.set_project_name("test_temp_project")
        project_path = self.test_projects_dir / "test_temp_project"

        # 1. Default fallback when state.json doesn't exist
        self.assertEqual(paths.get_novel_title(), "the novel")

        # 2. When state.json exists and has a title
        project_path.mkdir(parents=True, exist_ok=True)
        state_path = paths.get_state_path()
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({"title": "A Great Novel Title"}, f)

        self.assertEqual(paths.get_novel_title(), "A Great Novel Title")

        # 3. When state.json is invalid JSON
        with open(state_path, "w", encoding="utf-8") as f:
            f.write("{invalid_json")

        self.assertEqual(paths.get_novel_title(), "the novel")

    def test_get_novel_title_directory_error(self):
        # Test error handling when state.json is a directory
        paths.set_project_name("test_temp_project")
        project_path = self.test_projects_dir / "test_temp_project"
        project_path.mkdir(parents=True, exist_ok=True)
        state_path = paths.get_state_path()
        
        # Create state.json as a directory instead of a file
        state_path.mkdir(parents=True, exist_ok=True)
        
        # We expect get_novel_title() to return "the novel" fallback rather than raising an exception.
        self.assertEqual(paths.get_novel_title(), "the novel")
            
        # Clean up the directory state.json so tearDown can clean up the project path
        state_path.rmdir()

    def test_format_prompt_ordering_dependency(self):
        # Verify that ordering of replacements can cause dependency issues
        # template = "{a} {b}" where a has value "{b}" and b has value "2"
        # If 'a' is replaced first: "{a} {b}" -> "{b} {b}" -> "2 2"
        # If 'b' is replaced first: "{a} {b}" -> "{a} 2" -> "{b} 2" (no further replacement)
        # Let's test how format_prompt behaves.
        template = "{a} {b}"
        # Case 1: kwargs ordered as a, then b
        res1 = paths.format_prompt(template, a="{b}", b="2")
        # Case 2: kwargs ordered as b, then a
        res2 = paths.format_prompt(template, b="2", a="{b}")
        
        # We assert that the output depends on kwargs order
        self.assertNotEqual(res1, res2)
        self.assertEqual(res1, "2 2")
        self.assertEqual(res2, "{b} 2")

    def test_concurrent_project_name_modification(self):
        # Verify that concurrent modifications to the global _project_name are not thread-safe.
        import threading
        import time

        errors = []

        def run_thread(name, delay):
            try:
                paths.set_project_name(name)
                time.sleep(delay)
                self.assertEqual(paths.get_project_name(), name)
            except AssertionError as e:
                errors.append(e)

        t1 = threading.Thread(target=run_thread, args=("thread_project_1", 0.1))
        t2 = threading.Thread(target=run_thread, args=("thread_project_2", 0.01))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # We expect that t1 failed because t2 overwrote the global project name
        self.assertGreater(len(errors), 0, "Expected thread-safety violation but got none.")

    def test_directory_existence_checks_file_collision(self):
        # Test collision where a file exists with the name of a folder helper
        paths.set_project_name("test_temp_project")
        project_path = self.test_projects_dir / "test_temp_project"
        project_path.mkdir(parents=True, exist_ok=True)
        
        # Create a file named 'chapters'
        chapters_file = project_path / "chapters"
        chapters_file.touch()
        
        # Now call get_chapters_dir() which tries to mkdir 'chapters'
        with self.assertRaises(FileExistsError):
            paths.get_chapters_dir()
            
        chapters_file.unlink()

    def test_save_registry_target_is_directory(self):
        # Test save_registry when the target path is actually a directory.
        test_project_path = self.test_projects_dir / "test_temp_project"
        test_project_path.mkdir(parents=True, exist_ok=True)
        
        # Target path is a directory
        registry_dir = test_project_path / "registry.json"
        registry_dir.mkdir(parents=True, exist_ok=True)
        
        data = {"projects": ["project1"]}
        with self.assertRaises(Exception):
            paths.save_registry(data, registry_dir)
            
        # Ensure temporary file .tmp was cleaned up or not created
        tmp_file = registry_dir.with_suffix(".json.tmp")
        self.assertFalse(tmp_file.exists())
        
        # Clean up directory
        registry_dir.rmdir()


if __name__ == "__main__":
    unittest.main()

