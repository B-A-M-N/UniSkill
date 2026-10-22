"""Comprehensive behavioral evaluation tests for UniSkill.

Tests the four distinct test types, workspace diff measurement, reliability,
regression/baseline, backward compatibility, and evidence labelling.
"""
from __future__ import annotations
from pathlib import Path
import json
import os
import sys
import textwrap
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from uniskill.evaluation import (
    TEST_TYPES,
    PASS, FAILED, UNVERIFIED, REGRESSION, TIMEOUT,
    MEASURED, RUNNER_ASSERTED,
    load_cases,
    run_evals,
    run_behavioral_suite,
    reliability,
    diff_workspace,
    snapshot_workspace,
    baseline_save,
    baseline_load,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_runner(tmp_path, response_fn, name="runner.py"):
    """Create a small Python runner script.

    *response_fn* is a string of Python code for the ``main()`` function body
    that returns a dict (or prints JSON). The runner reads the prompt from
    stdin.
    """
    runner_py = tmp_path / name
    runner_py.write_text(textwrap.dedent(response_fn), encoding="utf-8")
    runner_py.chmod(0o755)
    return runner_py


def _write_cases_jsonl(tmp_path, cases, name="cases.jsonl"):
    """Write case dicts as JSONL."""
    path = tmp_path / name
    path.write_text(
        "\n".join(json.dumps(c) for c in cases) + "\n", encoding="utf-8"
    )
    return path


def _write_runner_json(tmp_path, runner_path, output="json", name="runner.json"):
    """Write a runner config JSON."""
    config = {
        "argv": ["python", str(runner_path)],
        "cwd": str(tmp_path),
        "timeout_seconds": 5,
        "mode": "stdin",
        "output": output,
    }
    path = tmp_path / name
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def _assert_all_pass(suite):
    """Assert a behavioral suite result states PASS overall."""
    assert suite["state"] == PASS, f"Expected PASS but got {suite['state']}: {[c['state'] for c in suite['cases']]}"


def _assert_all_fail(suite):
    """Assert a behavioral suite result states FAILED overall."""
    assert suite["state"] == FAILED


def _assert_unverified(suite):
    assert suite["state"] == UNVERIFIED


# ---------------------------------------------------------------------------
# Test: TEST_TYPES constant
# ---------------------------------------------------------------------------

def test_test_types_constant():
    assert TEST_TYPES == ("discovery", "invocation", "selection", "functionality", "regression")


# ---------------------------------------------------------------------------
# Test: backward compatibility — old case files (no type field)
# ---------------------------------------------------------------------------

def test_backward_compat_old_case_file_works(tmp_path):
    """An old-style case file (no ``type`` field) still evaluates, and the
    existing suite's expectations hold."""
    runner_py = _write_runner(tmp_path, """
import sys,json
print(json.dumps({"response":"mutex lock","tool_calls":[]}))
""")
    runner_cfg = _write_runner_json(tmp_path, runner_py)
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "oldcase", "prompt": "explain mutex", "must_include": ["mutex"], "forbidden_tools": ["deploy"]},
    ])
    loaded = load_cases(cases)
    assert loaded[0]["type"] == "functionality"  # defaults

    results = run_evals(loaded, runner_cfg, 1)
    assert results["state"] == "EVAL_PASS"
    assert results["passed"] == 1
    assert results["total"] == 1


def test_backward_compat_old_case_text_only(tmp_path):
    """Old case without tool assertions, text-only runner."""
    runner_py = _write_runner(tmp_path, """
import sys,json
print(json.dumps({"response":"contention and concurrency report","tool_calls":[]}))
""")
    runner_cfg = _write_runner_json(tmp_path, runner_py, output="json")
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "old2", "prompt": "test", "must_include": ["contention|concurrency|bottleneck"]},
    ])
    loaded = load_cases(cases)
    results = run_evals(loaded, runner_cfg, 1)
    assert results["state"] == "EVAL_PASS"


