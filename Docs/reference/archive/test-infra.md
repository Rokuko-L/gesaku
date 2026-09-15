# Gesaku E2E Test Infrastructure Documentation

This document describes the End-to-End (E2E) Test Infrastructure for the Gesaku isolated project refactoring. The E2E test suite validates project isolation, dynamic path helper resolution, CLI integration, lifecycle management, git guard containment, and typesetting sandboxing.

---

## 1. System Architecture Overview

Gesaku is a fully automated novel generation pipeline. To support concurrent runs, it implements the following isolated project session architecture under the root directory:

- **Registry System (`projects/registry.json`)**: Tracks active project sessions atomically.
- **Dynamic Path Resolution (`utils.py`)**: Dynamically resolves folders and files under `projects/<project_name>/`.
- **Script Routing**: All generator scripts use path helpers from `utils` to avoid writing to the global/root workspace directory.
- **Typesetting Sandbox**: Runs the Tectonic LaTeX typesetting subprocess in `projects/<project_name>/typeset/` to contain compile logs and outputs.
- **Git Protection**: Root git ignores `projects/`, while each project directory has an independent Git repository for backup/restore.

---

## 2. 4-Tier E2E Testing Strategy

The test suite consists of exactly **80 test cases** divided into 4 tiers to verify the robustness, isolation, and recovery behavior of the refactored pipeline.

### Tier 1: Feature Coverage (>=5 cases per feature F1 to F7)
Verifies the happy-path behavior of all 7 key features of the isolation design.

- **F1: Dynamic Workspace Root Detection (`get_root_dir()`)**
- **F2: Active Project Configuration State (`utils.set_project_name()`, `utils.get_project_name()`)**
- **F3: Folder & File Path Helpers (existence side-effects and pure file path resolvers)**
- **F4: Atomic Registry Writes (`utils.save_registry()`)**
- **F5: CLI Project & Lifecycle (`--project`, `--from-scratch`, resuming)**
- **F6: Git Guards (independent git repos, root containment)**
- **F7: Script Routing & Sandboxed Typesetting (subprocesses environment and Tectonic isolation)**

### Tier 2: Boundary & Corner Cases (>=5 cases per feature F1 to F7)
Assesses boundary conditions, traversal attacks, extreme inputs, system failures, and edge behaviors.

- **F1 Boundary Cases**: Drive roots, permission errors, symlinks, missing files.
- **F2 Boundary Cases**: Traversal names, spaces, long names, thread isolation.
- **F3 Boundary Cases**: Read-only directories, case sensitivity, UTF-8 names.
- **F4 Boundary Cases**: Overwriting old temp files, locks, full disk simulation.
- **F5 Boundary Cases**: Corrupted json files, missing lifecycle states, invalid arguments.
- **F6 Boundary Cases**: Already initialized git directories, missing system git, isolation leaks.
- **F7 Boundary Cases**: Tectonic compilation failures, massive manuscripts, concurrent locks.

### Tier 3: Cross-Feature Combinations (5 cases)
Tests the interaction between multiple features when running concurrently or sequentially.

### Tier 4: Real-World Scenarios (5 cases)
Simulates end-to-end user journeys and system crashes (e.g. state corruption recovery).

---

## 3. The Test Catalog (80 Test Cases)

### File: `tests/test_path_contamination.py`
This file contains tests verifying root detection, folder helpers, path contamination, git guards, and sandboxing (F1, F3, F6, and related boundaries/combinations).

#### Tier 1: Feature Coverage
1. **`test_f1_root_via_pyproject`**: Verify `get_root_dir()` detects root when `pyproject.toml` is present.
2. **`test_f1_root_via_dotenv`**: Verify `get_root_dir()` detects root when only `.env` is present.
3. **`test_f1_root_both_present`**: Verify `get_root_dir()` succeeds when both sentinel files are present.
4. **`test_f1_root_nested_lookup`**: Verify parent traversal lookup from deep subdirectory.
5. **`test_f1_root_missing_raises_error`**: Verify `RuntimeError` is raised when sentinel files are missing.
6. **`test_f3_folder_helper_creates_dir`**: Verify chapters dir helper creates the directory on disk.
7. **`test_f3_pure_file_helper_no_creation`**: Verify state path helper returns a path but writes nothing.
8. **`test_f3_paths_change_on_project_switch`**: Verify helper path outputs change dynamically when active project name is changed.
9. **`test_f3_registry_path_resolves_outside_project`**: Verify registry path resolves directly to `projects/registry.json`.
10. **`test_f3_all_folders_exist`**: Verify calling all five folder helpers creates all five directories correctly.
11. **`test_f6_root_gitignore_rules`**: Verify root `.gitignore` contains rules that exclude `projects/` folder.
12. **`test_f6_project_git_init`**: Verify running pipeline initializes independent git repository inside project folder.
13. **`test_f6_project_gitignore_created`**: Verify project-level `.gitignore` exists and excludes output/build directories.
14. **`test_f6_project_git_commits_isolated`**: Verify commits inside the project git do not modify or touch root git.
15. **`test_f6_git_guard_prevents_root_staging`**: Verify staging in project git does not stage files modified in workspace root.

