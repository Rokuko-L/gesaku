from core import paths
import sys
import os
import subprocess
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).resolve().parents[1]))
import run_pipeline

def main():
    # Set active project env to test_wordcount_v2
    os.environ["GESAKU_PROJECT"] = "test_wordcount_v2"
    
    # 1. Test get_historical_best_for_chapter helper
    print("Testing get_historical_best_for_chapter helper...")
    best_score, best_commit = run_pipeline.get_historical_best_for_chapter(1)
    print(f"Ch 1: Best Score = {best_score}, Best Commit = {best_commit}")
    
    # Assert values based on results.tsv:
    # 29ee32c	ch01	7.89	2870	keep
    assert best_score == 7.89, f"Expected 7.89, got {best_score}"
    assert best_commit == "29ee32c", f"Expected '29ee32c', got {best_commit}"
    print("SUCCESS: get_historical_best_for_chapter helper matches results.tsv")
    
    # 2. Test Git Revert behavior
    print("\nTesting Git Revert behavior...")
    ch1_path = paths.get_chapters_dir() / "ch_01.md"
    original_text = ch1_path.read_text(encoding="utf-8")
    original_wc = len(original_text.split())
    print(f"Current Ch 1 on disk before test: {original_wc} words")
    
    # Write garbage/degraded text to ch_01.md to simulate a bad revision
    degraded_text = "This is a degraded version of Chapter 1 containing very few words."
    ch1_path.write_text(degraded_text, encoding="utf-8")
    print("Simulated bad revision: overwrote ch_01.md with degraded text")
    
    # Run the exact revert code from run_pipeline
    hist_best_score, hist_best_commit = run_pipeline.get_historical_best_for_chapter(1)
    
    print(f"Reverting ch_01.md to best commit: {hist_best_commit}...")
    if hist_best_commit == "HEAD":
        tracked_res = run_pipeline.run_tool(
            "git ls-files --error-unmatch chapters/ch_01.md",
            cwd=str(paths.get_project_dir())
        )
        if tracked_res.returncode == 0:
            run_pipeline.run_tool("git checkout HEAD -- chapters/ch_01.md", cwd=str(paths.get_project_dir()))
        else:
            ch1_path.unlink(missing_ok=True)
    else:
        run_pipeline.run_tool(f"git checkout {hist_best_commit} -- chapters/ch_01.md", cwd=str(paths.get_project_dir()))
        
    # Read the text back and verify it was restored to the historical best commit (2870 words)
    restored_text = ch1_path.read_text(encoding="utf-8")
    restored_wc = len(restored_text.split())
    print(f"Restored Ch 1: {restored_wc} words")
    
    assert restored_wc == 2870, f"Expected 2870 words from commit 29ee32c, got {restored_wc}"
    print("SUCCESS: git checkout successfully self-healed the chapter back to its historical best commit!")
    
    # Restore the file back to its pre-test state (2,396 words) so the test is clean
    ch1_path.write_text(original_text, encoding="utf-8")
    print("Cleaned up: restored original file content on disk.")

if __name__ == "__main__":
    main()