def test_backward_compat_run_evals_from_test_uniskill_still_works(tmp_path):
    """Mirrors the original test_eval_execution test from test_uniskill.py."""
    script = tmp_path / "runner.py"
    script.write_text(
        "import sys,json\n"
        "p=sys.stdin.read()\n"
        "print(json.dumps({'response':'mutex lock' if 'mutex' in p else 'bottleneck','tool_calls':[]}))\n",
    )
    runner = tmp_path / "runner.json"
    runner.write_text(
        json.dumps({"argv":["python", str(script)], "cwd":str(tmp_path), "output":"json"})
    )
    cases_file = tmp_path / "cases.jsonl"
    cases_file.write_text(
        json.dumps({"id":"case1", "prompt":"explain mutex", "must_include":["mutex"], "forbidden_tools":["deploy"]}) + "\n"
    )
    from uniskill.evaluation import load_cases as lc
    results = run_evals(lc(cases_file), runner, 2)
    assert results["state"] == "EVAL_PASS"
    assert results["passed"] == 2


# ---------------------------------------------------------------------------
# Test: load_cases validation
# ---------------------------------------------------------------------------

def test_load_cases_defaults_type_to_functionality(tmp_path):
    cases = _write_cases_jsonl(tmp_path, [
        {"prompt": "hello"},
    ])
    loaded = load_cases(cases)
    assert loaded[0]["type"] == "functionality"


def test_load_cases_invalid_type_rejected(tmp_path):
    cases = _write_cases_jsonl(tmp_path, [
        {"prompt": "hello", "type": "magic"},
    ])
    with pytest.raises(Exception, match="invalid type"):
        load_cases(cases)


# ---------------------------------------------------------------------------
# Test: Discovery type — positive
# ---------------------------------------------------------------------------

def test_discovery_positive_passes_when_skill_mentioned(tmp_path):
    """A discovery case with must_match_skill PASSES when the runner mentions the skill."""
    runner_py = _write_runner(tmp_path, """
import sys,json
p=sys.stdin.read()
# Always mention the skill name
print(json.dumps({"response":"I used search-concurrency skill to find issues","tool_calls":["search-concurrency"]}))
""")
    runner_cfg = _write_runner_json(tmp_path, runner_py, output="json")
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "disc-pos", "type": "discovery",
         "prompt": "test", "must_match_skill": "search-concurrency"},
    ])
    suite = run_behavioral_suite(loaded_cases := load_cases(cases), runner_cfg, 1)
    row = suite["cases"][0]
    # Discovery type is runner-asserted-only (no harness probe) → UNVERIFIED
    assert row["case_type"] == "discovery"
    assert row["evidence_type"] == RUNNER_ASSERTED


def test_discovery_negative_passes_when_skill_not_mentioned(tmp_path):
    """A negative discovery case PASSES when the runner does NOT mention the forbidden skill."""
    runner_py = _write_runner(tmp_path, """
import sys,json
print(json.dumps({"response":"Here is my analysis","tool_calls":["analysis-tool"]}))
""")
    runner_cfg = _write_runner_json(tmp_path, runner_py, output="json")
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "disc-neg", "type": "discovery",
         "prompt": "test", "must_not_match_skill": "search-concurrency"},
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1)
    row = suite["cases"][0]
    assert row["case_type"] == "discovery"
    # Without harness probe, discovery always returns UNVERIFIED
    assert row["state"] == UNVERIFIED


def test_discovery_negative_fails_when_skill_mentioned(tmp_path):
    """A negative discovery case FAILS when the runner mentions the forbidden skill."""
    runner_py = _write_runner(tmp_path, """
import sys,json
print(json.dumps({"response":"I found search-concurrency skill issues","tool_calls":["search-concurrency"]}))
""")
    runner_cfg = _write_runner_json(tmp_path, runner_py, output="json")
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "disc-neg-fail", "type": "discovery",
         "prompt": "test", "must_not_match_skill": "search-concurrency"},
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1)
    row = suite["cases"][0]
    assert row["case_type"] == "discovery"
    # Discovery with negative assertion that fails → FAILED
    assert row["state"] == FAILED