#### Tier 2: Boundary & Corner Cases
16. **`test_f1_boundary_drive_root`**: Verify lookup raises error when traversing up to filesystem drive root.
17. **`test_f1_boundary_permission_denied`**: Verify permission denied error on ancestor directories raises `RuntimeError` gracefully.
18. **`test_f1_boundary_directory_sentinel`**: Verify a folder named `.env` does not satisfy file detection.
19. **`test_f1_boundary_symlink_traversal`**: Verify lookup correctly resolves symlinked ancestors.
20. **`test_f1_boundary_empty_file_fallback`**: Verify fallback to `cwd` if `__file__` is unset or empty.
21. **`test_f3_boundary_read_only_parent`**: Verify folders helper raises clean `PermissionError` when `projects/` is read-only.
22. **`test_f3_boundary_dir_already_exists`**: Verify folders helper works without error when directories already exist.
23. **`test_f3_boundary_missing_project_parent`**: Verify pure file helpers do not raise errors if `projects/` does not exist.
24. **`test_f3_boundary_case_sensitivity`**: Verify behavior on case-insensitive filesystems when project names differ only in casing.
25. **`test_f3_boundary_unicode_project_name`**: Verify folder creation and path resolution with UTF-8 unicode chars.
26. **`test_f6_boundary_git_already_initialized`**: Verify running pipeline does not crash or overwrite existing git repository.
27. **`test_f6_boundary_git_missing_system`**: Verify system continues and warns if git binary is not installed/configured.
28. **`test_f6_boundary_git_add_containment`**: Verify running `git add -A` inside project folder does not stage root changes.
29. **`test_f6_boundary_corrupt_git_dir`**: Verify recovery or warning when project `.git` folder is corrupt.
30. **`test_f6_boundary_no_global_config`**: Verify commits succeed using local placeholders when global git username/email are unset.

#### Tier 3: Cross-Feature Combinations
31. **`test_f1_f2_f3_combination`**: Verify path resolution from nested context under environment variable project config.
32. **`test_f3_f7_path_typeset_sandboxing`**: Verify typesetting files are strictly restricted to the typesetting helper directory.

---

### File: `tests/test_multi_project.py`
This file contains tests verifying active project state, atomic registry writes, CLI project arguments, pipeline lifecycle, and typesetting sandboxing (F2, F4, F5, F7, and related boundaries/combinations).

#### Tier 1: Feature Coverage
33. **`test_f2_project_name_default`**: Verify default active project name is `"default"`.
34. **`test_f2_project_name_explicit_set`**: Verify setting project name explicitly updates state.
35. **`test_f2_project_name_env_fallback`**: Verify environment variable fallback when project name is unset.
36. **`test_f2_project_name_explicit_overrides_env`**: Verify explicit set has priority over environment variable value.
37. **`test_f2_project_name_reset`**: Verify setting project name to `None` reverts to fallback.
38. **`test_f4_save_registry_writes_json`**: Verify registry file is written correctly and contains correct JSON.
39. **`test_f4_save_registry_uses_tmp_swap`**: Verify atomic swap using temporary files and rename.
40. **`test_f4_save_registry_serialization_failure_preserves_original`**: Verify original file remains intact on invalid data format.
41. **`test_f4_save_registry_serialization_failure_cleans_tmp`**: Verify temporary files are deleted on writing failures.
42. **`test_f4_save_registry_creates_parent_dir`**: Verify writing registry auto-creates parent folders.
43. **`test_f5_cli_project_argument_parsing`**: Verify command-line argument parser parses `--project` parameter.
44. **`test_f5_pipeline_registers_new_project`**: Verify active project gets added to `registry.json` list.
45. **`test_f5_pipeline_lifecycle_resume`**: Verify pipeline reads state and resumes from previous stage.
46. **`test_f5_pipeline_lifecycle_from_scratch`**: Verify starting fresh deletes existing files and resets state to foundation.
47. **`test_f5_pipeline_multi_project_isolation`**: Verify concurrent runs in Project A and B do not overlap or corrupt each other.
48. **`test_f7_subprocess_inherits_project_env`**: Verify spawned scripts receive `GESAKU_PROJECT` in environment variables.
49. **`test_f7_typesetting_subprocess_cwd`**: Verify tectonic compilation is run with typeset directory as working directory.
50. **`test_f7_typesetting_pdf_sandboxed`**: Verify PDF is generated inside the isolated typeset folder.
51. **`test_f7_typesetting_aux_files_contained`**: Verify auxiliary build outputs do not leak to project root or parent.
52. **`test_f7_missing_tectonic_fallback`**: Verify pipeline falls back and warning is logged if tectonic is missing.

