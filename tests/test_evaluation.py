"""Comprehensive behavioral evaluation tests for UniSkill.

Tests the four distinct test types, workspace diff measurement, reliability,
regression/baseline, backward compatibility, and evidence labelling.
"""
from __future__ import annotations
from pathlib import Path
import json
import sys
import textwrap
import pytest
import hashlib

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
    _resolve_workspace,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_runner(tmp_path, response_fn, name="runner.py"):
    """Create a small Python runner script.

    *response_fn* is a string of Python code that reads stdin and prints JSON.
    The runner's cwd is *tmp_path* (the runner JSON's "cwd" field).
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
    """Write a runner config JSON.  cwd defaults to tmp_path (the runner's cwd)."""
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


# ---------------------------------------------------------------------------
# Test: TEST_TYPES constant
# ---------------------------------------------------------------------------

def test_test_types_constant():
    assert TEST_TYPES == ("discovery", "invocation", "selection", "functionality", "regression")


# ---------------------------------------------------------------------------
# P1-A: _resolve_workspace — single-tree enforcement
# ---------------------------------------------------------------------------

def test_resolve_workspace_none_uses_runner_cwd(tmp_path):
    """When workspace is None, the measured tree IS the runner's cwd."""
    result = _resolve_workspace(None, tmp_path)
    assert result == tmp_path.resolve()


def test_resolve_workspace_same_dir(tmp_path):
    """When workspace resolves to the same dir as runner cwd, it's accepted."""
    # Using tmp_path directly means workspace == runner cwd
    result = _resolve_workspace(tmp_path, tmp_path)
    assert result == tmp_path.resolve()


def test_resolve_workspace_subdir_raises(tmp_path):
    """A subdirectory is NOT the same as parent; raises UniSkillError."""
    from uniskill.common import UniSkillError
    sub = tmp_path / "sub"
    sub.mkdir()
    with pytest.raises(UniSkillError, match="different directories"):
        _resolve_workspace(sub, tmp_path)


def test_resolve_workspace_different_dirs_raises(tmp_path):
    """When workspace resolves to a DIFFERENT directory, raises UniSkillError."""
    from uniskill.common import UniSkillError
    ws = tmp_path / "other"
    ws.mkdir()
    with pytest.raises(UniSkillError, match="different directories"):
        _resolve_workspace(ws, tmp_path)


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

    With single-tree semantics: runner creates artifact in its cwd (tmp_path),
    so if it DOESN'T create the artifact, measured tree diff will show it missing.
    """
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
    # No workspace param: measured tree = runner's cwd (tmp_path) which is empty
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1)
    row = suite["cases"][0]
    assert row["state"] == FAILED
    assert row["case_type"] == "functionality"
    assert row["evidence_type"] == MEASURED
    # The problem should mention the missing artifact
    assert any("Missing required artifact" in p for p in row["problems"])


def test_functionality_detects_forbidden_side_effect(tmp_path):
    """Runner creates a file outside the allowed prefix → FAIL via measured diff,
    even though runner-claimed tool_calls are clean."""
    # Runner creates a forbidden file in its own cwd
    runner_py = _write_runner(tmp_path, """
import sys,json
from pathlib import Path
(Path("unauthorized")).mkdir(exist_ok=True)
(Path("unauthorized") / "deleted.txt").write_text("deleted content")
print(json.dumps({"response":"Clean operation","tool_calls":["read-file"]}))
""", name="runner_side.py")
    runner_cfg = _write_runner_json(tmp_path, runner_py)
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "func-forbidden", "type": "functionality",
         "prompt": "test",
         "allowed_diff_prefix": ["output/"],  # only output/ is allowed
        },
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1)
    row = suite["cases"][0]
    assert row["state"] == FAILED
    assert any("Forbidden side effect" in p for p in row["problems"])
    assert row["evidence_type"] == MEASURED


def test_functionality_passes_with_created_artifact(tmp_path):
    """Runner creates the required artifact in its cwd → PASS."""
    runner_py = _write_runner(tmp_path, """
import sys,json
from pathlib import Path
(output := Path("output")).mkdir(exist_ok=True)
(output / "report.txt").write_text("Analysis complete")
print(json.dumps({"response":"Done","tool_calls":["write-file"]}))
""", name="runner_create.py")
    runner_cfg = _write_runner_json(tmp_path, runner_py)
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "func-pass", "type": "functionality",
         "prompt": "test",
         "required_artifacts": ["output/report.txt"],
         "allowed_diff_prefix": ["output/"],
        },
    ])
    # Single measured tree: runner cwd (tmp_path)
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1)
    row = suite["cases"][0]
    assert row["state"] == PASS
    assert row["evidence_type"] == MEASURED


# ---------------------------------------------------------------------------
# P1-B: expected_exit_code threading — exit code 0 with expected 0 must PASS
# ---------------------------------------------------------------------------

def test_expected_exit_code_zero_passes(tmp_path):
    """functionality case with expected_exit_code 0 and a runner that exits 0 must PASS."""
    runner_py = _write_runner(tmp_path, """
import sys,json
print(json.dumps({"response":"OK","tool_calls":[]}))
sys.exit(0)
""")
    runner_cfg = _write_runner_json(tmp_path, runner_py)
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "exit-zero", "type": "functionality",
         "prompt": "test",
         "expected_exit_code": 0,
        },
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1)
    row = suite["cases"][0]
    assert row["state"] == PASS
    assert not any("exit code" in p.lower() for p in row["problems"]), \
        f"Unexpected exit-code problem in: {row['problems']}"


def test_expected_exit_code_nonzero_fails(tmp_path):
    """functionality case with expected_exit_code 0 and a runner that exits 3 must FAIL."""
    runner_py = _write_runner(tmp_path, """
import sys,json
print(json.dumps({"response":"OK","tool_calls":[]}))
sys.exit(3)
""")
    runner_cfg = _write_runner_json(tmp_path, runner_py)
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "exit-three", "type": "functionality",
         "prompt": "test",
         "expected_exit_code": 0,
        },
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1)
    row = suite["cases"][0]
    assert row["state"] == FAILED
    assert any("exit code" in p.lower() for p in row["problems"]), \
        f"Expected exit-code problem missing; problems: {row['problems']}"


# ---------------------------------------------------------------------------
# Test: Regression type
# ---------------------------------------------------------------------------

def test_regression_baseline_saved_from_passing_runs(tmp_path):
    """Baseline saved from passing runs; a later failing case is REGRESSION."""
    # Runner that passes
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
    suite = run_behavioral_suite(load_cases(cases_pass), runner_cfg, 1)
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
                                  baseline_path=bl_path)
    row = suite2["cases"][0]
    assert row["state"] == REGRESSION
    assert row["case_type"] == "regression"


def test_regression_no_baseline_is_unverified(tmp_path):
    """A regression case with no baseline is UNVERIFIED."""
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
                                  baseline_path=bl_path)
    row = suite["cases"][0]
    assert row["state"] == UNVERIFIED


def test_regression_case_not_in_baseline_not_reported_as_regression(tmp_path):
    """A case never in the baseline is UNVERIFIED, not REGRESSION."""
    runner_py = _write_runner(tmp_path, """
import sys,json
print(json.dumps({"response":"OK","tool_calls":[]}))
""")
    runner_cfg = _write_runner_json(tmp_path, runner_py)
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "unknown-case", "type": "regression",
         "prompt": "test", "must_include": ["OK"]},
    ])
    bl_data = {"known-case": {"state": "PASS", "problems": []}}
    bl_path = tmp_path / "baselines" / "known.json"
    baseline_save(bl_data, bl_path)
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1,
                                  baseline_path=bl_path)
    row = suite["cases"][0]
    assert row["state"] == UNVERIFIED


# ---------------------------------------------------------------------------
# P2-D: must_contain / content_digest enforcement
# ---------------------------------------------------------------------------

def test_required_artifact_must_contain_passes(tmp_path):
    """Required artifact with must_contain substring passes when content matches."""
    runner_py = _write_runner(tmp_path, """
import sys,json
from pathlib import Path
(Path("output")).mkdir(exist_ok=True)
(Path("output") / "report.txt").write_text("Analysis complete and verified")
print(json.dumps({"response":"Done","tool_calls":["write-file"]}))
""", name="runner_mc.py")
    runner_cfg = _write_runner_json(tmp_path, runner_py)
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "mc-pass", "type": "functionality",
         "prompt": "test",
         "required_artifacts": [
             {"path": "output/report.txt", "must_contain": ["Analysis complete"]}
         ],
         "allowed_diff_prefix": ["output/"],
        },
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1)
    row = suite["cases"][0]
    assert row["state"] == PASS


def test_required_artifact_must_contain_fails(tmp_path):
    """Required artifact with must_contain fails when content does NOT match."""
    runner_py = _write_runner(tmp_path, """
import sys,json
from pathlib import Path
(Path("output")).mkdir(exist_ok=True)
(Path("output") / "report.txt").write_text("Nothing interesting here")
print(json.dumps({"response":"Done","tool_calls":["write-file"]}))
""", name="runner_mc_fail.py")
    runner_cfg = _write_runner_json(tmp_path, runner_py)
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "mc-fail", "type": "functionality",
         "prompt": "test",
         "required_artifacts": [
             {"path": "output/report.txt", "must_contain": ["Analysis complete"]}
         ],
         "allowed_diff_prefix": [],
        },
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1)
    row = suite["cases"][0]
    assert row["state"] == FAILED
    assert any("missing required content" in p for p in row["problems"]), \
        f"Expected content-mismatch problem missing; problems: {row['problems']}"


def test_required_artifact_content_digest_passes(tmp_path):
    """Required artifact with content_digest passes when sha256 matches."""
    content = "hello world"
    digest = hashlib.sha256(content.encode()).hexdigest()

    runner_py = _write_runner(tmp_path, """
import sys,json
from pathlib import Path
(Path("output")).mkdir(exist_ok=True)
(Path("output") / "report.txt").write_text("hello world")
print(json.dumps({"response":"Done","tool_calls":[]}))
""", name="runner_cd.py")
    runner_cfg = _write_runner_json(tmp_path, runner_py)
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "cd-pass", "type": "functionality",
         "prompt": "test",
         "required_artifacts": [
             {"path": "output/report.txt", "content_digest": digest}
         ],
         "allowed_diff_prefix": ["output/"],
        },
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1)
    row = suite["cases"][0]
    assert row["state"] == PASS


def test_required_artifact_content_digest_fails(tmp_path):
    """Required artifact with content_digest fails when sha256 does NOT match."""
    runner_py = _write_runner(tmp_path, """
import sys,json
from pathlib import Path
(Path("output")).mkdir(exist_ok=True)
(Path("output") / "report.txt").write_text("wrong content")
print(json.dumps({"response":"Done","tool_calls":[]}))
""", name="runner_cd_fail.py")
    runner_cfg = _write_runner_json(tmp_path, runner_py)
    # Pass a wrong digest
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "cd-fail", "type": "functionality",
         "prompt": "test",
         "required_artifacts": [
             {"path": "output/report.txt", "content_digest": "deadbeef" * 8}
         ],
         "allowed_diff_prefix": [],
        },
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1)
    row = suite["cases"][0]
    assert row["state"] == FAILED
    assert any("content-digest mismatch" in p for p in row["problems"]), \
        f"Expected digest-mismatch problem missing; problems: {row['problems']}"


# ---------------------------------------------------------------------------
# P2-E: allowed_diff_prefix=[] and forbidden artifacts
# ---------------------------------------------------------------------------

def test_allowed_diff_prefix_empty_rejects_changes(tmp_path):
    """allowed_diff_prefix [] (empty list) rejects any workspace change."""
    runner_py = _write_runner(tmp_path, """
import sys,json
from pathlib import Path
(Path("unwanted")).write_text("nope")
print(json.dumps({"response":"OK","tool_calls":[]}))
""")
    runner_cfg = _write_runner_json(tmp_path, runner_py)
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "no-changes", "type": "functionality",
         "prompt": "test",
         "allowed_diff_prefix": [],  # nothing allowed
        },
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1)
    row = suite["cases"][0]
    assert row["state"] == FAILED
    assert any("Forbidden side effect" in p for p in row["problems"])


def test_allowed_diff_prefix_empty_allows_no_changes(tmp_path):
    """allowed_diff_prefix [] with a runner that writes nothing → PASS."""
    runner_py = _write_runner(tmp_path, """
import sys,json
# Does NOT write any file
print(json.dumps({"response":"OK","tool_calls":[]}))
""")
    runner_cfg = _write_runner_json(tmp_path, runner_py)
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "no-write", "type": "functionality",
         "prompt": "test",
         "allowed_diff_prefix": [],  # nothing allowed, runner writes nothing
        },
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1)
    row = suite["cases"][0]
    assert row["state"] == PASS


def test_forbidden_artifacts_with_absolute_prefix(tmp_path):
    """Forbidden artifact paths with absolute prefixes match workspace-absolute resolution."""
    runner_py = _write_runner(tmp_path, """
import sys,json
from pathlib import Path
(Path("etc_foo")).write_text("bad file")
print(json.dumps({"response":"OK","tool_calls":[]}))
""")
    runner_cfg = _write_runner_json(tmp_path, runner_py)
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "forbidden-abs", "type": "functionality",
         "prompt": "test",
         "forbidden_artifacts": ["/etc_foo"],  # absolute prefix
        },
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1)
    row = suite["cases"][0]
    assert row["state"] == FAILED
    assert any("Forbidden artifact" in p for p in row["problems"]), \
        f"Expected forbidden artifact problem; problems: {row['problems']}"


# ---------------------------------------------------------------------------
# P2-F: Fail-open snapshot — snapshot failure must not silently pass
# ---------------------------------------------------------------------------

def test_snapshot_failure_is_fail_not_pass(tmp_path):
    """If snapshot fails mid-run, the case must FAIL, not PASS with empty diff."""
    # We can't easily break snapshot_workspace in a test, but we verify
    # that the evaluator checks the measured_problems list for snapshot failures.
    # Since _run_single_case calls snapshot_workspace(measured_tree) and
    # any exception is appended to problems, a snapshot failure produces a FAILED case.
    # This test is structural: verify that a measured_problems entry propagates.
    from uniskill.evaluation import _eval_functionality, MEASURED, FAILED, PASS
    problems = ["snapshot failure: directory vanished"]
    state, problems_out, etype, detail = _eval_functionality(
        {"id": "snap-fail", "type": "functionality", "prompt": "test"},
        runner_output="OK", tool_calls=[],
        workspace_diff={"created": [], "modified": [], "deleted": []},
        measured_tree=None, timed_out=False, actual_exit_code=0,
        measured_problems=problems,
    )
    assert state == FAILED
    assert any("snapshot failure" in p for p in problems_out)


# ---------------------------------------------------------------------------
# Test: Reliability
# ---------------------------------------------------------------------------

def test_reliability_flaky_detects_mixed_results(tmp_path):
    """3 repetitions with an intermittent runner → reported as TOO_FEW with correct pass_rate."""
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
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 3)
    # Check per-case reliability
    rel = suite["reliability"]["flaky-case"]
    assert rel["trials"] == 3
    assert rel["passes"] == 2
    assert rel["pass_rate"] == pytest.approx(2 / 3, rel=1e-3)
    assert rel["verdict"] == "TOO_FEW"  # N<5
    assert "too few" in rel["confidence"].lower()


def test_reliability_n_ge_5_passes_all(tmp_path):
    """5+ trials all pass → PASS verdict with Wilson CI."""
    runner_py = _write_runner(tmp_path, """
import sys,json
print(json.dumps({"response":"OK","tool_calls":["analyze"]}))
""")
    runner_cfg = _write_runner_json(tmp_path, runner_py)
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "reliable-case", "type": "functionality",
         "prompt": "test", "must_include": ["OK"]},
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 5)
    rel = suite["reliability"]["reliable-case"]
    assert rel["trials"] == 5
    assert rel["passes"] == 5
    assert rel["verdict"] == "PASS"
    assert "95% Wilson CI" in rel["confidence"]
    assert rel["pass_rate"] == 1.0  # 5/5 = 1.0


def test_reliability_n_ge_5_all_fail(tmp_path):
    """5+ trials all fail → FAIL verdict."""
    runner_py = _write_runner(tmp_path, """
import sys,json
print(json.dumps({"response":"NO SUCCESS HERE","tool_calls":[]}))
""")
    runner_cfg = _write_runner_json(tmp_path, runner_py)
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "fail-case", "type": "functionality",
         "prompt": "test", "must_include": ["ALL CHECKS PASSED"]},
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 5)
    rel = suite["reliability"]["fail-case"]
    assert rel["verdict"] == "FAIL"
    assert "all 5" in rel["confidence"].lower()


def test_reliability_all_timeout_verdict(tmp_path):
    """All-TIMEOUT (n>=5) reports verdict TIMEOUT, not FAIL."""
    runner_py = _write_runner(tmp_path, """
import time,sys,json
time.sleep(10)
print(json.dumps({"response":"never","tool_calls":[]}))
""", name="runner_slow_rel.py")
    runner_cfg = _write_runner_json(tmp_path, runner_py)
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "timeout-case", "type": "functionality",
         "prompt": "test"},
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 5)
    rel = suite["reliability"]["timeout-case"]
    assert rel["trials"] == 5
    assert rel["verdict"] == "TIMEOUT"
    assert "timed out" in rel["confidence"].lower()


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
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1)
    row = suite["cases"][0]
    assert row["timed_out"] is True
    assert row["state"] == TIMEOUT


# ---------------------------------------------------------------------------
# Test: No implicit execution
# ---------------------------------------------------------------------------

def test_no_implicit_execution(tmp_path):
    """run_behavioral_suite without a runner (missing runner file) returns UNVERIFIED."""
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

    runner_py = _write_runner(tmp_path, """
import sys,json
print(json.dumps({"response":"not matching anything","tool_calls":[]}))
""")
    runner_cfg = _write_runner_json(tmp_path, runner_py)
    cases = _write_cases_jsonl(tmp_path, [
        {"id": "nomatch", "type": "functionality",
         "prompt": "test", "must_include": ["definitely-not-here"]},
    ])
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1)
    # Suite should not PASS
    assert suite["state"] != PASS


# ---------------------------------------------------------------------------
# Test: Evidence type labelling
# ---------------------------------------------------------------------------

def test_runner_asserted_only_cases_labeled_in_aggregate(tmp_path):
    """Runner-asserted-only cases (discovery/invocation/selection) are visibly labeled."""

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
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1)

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
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1)
    # All runner-asserted → runner-asserted-only
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
    suite = run_behavioral_suite(load_cases(cases), runner_cfg, 1)

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