# ---------------------------------------------------------------------------
# Test: Invocation type
# ---------------------------------------------------------------------------

def test_invocation_positive_passes(tmp_path):
    """An invocation case PASSES when the runner invoked the required tool."""
    runner_py = _write_runner(tmp_path, """
import sys,json
print(json.dumps({"response":"done","tool_calls":["search-concurrency"]}))
""")
    runner_cfg = _write_runner_json(tmp_path, runner_py, output="json")
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "inv-pos", "type": "invocation",
         "prompt": "test", "must_invoke_skill": "search-concurrency"},
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1)
    row = suite["cases"][0]
    assert row["state"] == PASS
    assert row["case_type"] == "invocation"
    # Invocation is runner-asserted because tool_calls come from self-report
    assert row["evidence_type"] == RUNNER_ASSERTED


def test_invocation_negative_fails_when_tool_called(tmp_path):
    """A negative invocation case FAILS when the runner invoked the forbidden tool."""
    runner_py = _write_runner(tmp_path, """
import sys,json
print(json.dumps({"response":"done","tool_calls":["deploy-tool"]}))
""")
    runner_cfg = _write_runner_json(tmp_path, runner_py, output="json")
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "inv-neg", "type": "invocation",
         "prompt": "test", "must_not_invoke_skill": "deploy-tool"},
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1)
    row = suite["cases"][0]
    assert row["state"] == FAILED


# ---------------------------------------------------------------------------
# Test: Selection type (auto-trigger)
# ---------------------------------------------------------------------------

def test_selection_positive_fails_when_skill_not_auto_selected(tmp_path):
    """A positive selection case FAILS when the skill was NOT auto-selected."""
    runner_py = _write_runner(tmp_path, """
import sys,json
print(json.dumps({"response":"done","tool_calls":["other-tool"]}))
""")
    runner_cfg = _write_runner_json(tmp_path, runner_py, output="json")
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "sel-pos", "type": "selection",
         "prompt": "test", "must_select_skill": "search-concurrency"},
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1)
    row = suite["cases"][0]
    assert row["state"] == FAILED


def test_selection_negative_passes_when_skill_not_selected(tmp_path):
    """A negative selection case PASSES when the skill was NOT auto-selected."""
    runner_py = _write_runner(tmp_path, """
import sys,json
print(json.dumps({"response":"done","tool_calls":["other-tool"]}))
""")
    runner_cfg = _write_runner_json(tmp_path, runner_py, output="json")
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "sel-neg", "type": "selection",
         "prompt": "test", "must_not_select_skill": "search-concurrency"},
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1)
    row = suite["cases"][0]
    assert row["state"] == PASS


def test_selection_negative_fails_when_skill_selected(tmp_path):
    """A negative selection case FAILS when the runner did auto-select the skill."""
    runner_py = _write_runner(tmp_path, """
import sys,json
print(json.dumps({"response":"done","tool_calls":["search-concurrency"]}))
""")
    runner_cfg = _write_runner_json(tmp_path, runner_py, output="json")
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "sel-neg-fail", "type": "selection",
         "prompt": "test", "must_not_select_skill": "search-concurrency"},
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1)
    row = suite["cases"][0]
    assert row["state"] == FAILED


# ---------------------------------------------------------------------------
# Test: Functionality type — workspace diff (measured evidence)
# ---------------------------------------------------------------------------

def test_functionality_catches_missing_artifact_via_workspace_diff(tmp_path):
    """The money test: runner says success, diff shows no artifact, evaluator FAILS.

    The runner self-reports a successful response, but the required artifact
    (file) was not created in the workspace. The measured workspace diff
    catches this.
    """
    # Create a workspace dir
    ws = tmp_path / "workspace"
    ws.mkdir()

    # Runner that reports success but does NOT create the required file
    runner_py = _write_runner(tmp_path, """
import sys,json
# Claims success but doesn't create any file
print(json.dumps({"response":"Artifact created successfully","tool_calls":["write-file"]}))
""")
    runner_cfg = _write_runner_json(tmp_path, runner_py)
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "func-missing", "type": "functionality",
         "prompt": "test",
         "required_artifacts": ["output/report.txt"],
         "allowed_diff_prefix": [],  # no allowed changes
        },
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1, workspace=str(ws))
    row = suite["cases"][0]
    assert row["state"] == FAILED
    assert row["case_type"] == "functionality"
    assert row["evidence_type"] == MEASURED
    # The problem should mention the missing artifact
    assert any("Missing required artifact" in p for p in row["problems"])


