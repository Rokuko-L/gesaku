# Offline Testing with MockLLM (`core/mock_llm.py`)

All pipeline code reaches the LLM through `llm.call_llm()`, and
scripts bind it at import time (`from llm import call_llm`).
`MockLLM.install()` rebinds **every** module in `sys.modules` that still
holds the original reference — so it works whether you install before or
after importing pipeline modules.

## Basic Usage

```python
from mock_llm import MockLLM

mock = MockLLM()
mock.add('{"overall_score": 8.0}')                    # consumed in order
mock.add('{"winner": "A"}', match="Compare these")    # only for matching prompts

with mock.install():
    import evaluate            # safe: no network
    result = evaluate.call_judge_json(prompt, model=validation.ScoreOutput)

assert mock.calls[0]["prompt"][:80] == ...
```

- Unexpected calls (no unconsumed rule matches) raise `AssertionError` —
  tests fail loudly instead of silently hitting the network.
- Responses are returned verbatim; damaged/truncated JSON is fine because
  production goes through the same healing parser.
- `load_fixture(mock, path)` queues responses from a JSON file:
  `[{"match": str|null, "response": str}]`.

## What to Test Offline

| Layer | How |
|---|---|
| Validation retry loop | Queue a bad-schema response then a good one; assert fix-prompt contains feedback (see `tests/test_mock_llm.py`) |
| Stage scripts | Create a temp project via `paths.set_project_name`, write input files, mock responses, call the script's functions |
| Path isolation | No mock needed — patch `paths._root_dir` to a tmp dir |
| Validation gates | Assert the gate can FAIL: queue a "violation" verdict and assert the gate blocks (see `tests/test_gatekeepers.py`). A gate whose broken path returns the same result as a passing check must be tested on its failing branch. |

## Running the Suites

```bash
uv run python -m unittest discover -s tests -p "test_*.py"
uv run python tests/test_multi_project.py
uv run python tests/test_path_contamination.py
```

All must pass without an API key. Anything needing real LLM output is E2E,
not a unit test. See [../reference/test-suites.md](../reference/test-suites.md).

Related: [../core/llm-client.md](../core/llm-client.md) ·
[../core/output-validation.md](../core/output-validation.md)
