"""Adversarial corpus: replays every attack that ever found a real defect.

Each test here corresponds to a live attack from a review round or an
orchestrator probe that FAILED at the time it was found. They stay in the
suite so a regression cannot silently re-land. This is the dynamic
counterpart to the code reviews: reviews discover, this file remembers.

All attacks run through the real CLI (main()) where the defect was
user-facing, because a library-level pass is not what users execute.
"""
import json
import sys
from pathlib import Path

import pytest

from uniskill.cli import main


def _workspace(tmp_path, name="measured"):
    ws = tmp_path / name
    ws.mkdir()
    return ws


def _runner(tmp_path, ws, script_body, name="runner"):
    script = tmp_path / f"{name}.py"
    script.write_text(script_body)
    cfg = tmp_path / f"{name}.json"
    cfg.write_text(json.dumps({
        "argv": [sys.executable, str(script)],
        "mode": "stdin", "cwd": str(ws), "timeout_seconds": 30}))
    return cfg


def _evaluate(capsys, cases_file, runner_cfg, ws=None):
    argv = ["evaluate", str(cases_file), "--runner-config", str(runner_cfg),
            "--execute"]
    if ws is not None:
        argv += ["--workspace", str(ws)]
    assert main(argv) == 2  # failing evaluate exits non-zero
    return json.loads(capsys.readouterr().out)


def _evaluate_ok(capsys, cases_file, runner_cfg, ws=None):
    argv = ["evaluate", str(cases_file), "--runner-config", str(runner_cfg),
            "--execute"]
    if ws is not None:
        argv += ["--workspace", str(ws)]
    assert main(argv) == 0
    return json.loads(capsys.readouterr().out)


def _first_case(out):
    cases = out["cases"]
    return cases[0] if isinstance(cases, list) else list(cases.values())[0]


LYING_RUNNER = (
    'import json\n'
    'print(json.dumps({"response": "Artifact created successfully", '
    '"tool_calls": [{"name": "fs.write"}]}))\n')

HONEST_RUNNER = (
    'import json, os\n'
    'os.makedirs("output", exist_ok=True)\n'
    'open("output/report.txt", "w").write("done")\n'
    'print(json.dumps({"response": "Artifact created successfully", '
    '"tool_calls": [{"name": "fs.write"}]}))\n')


def func_case(required=None, allowed=None, forbidden=None, exit_code=None):
    case = {"id": "attack", "type": "functionality", "prompt": "do the thing"}
    if required is not None:
        case["required_artifacts"] = required
    if allowed is not None:
        case["allowed_diff_prefix"] = allowed
    if forbidden is not None:
        case["forbidden_artifacts"] = forbidden
    if exit_code is not None:
        case["expected_exit_code"] = exit_code
    return case


def corpus_cases(tmp_path, case):
    f = tmp_path / "cases.jsonl"
    f.write_text(json.dumps(case) + "\n")
    return f


def test_lying_runner_fails_via_measured_diff(tmp_path, capsys):
    """Attack (review round 1): prose claims success, creates nothing.
    Was EVAL_PASS 2/2 through the documented command; must FAIL."""
    ws = _workspace(tmp_path)
    cfg = _runner(tmp_path, ws, LYING_RUNNER, "lying")
    cases = corpus_cases(tmp_path, func_case(
        required=["output/report.txt"], allowed=["output/"]))
    out = _evaluate(capsys, cases, cfg, ws)
    assert out["state"] == "FAILED"
    assert _first_case(out)["state"] == "FAILED"


def test_honest_runner_passes_on_same_tree(tmp_path, capsys):
    """Counterpart: the artifact actually created in the measured tree
    must pass — pins the two-tree diff bug from review round 1."""
    ws = _workspace(tmp_path)
    cfg = _runner(tmp_path, ws, HONEST_RUNNER, "honest")
    cases = corpus_cases(tmp_path, func_case(
        required=["output/report.txt"], allowed=["output/"]))
    out = _evaluate_ok(capsys, cases, cfg, ws)
    assert out["state"] == "PASS"
    assert out.get("verification_class") == "measured-diff"


def test_workspace_cwd_mismatch_is_refused(tmp_path, capsys):
    """Attack (review round 1): before/after snapshots of two different
    trees. The evaluator must refuse via the CLI error path (rc=2, ERROR on
    stderr), never silently measure the wrong one."""
    ws = _workspace(tmp_path, "other")
    cfg = _runner(tmp_path, tmp_path, HONEST_RUNNER, "honest2")  # cwd = tmp_path
    cases = corpus_cases(tmp_path, func_case(
        required=["output/report.txt"], allowed=["output/"]))
    rc = main(["evaluate", str(cases), "--runner-config", str(cfg),
               "--workspace", str(ws), "--execute"])
    assert rc == 2
    err = json.loads(capsys.readouterr().err)
    assert err["state"] == "ERROR"
    assert "different directories" in err["error"]


def test_expected_exit_code_zero_with_exit_zero_passes(tmp_path, capsys):
    """Attack (review round 1): expected 0 + exit 0 failed as
    'Unexpected exit code: N/A'. Must PASS."""
    ws = _workspace(tmp_path)
    cfg = _runner(tmp_path, ws, 'import json\nprint(json.dumps({"response": "ok"}))\n',
                  "exit0")
    cases = corpus_cases(tmp_path, func_case(allowed=[], exit_code=0))
    out = _evaluate_ok(capsys, cases, cfg, ws)
    assert out["state"] == "PASS"