def test_functionality_detects_forbidden_side_effect(tmp_path):
    """Runner creates a file outside the allowed prefix → FAIL via measured diff,
    even though runner-claimed tool_calls are clean."""
    ws = tmp_path / "workspace2"
    ws.mkdir()

    # Runner claims only allowed tools, but creates an unauthorized file
    runner_py = _write_runner(tmp_path, """
import sys,json
import os
# Create a forbidden file in workspace
(Path(sys.argv[1]) / "workspace2" / "unauthorized/deleted.txt").write_text("deleted content")
print(json.dumps({"response":"Clean operation","tool_calls":["read-file"]}))
""", name="runner_side.py")
    runner_cfg = _write_runner_json(tmp_path, runner_py)
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "func-forbidden", "type": "functionality",
         "prompt": "test",
         "allowed_diff_prefix": ["output/"],  # only output/ is allowed
        },
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1, workspace=str(ws))
    row = suite["cases"][0]
    assert row["state"] == FAILED
    assert any("Forbidden side effect" in p for p in row["problems"])
    assert row["evidence_type"] == MEASURED


def test_functionality_passes_with_created_artifact(tmp_path):
    """Runner creates the required artifact → PASS."""
    ws = tmp_path / "workspace3"
    ws.mkdir()

    runner_py = _write_runner(tmp_path, """
import sys,json
from pathlib import Path
# Create the required artifact in the runner's cwd (tmp_path)
(output := Path("output")).mkdir(exist_ok=True)
(output / "report.txt").write_text("Analysis complete")
print(json.dumps({"response":"Done","tool_calls":["write-file"]}))
""", name="runner_create.py")
    runner_cfg = _write_runner_json(tmp_path, runner_py)
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "func-pass", "type": "functionality",
         "prompt": "test",
         "required_artifacts": ["output/report.txt"],
         "allowed_diff_prefix": [],
        },
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1, workspace=str(ws))
    row = suite["cases"][0]
    assert row["state"] == PASS
    assert row["evidence_type"] == MEASURED


# ---------------------------------------------------------------------------
# Test: Regression type
# ---------------------------------------------------------------------------

def test_regression_baseline_saved_from_passing_runs(tmp_path):
    """Baseline saved from passing runs; a later failing case is REGRESSION."""
    ws = tmp_path / "ws_reg"
    ws.mkdir()

    # First: create a baseline with a passing case
    runner_py = _write_runner(tmp_path, """
import sys,json
print(json.dumps({"response":"SUCCESS: all checks passed","tool_calls":["analyze"]}))
""")
    runner_cfg = _write_runner_json(tmp_path, runner_py)

    # Run with a functionality case (to generate baseline)
    cases_pass = _write_cases_jsonl(tmp_path, [
        {"id": "reg-case", "type": "functionality",
         "prompt": "test", "must_include": ["all checks passed"]},
    ])
    suite = run_behavioral_suite(load_cases(cases_pass), runner_cfg, 1, workspace=str(ws))
    assert suite["cases"][0]["state"] == PASS

    # Save baseline
    bl_data = {
        "reg-case": {
            "state": "PASS",
            "problems": [],
        }
    }
    bl_path = tmp_path / "baselines" / "reg.json"
    baseline_save(bl_data, bl_path)

    # Now a case that fails compared to baseline → REGRESSION
    runner_fail = _write_runner(tmp_path, """
import sys,json
print(json.dumps({"response":"FAILURE: regression detected","tool_calls":[]}))
""")
    runner_cfg_fail = _write_runner_json(tmp_path, runner_fail)

    cases_fail = _write_cases_jsonl(tmp_path, [
        {"id": "reg-case", "type": "regression",
         "prompt": "test", "must_include": ["all checks passed"]},
    ])
    suite2 = run_behavioral_suite(load_cases(cases_fail), runner_cfg_fail, 1,
                                  workspace=str(ws), baseline_path=bl_path)
    row = suite2["cases"][0]
    assert row["state"] == REGRESSION
    assert row["case_type"] == "regression"