#### Tier 2: Boundary & Corner Cases
53. **`test_f2_boundary_path_traversal`**: Verify project names containing `../` are sanitized/rejected.
54. **`test_f2_boundary_invalid_characters`**: Verify setting project name to empty string or spaces is rejected.
55. **`test_f2_boundary_extreme_length`**: Verify project name handling under extreme filesystem limits.
56. **`test_f2_boundary_windows_reserved`**: Verify sanitization/rejection of Windows reserved names (`CON`, `PRN`).
57. **`test_f2_boundary_thread_isolation`**: Verify active project name is isolated per thread (uses thread-local).
58. **`test_f4_boundary_pre_existing_tmp`**: Verify atomic write succeeds even if a stale `.tmp` file already exists.
59. **`test_f4_boundary_registry_locked`**: Verify atomic write fails gracefully and cleans up if target file is locked.
60. **`test_f4_boundary_large_payload`**: Verify atomic write doesn't truncate registry under large dictionary size.
61. **`test_f4_boundary_disk_full`**: Verify original file integrity and temporary file cleanup on simulated full disk error.
62. **`test_f4_boundary_empty_payload`**: Verify validation of empty payload during registry write.
63. **`test_f5_boundary_cli_invalid_arg`**: Verify CLI rejects invalid symbols inside `--project` value.
64. **`test_f5_boundary_state_json_corrupted`**: Verify pipeline recovers (or resets) on corrupted `state.json` file.
65. **`test_f5_boundary_scratch_non_existent`**: Verify `--from-scratch` does not crash if run on a non-existent project folder.
66. **`test_f5_boundary_state_invalid_phase`**: Verify default fallback phase when `state.json` contains an invalid phase value.
67. **`test_f5_boundary_resume_missing_chapters`**: Verify recovery action if saved state mismatch with missing draft files.
68. **`test_f7_boundary_missing_typeset_dir`**: Verify typeset directory is recreated on export phase if deleted midway.
69. **`test_f7_boundary_tectonic_compile_fail`**: Verify compilation failure does not crash pipeline and logs warning.
70. **`test_f7_boundary_massive_manuscript`**: Verify typesetting handle massive manuscript without crashing.
71. **`test_f7_boundary_concurrent_tectonic_runs`**: Verify parallel compilation for separate projects does not cause locks.
72. **`test_f7_boundary_invalid_python_interpreter`**: Verify execution fallback if `sys.executable` is invalid.

#### Tier 3: Cross-Feature Combinations
73. **`test_f2_f5_f6_combination`**: Verify `--project init_project --from-scratch` initializes registry, git repository, and templates.
74. **`test_f3_f4_combination`**: Verify registry save uses dynamic path returned by `get_registry_path()` helper.
75. **`test_f5_f7_combination`**: Verify running pipeline export phase triggers tectonic compiler inside sandboxed typesetting directory.

#### Tier 4: Real-World Scenarios
76. **`test_scenario_1_fresh_novel_generation`**: E2E simulation of fresh novel creation (all planning, chapters, and pdf).
77. **`test_scenario_2_resume_interrupted_novel`**: E2E simulation of resuming drafting phase from Chapter 4 when Chapter 1-3 exist.
78. **`test_scenario_3_zero_contamination_concurrency`**: Verify running two project pipelines in parallel has zero cross-pollution.
79. **`test_scenario_4_corrupted_state_recovery`**: Simulate recovery from corrupted state by checking out the last stable version from git.
80. **`test_scenario_5_tectonic_presence_behavior`**: Verify compilation output variations based on compiler presence on host machine.

---

## 4. Implementation Details

### Subprocess Interception Mocking Strategy
To enable fast, offline, and API-free test execution in the `CODE_ONLY` network environment, the test suites use a subprocess interception strategy. 
A mock runner intercepting `subprocess.run` (and `run_pipeline.run_tool`) is used to:
1. **Mock Anthropic API calls**: `utils.call_llm` is patched to return mock text, avoiding any real HTTP requests.
2. **Git Actions**: Command lines starting with `git` are intercepted and simulated by making dummy `.git` files or return states.
3. **Tectonic Compilation**: Tectonic is mocked so that if run, it writes a mock PDF to the sandboxed typesetting folder.
4. **Subprocess Script Runs**: Script calls to `gen_world.py`, `gen_characters.py`, etc., write mock output files to the target project directory so the orchestrator's verification checks pass.

---

## 5. Execution Guide

Run the E2E test suite from the root folder:

```bash
pytest tests/test_path_contamination.py
pytest tests/test_multi_project.py
```

To run all tests:

```bash
pytest tests/test_path_contamination.py tests/test_multi_project.py
```