def test_expected_exit_code_zero_with_exit_three_fails_once(tmp_path, capsys):
    """Attack (review round 2, N2): exit 3 must fail AND the problem must
    appear exactly once (deduplicated)."""
    ws = _workspace(tmp_path)
    cfg = _runner(tmp_path, ws,
                  'import sys, json\nsys.exit(3)\n', "exit3")
    cases = corpus_cases(tmp_path, func_case(allowed=[], exit_code=0))
    out = _evaluate(capsys, cases, cfg, ws)
    row = _first_case(out)
    assert row["state"] == "FAILED"
    exit_problems = [p for p in row.get("problems", [])
                     if "Unexpected exit code" in str(p)]
    assert len(exit_problems) == 1


def test_absolute_forbidden_prefixes_do_not_flag_benign_runs(tmp_path, capsys):
    """Attack (review round 2, N1): the absolute-match branch was a
    tautology — ["/etc/", "/root/"] flagged a runner that only wrote
    output/report.txt. Benign runs must pass."""
    ws = _workspace(tmp_path)
    cfg = _runner(tmp_path, ws, HONEST_RUNNER, "honest3")
    cases = corpus_cases(tmp_path, func_case(
        required=["output/report.txt"], allowed=["output/"],
        forbidden=["/etc/", "/root/"]))
    out = _evaluate_ok(capsys, cases, cfg, ws)
    assert out["state"] == "PASS"


def test_absolute_forbidden_prefix_catches_real_violation(tmp_path, capsys):
    """The tautology fix must still catch a file created under a real
    forbidden prefix INSIDE the measured tree (diff evidence is scoped to
    the measured tree; writes outside it are invisible to any diff)."""
    ws = _workspace(tmp_path)
    forbidden_dir = ws / "forbidden_zone"
    violator = (
        'import json, os\n'
        'os.makedirs("forbidden_zone", exist_ok=True)\n'
        'open(os.path.join("forbidden_zone", "leaked.txt"), "w").write("x")\n'
        'print(json.dumps({"response": "ok"}))\n')
    cfg = _runner(tmp_path, ws, violator, "violator")
    cases = corpus_cases(tmp_path, func_case(
        allowed=["output/"], forbidden=[str(forbidden_dir)]))
    out = _evaluate(capsys, cases, cfg, ws)
    assert out["state"] == "FAILED"


def test_empty_allowed_prefix_means_no_changes(tmp_path, capsys):
    """Attack (review round 1, P2-5): [] was falsy-skipped, so 'no changes
    allowed' was inexpressible."""
    ws = _workspace(tmp_path)
    cfg = _runner(tmp_path, ws, HONEST_RUNNER, "writer")
    cases = corpus_cases(tmp_path, func_case(allowed=[]))
    out = _evaluate(capsys, cases, cfg, ws)
    assert out["state"] == "FAILED"
    ws2 = _workspace(tmp_path, "quiet")
    cfg2 = _runner(tmp_path, ws2,
                   'import json\nprint(json.dumps({"response": "ok"}))\n',
                   "quiet_runner")
    out2 = _evaluate_ok(capsys, cases, cfg2, ws2)
    assert out2["state"] == "PASS"


def test_missing_runner_binary_fails_one_case_not_the_suite(tmp_path, capsys):
    """Attack (review round 2, N4): OSError on runner launch used to abort
    the whole suite, losing every prior result. Now: per-case FAILED,
    siblings still reported."""
    ws = _workspace(tmp_path)
    cfg = tmp_path / "missing.json"
    cfg.write_text(json.dumps({
        "argv": [sys.executable, "/nonexistent/binary/xyz.py"],
        "mode": "stdin", "cwd": str(ws), "timeout_seconds": 30}))
    f = tmp_path / "cases.jsonl"
    f.write_text(json.dumps(func_case(allowed=[], exit_code=None), ) + "\n" +
                 json.dumps({"id": "second", "type": "discovery",
                             "prompt": "p", "must_match_skill": "x"}) + "\n")
    out = _evaluate(capsys, f, cfg, ws)
    assert out["state"] == "FAILED"
    rows = out["cases"] if isinstance(out["cases"], list) else list(out["cases"].values())
    assert len(rows) == 2  # both cases have rows; nothing was lost


def test_runner_answered_discovery_is_labeled_runner_asserted(tmp_path, capsys):
    """Honesty pin for discovery semantics (prose can never prove discovery):
    - a runner that answers but never mentions the skill FAILS the case
      (contradicting evidence — the only source we have says no);
    - a runner whose prose mentions the skill is UNVERIFIED, never PASS:
      consistent, but a mention in prose is not proof the harness discovered
      anything. UNVERIFIED is the honest ceiling for probeless discovery."""
    ws = _workspace(tmp_path)
    f = tmp_path / "cases.jsonl"
    f.write_text(json.dumps({"id": "d1", "type": "discovery", "prompt": "p",
                             "must_match_skill": "some-skill"}) + "\n")

    cfg = _runner(tmp_path, ws,
                  'import json\nprint(json.dumps({"response": "skill ran"}))\n',
                  "silent")
    out = _evaluate(capsys, f, cfg, ws)
    assert _first_case(out)["state"] == "FAILED"

    cfg2 = _runner(tmp_path, ws,
                   'import json\nprint(json.dumps('
                   '{"response": "using some-skill now"}))\n',
                   "mentioner")
    argv = ["evaluate", str(f), "--runner-config", str(cfg2), "--execute",
            "--workspace", str(ws)]
    assert main(argv) == 0  # UNVERIFIED is not a failure
    out2 = json.loads(capsys.readouterr().out)
    row2 = _first_case(out2)
    assert row2["state"] == "UNVERIFIED"
    assert row2["evidence_type"] == "runner-asserted"
    assert "No harness probe" in str(row2.get("evidence_detail"))