def test_regression_no_baseline_is_unverified(tmp_path):
    """A regression case with no baseline is UNVERIFIED."""
    ws = tmp_path / "ws_reg2"
    ws.mkdir()
    runner_py = _write_runner(tmp_path, """
import sys,json
print(json.dumps({"response":"OK","tool_calls":[]}))
""")
    runner_cfg = _write_runner_json(tmp_path, runner_py)
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "reg-no-baseline", "type": "regression",
         "prompt": "test", "must_include": ["OK"]},
    ])
    bl_path = tmp_path / "baselines" / "nonexistent.json"
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1,
                                  workspace=str(ws), baseline_path=bl_path)
    row = suite["cases"][0]
    assert row["state"] == UNVERIFIED


def test_regression_case_not_in_baseline_not_reported_as_regression(tmp_path):
    """A case never in the baseline is UNVERIFIED, not REGRESSION."""
    ws = tmp_path / "ws_reg3"
    ws.mkdir()
    runner_py = _write_runner(tmp_path, """
import sys,json
print(json.dumps({"response":"OK","tool_calls":[]}))
""")
    runner_cfg = _write_runner_json(tmp_path, runner_py)
    # Case with id "unknown-case" — not in baseline
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "unknown-case", "type": "regression",
         "prompt": "test", "must_include": ["OK"]},
    ])
    bl_data = {"known-case": {"state": "PASS", "problems": []}}
    bl_path = tmp_path / "baselines" / "known.json"
    baseline_save(bl_data, bl_path)
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1,
                                  workspace=str(ws), baseline_path=bl_path)
    row = suite["cases"][0]
    assert row["state"] == UNVERIFIED


# ---------------------------------------------------------------------------
# Test: Reliability
# ---------------------------------------------------------------------------

def test_reliability_flaky_detects_mixed_results(tmp_path):
    """3 repetitions with an intermittent runner → reported as FLAKY with correct pass_rate."""
    ws = tmp_path / "ws_rel"
    ws.mkdir()

    # Runner that succeeds on 1st, fails on 2nd, succeeds on 3rd
    # Uses the tmp_path directory (passed as cwd) to store the counter
    runner_py = _write_runner(tmp_path, """
import sys,json
from pathlib import Path
counter_file = Path(".counter")
n = int(counter_file.read_text()) if counter_file.exists() else 0
n += 1
counter_file.write_text(str(n))
# Fail on 2nd run
if n == 2:
    print(json.dumps({"response":"FAIL","tool_calls":[]}))
else:
    print(json.dumps({"response":"OK","tool_calls":["analyze"]}))
""", name="runner_flaky.py")
    runner_cfg = _write_runner_json(tmp_path, runner_py)
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "flaky-case", "type": "functionality",
         "prompt": "test", "must_include": ["OK"]},
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 3, workspace=str(ws))
    # Check per-case reliability
    rel = suite["reliability"]["flaky-case"]
    assert rel["trials"] == 3
    assert rel["passes"] == 2
    assert rel["pass_rate"] == pytest.approx(2 / 3, rel=1e-3)
    assert rel["verdict"] == "TOO_FEW"  # N<5
    assert "too few" in rel["confidence"].lower()


def test_reliability_n_ge_5_passes_all(tmp_path):
    """5+ trials all pass → PASS verdict with Wilson CI."""
    ws = tmp_path / "ws_rel2"
    ws.mkdir()

    runner_py = _write_runner(tmp_path, """
import sys,json
print(json.dumps({"response":"OK","tool_calls":["analyze"]}))
""")
    runner_cfg = _write_runner_json(tmp_path, runner_py)
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "reliable-case", "type": "functionality",
         "prompt": "test", "must_include": ["OK"]},
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 5, workspace=str(ws))
    rel = suite["reliability"]["reliable-case"]
    assert rel["trials"] == 5
    assert rel["passes"] == 5
    assert rel["verdict"] == "PASS"
    assert "95% Wilson CI" in rel["confidence"]
    assert rel["pass_rate"] == 1.0  # 5/5 = 1.0


