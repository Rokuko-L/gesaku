import os
import sys
import json
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

# Project env is set in main() — never at import time. unittest discover
# imports this module (filename matches test_*.py); writing GESAKU_PROJECT
# here would leak into every later module in the same process.

# Add project root to sys.path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

import pipeline.evaluate as evaluate

def run_evaluation(call_num):
    print(f"[Call {call_num}] Started evaluation at {datetime.now().isoformat()}...", flush=True)
    try:
        # Perform isolated evaluation (no file writing in evaluate_full itself)
        result = evaluate.evaluate_full()
        timestamp = datetime.now().isoformat()
        
        # Save results next to this probe
        out_dir = os.path.join(project_root, "tests")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"eval_test_{call_num}.json")
        
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({
                "call_number": call_num,
                "timestamp_completed": timestamp,
                "result": result
            }, f, indent=2)
            
        print(f"[Call {call_num}] Completed at {timestamp}.", flush=True)
        return {
            "call_number": call_num,
            "novel_score": result.get("novel_score", "N/A"),
            "timestamp_completed": timestamp,
            "success": True,
            "error": None
        }
    except Exception as e:
        timestamp = datetime.now().isoformat()
        print(f"[Call {call_num}] Failed at {timestamp}: {e}", flush=True)
        return {
            "call_number": call_num,
            "novel_score": "Error",
            "timestamp_completed": timestamp,
            "success": False,
            "error": str(e)
        }

def main():
    # Force output to use UTF-8 to prevent cp1252 encoding crashes on Windows
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')

    # Bind the probe project only for this standalone run (restored on exit).
    prev_project = os.environ.get("GESAKU_PROJECT")
    os.environ["GESAKU_PROJECT"] = "serious"
    try:
        print(f"Starting Novel Evaluator Consistency Test (5 parallel calls on project: serious)\n", flush=True)

        # Run 5 evaluations in parallel
        results = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(run_evaluation, i) for i in range(1, 6)]
            for fut in futures:
                results.append(fut.result())

        # Sort results by call number
        results.sort(key=lambda x: x["call_number"])

        # Print summary table
        print("\n" + "="*70, flush=True)
        print("                  EVALUATION CONSISTENCY SUMMARY", flush=True)
        print("="*70, flush=True)
        print(f"{'Call #':<10} | {'Novel Score':<15} | {'Timestamp Completed':<35}", flush=True)
        print("-"*70, flush=True)
        for res in results:
            score_str = str(res["novel_score"])
            ts_str = res["timestamp_completed"]
            print(f"{res['call_number']:<10} | {score_str:<15} | {ts_str:<35}", flush=True)
        print("="*70, flush=True)
    finally:
        if prev_project is None:
            os.environ.pop("GESAKU_PROJECT", None)
        else:
            os.environ["GESAKU_PROJECT"] = prev_project

if __name__ == "__main__":
    main()