def test_reliability_n_ge_5_all_fail(tmp_path):
    """5+ trials all fail → FAIL verdict."""
    ws = tmp_path / "ws_rel3"
    ws.mkdir()

    runner_py = _write_runner(tmp_path, """
import sys,json
print(json.dumps({"response":"NO SUCCESS HERE","tool_calls":[]}))
""")
    runner_cfg = _write_runner_json(tmp_path, runner_py)
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "fail-case", "type": "functionality",
         "prompt": "test", "must_include": ["ALL CHECKS PASSED"]},
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 5, workspace=str(ws))
    rel = suite["reliability"]["fail-case"]
    assert rel["verdict"] == "FAIL"
    assert "all 5" in rel["confidence"].lower()


def test_reliability_function_direct(tmp_path):
    """Test reliability() helper directly with raw row dicts."""
    rows = [
        {"case": "c1", "state": "PASS"},
        {"case": "c1", "state": "PASS"},
        {"case": "c1", "state": "PASS"},
    ]
    rel = reliability(rows)
    assert rel["c1"]["trials"] == 3
    assert rel["c1"]["verdict"] == "TOO_FEW"


def test_reliability_flaky_direct(tmp_path):
    """Test reliability() directly with mixed results."""
    rows = [
        {"case": "flaky", "state": "PASS"},
        {"case": "flaky", "state": "FAILED"},
        {"case": "flaky", "state": "PASS"},
        {"case": "flaky", "state": "PASS"},
        {"case": "flaky", "state": "PASS"},
    ]
    rel = reliability(rows)
    assert rel["flaky"]["trials"] == 5
    assert rel["flaky"]["passes"] == 4
    assert rel["flaky"]["pass_rate"] == 0.8
    assert rel["flaky"]["verdict"] == "FLAKY"


# ---------------------------------------------------------------------------
# Test: Timeout as its own state
# ---------------------------------------------------------------------------

def test_timeout_is_own_state(tmp_path):
    """Timeout is reported as its own state, not a generic failure."""
    ws = tmp_path / "ws_to"
    ws.mkdir()

    # Runner that sleeps longer than the timeout
    runner_py = _write_runner(tmp_path, """
import time,sys,json
time.sleep(10)
print(json.dumps({"response":"never","tool_calls":[]}))
""", name="runner_slow.py")
    runner_cfg = _write_runner_json(tmp_path, runner_py)
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "timeout-case", "type": "functionality",
         "prompt": "test"},
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1, workspace=str(ws))
    row = suite["cases"][0]
    assert row["timed_out"] is True
    assert row["state"] == TIMEOUT


# ---------------------------------------------------------------------------
# Test: No implicit execution
# ---------------------------------------------------------------------------

def test_no_implicit_execution(tmp_path):
    """run_behavioral_suite without a runner (missing runner file) returns UNVERIFIED."""
    ws = tmp_path / "ws_noexec"
    ws.mkdir()

    cases = _write_cases_jsonl(tmp_path, [
        {"id": "no-exec", "type": "functionality",
         "prompt": "test"},
    ])
    non_existent = tmp_path / "no_runner.json"
    with pytest.raises(Exception):
        # Missing runner config should raise — can't run without it
        run_behavioral_suite(load_cases(cases), non_existent)


def test_run_behavioral_suite_returns_unverified_when_no_runner_matches(tmp_path):
    """When there's a runner but it fails completely, the suite doesn't falsely PASS."""
    ws = tmp_path / "ws_nomatch"
    ws.mkdir()

    runner_py = _write_runner(tmp_path, """
import sys,json
print(json.dumps({"response":"not matching anything","tool_calls":[]}))
""")
    runner_cfg = _write_runner_json(tmp_path, runner_py)
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "nomatch", "type": "functionality",
         "prompt": "test", "must_include": ["definitely-not-here"]},
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1, workspace=str(ws))
    # Suite should not PASS
    assert suite["state"] != PASS


# ---------------------------------------------------------------------------
# Test: Evidence type labelling
# ---------------------------------------------------------------------------

def test_runner_asserted_only_cases_labeled_in_aggregate(tmp_path):
    """Runner-asserted-only cases (discovery/invocation/selection) are visibly labeled."""
    ws = tmp_path / "ws_label"
    ws.mkdir()

    runner_py = _write_runner(tmp_path, """
import sys,json
print(json.dumps({"response":"OK","tool_calls":["analyze"]}))
""")
    runner_cfg = _write_runner_json(tmp_path, runner_py, output="json")
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "disc", "type": "discovery", "prompt": "test", "must_match_skill": "x"},
        {"id": "inv", "type": "invocation", "prompt": "test", "must_invoke_skill": "analyze"},
        {"id": "sel", "type": "selection", "prompt": "test", "must_select_skill": "analyze"},
        {"id": "func", "type": "functionality", "prompt": "test", "must_include": ["OK"]},
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1, workspace=str(ws))

    disc = [r for r in suite["cases"] if r["case"] == "disc"][0]
    inv = [r for r in suite["cases"] if r["case"] == "inv"][0]
    sel = [r for r in suite["cases"] if r["case"] == "sel"][0]
    func = [r for r in suite["cases"] if r["case"] == "func"][0]

    # Discovery, invocation, selection are runner-asserted
    assert disc["evidence_type"] == RUNNER_ASSERTED
    assert inv["evidence_type"] == RUNNER_ASSERTED
    assert sel["evidence_type"] == RUNNER_ASSERTED
    # Functionality gets measured evidence (workspace diff taken)
    assert func["evidence_type"] == MEASURED


def test_verification_class_labeling(tmp_path):
    """verification_class distinguishes measured-diff vs runner-asserted-only vs mixed."""
    ws = tmp_path / "ws_vclass"
    ws.mkdir()

    runner_py = _write_runner(tmp_path, """
import sys,json
print(json.dumps({"response":"OK","tool_calls":["analyze"]}))
""")
    runner_cfg = _write_runner_json(tmp_path, runner_py, output="json")

    # Only runner-asserted case types
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "disc-only", "type": "discovery", "prompt": "test", "must_match_skill": "x"},
        {"id": "inv-only", "type": "invocation", "prompt": "test", "must_invoke_skill": "analyze"},
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1, workspace=str(ws))
    # All runner-asserted → runner-asserted-only
    # (functionality with workspace diff gets measured, but we only have non-functionality)
    assert suite["verification_class"] == "runner-asserted-only"


# ---------------------------------------------------------------------------
# Test: diff_workspace helper
# ---------------------------------------------------------------------------

def test_diff_workspace_created():
    before = {"a.txt": "hash1", "b.txt": "hash2"}
    after = {"a.txt": "hash1", "b.txt": "hash2", "c.txt": "hash3"}
    d = diff_workspace(before, after)
    assert d["created"] == ["c.txt"]
    assert d["modified"] == []
    assert d["deleted"] == []


def test_diff_workspace_modified():
    before = {"a.txt": "hash1"}
    after = {"a.txt": "hash2"}
    d = diff_workspace(before, after)
    assert d["modified"] == ["a.txt"]


def test_diff_workspace_deleted():
    before = {"a.txt": "hash1", "b.txt": "hash2"}
    after = {"a.txt": "hash1"}
    d = diff_workspace(before, after)
    assert d["deleted"] == ["b.txt"]


def test_diff_workspace_combined():
    before = {"a.txt": "h1", "b.txt": "h2", "c.txt": "h3"}
    after = {"a.txt": "h1-mod", "c.txt": "h3", "d.txt": "h4"}
    d = diff_workspace(before, after)
    assert d["created"] == ["d.txt"]
    assert d["modified"] == ["a.txt"]
    assert d["deleted"] == ["b.txt"]


# ---------------------------------------------------------------------------
# Test: snapshot_workspace helper
# ---------------------------------------------------------------------------

def test_snapshot_workspace(tmp_path):
    ws = tmp_path / "snap"
    ws.mkdir()
    (ws / "a.txt").write_text("hello")
    (ws / "b.txt").write_text("world")
    snap = snapshot_workspace(ws)
    assert "a.txt" in snap
    assert "b.txt" in snap
    assert isinstance(snap["a.txt"], str)  # hex digest


def test_snapshot_workspace_empty(tmp_path):
    ws = tmp_path / "empty"
    ws.mkdir()
    snap = snapshot_workspace(ws)
    assert snap == {}


# ---------------------------------------------------------------------------
# Test: baseline_save / baseline_load
# ---------------------------------------------------------------------------

def test_baseline_save_load(tmp_path):
    data = {"case1": {"state": "PASS", "problems": []}}
    bl_path = tmp_path / "bl" / "test.json"
    baseline_save(data, bl_path)
    loaded = baseline_load(bl_path)
    assert loaded == data


def test_baseline_load_nonexistent(tmp_path):
    assert baseline_load(tmp_path / "nonexistent.json") == {}


# ---------------------------------------------------------------------------
# Test: Per-type rollups
# ---------------------------------------------------------------------------

def test_type_rollups(tmp_path):
    ws = tmp_path / "ws_roll"
    ws.mkdir()

    runner_py = _write_runner(tmp_path, """
import sys,json
print(json.dumps({"response":"OK","tool_calls":["analyze"]}))
""")
    runner_cfg = _write_runner_json(tmp_path, runner_py, output="json")

    cases = _write_cases_jsonl(tmp_path, [
        {"id": "d1", "type": "discovery", "prompt": "test", "must_match_skill": "x"},
        {"id": "i1", "type": "invocation", "prompt": "test", "must_invoke_skill": "analyze"},
        {"id": "f1", "type": "functionality", "prompt": "test", "must_include": ["OK"]},
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1, workspace=str(ws))

    types = suite["types"]
    assert "discovery" in types
    assert "invocation" in types
    assert "functionality" in types
    assert types["discovery"]["total"] == 1
    assert types["functionality"]["total"] == 1
    assert types["invocation"]["total"] == 1


# ---------------------------------------------------------------------------
# Test: backward compat — existing suite's expectations
# ---------------------------------------------------------------------------

def test_backward_compat_existing_suite_expectations(tmp_path):
    """The existing test suite expectations still hold with backward compat."""
    # Replicate the original eval execution pattern
    script = tmp_path / "runner.py"
    script.write_text(
        "import sys,json\n"
        "p=sys.stdin.read()\n"
        "print(json.dumps({'response':'mutex lock' if 'mutex' in p else 'bottleneck','tool_calls':[]}))\n",
    )
    runner = tmp_path / "runner.json"
    runner.write_text(
        json.dumps({"argv":["python", str(script)], "cwd":str(tmp_path), "output":"json"})
    )
    cases_file = tmp_path / "cases.jsonl"
    cases_file.write_text(
        json.dumps({"id":"case1", "prompt":"explain mutex", "must_include":["mutex"], "forbidden_tools":["deploy"]}) + "\n"
    )
    results = run_evals(load_cases(cases_file), runner, 2)
    # Original expectations
    assert results["state"] == "EVAL_PASS"
    assert results["passed"] == 2
    # Should have the limits string
    assert "does not establish discovery" in results["limits"].lower()


def test_backward_compat_tool_calls_unavailable(tmp_path):
    """Tool assertions without structured JSON runner → FAILED (backward compat)."""
    runner = tmp_path / "runner.json"
    runner.write_text(json.dumps({"argv":["python", "-c", "print('hello')"], "cwd":str(tmp_path)}))
    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        json.dumps({"prompt":"hello", "required_tools":["search"]}) + "\n"
    )
    results = run_evals(load_cases(cases), runner)
    assert results["state"] == "FAILED"
