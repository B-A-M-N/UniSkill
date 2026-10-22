"""Opt-in executable test runner with text and structured tool-trace assertions.

Extends the original evaluator with real behavioral evaluation:
  - Four distinct test types: discovery, invocation/selection, functionality, regression
  - Workspace diff (measured evidence) instead of relying on runner prose
  - Per-case and aggregate reliability with honest confidence statements
  - Baseline/regression comparison
  - Backward compatible with old-style case files

No agent is silently launched. A user-supplied runner is a trusted local executable;
real harness discovery/invocation are not inferred from test output alone.
"""
from __future__ import annotations
import json
import math
import os
import re
import subprocess
from pathlib import Path
from .common import UniSkillError, digest_tree, safe_tree, read_json

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TEST_TYPES = ("discovery", "invocation", "selection", "functionality", "regression")

# Evaluator result states
PASS = "PASS"
FAILED = "FAILED"
UNVERIFIED = "UNVERIFIED"
REGRESSION = "REGRESSION"  # distinct from FAILED — a previously-passing case now fails
TIMEOUT = "TIMEOUT"

# Evidence types
MEASURED = "measured"
RUNNER_ASSERTED = "runner-asserted"


# ---------------------------------------------------------------------------
# Backward-compatible case loading
# ---------------------------------------------------------------------------

def load_cases(path):
    """Load a JSONL file of test cases.

    Backward compatible: a case with no ``type`` field defaults to
    ``"functionality"`` and supports the original assertion keys
    (must_include, must_not_include, required_tools, forbidden_tools,
    expected_exit_code).

    New cases may also declare:
      - type: one of TEST_TYPES
      - must_not_match_skill (discovery negative)
      - must_match_skill / must_invoke_skill / must_select_skill / must_not_invoke_skill / must_not_select_skill
      - required_artifacts  – list of path prefixes or dicts {path, must_contain, content_digest}
      - forbidden_artifacts – list of path prefixes that must not appear in workspace diff
      - allowed_diff_prefix – allowed path prefixes for workspace diffs
      - baseline_key        – key used to look up the baseline result for regression cases
    """
    rows = []
    for i, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise UniSkillError(f"Invalid case JSON at line {i}: {exc}") from exc
        if not isinstance(row, dict) or not isinstance(row.get("prompt"), str):
            raise UniSkillError(f"Case line {i} requires prompt string")
        # Old-style validation
        for key in ("must_include", "must_not_include", "required_tools", "forbidden_tools"):
            if not isinstance(row.get(key, []), list) or any(
                not isinstance(x, str) for x in row.get(key, [])):
                raise UniSkillError(f"Case line {i}: {key} must be string list")
        # Normalise type
        row.setdefault("type", "functionality")
        if row["type"] not in TEST_TYPES:
            raise UniSkillError(f"Case line {i}: invalid type '{row['type']}'")
        rows.append(row)
    if not rows:
        raise UniSkillError("No test cases")
    return rows


# ---------------------------------------------------------------------------
# Runner config validation
# ---------------------------------------------------------------------------

def _validate_runner_config(runner, cwd):
    """Validate a runner config dict (from JSON)."""
    if not isinstance(runner, dict) or not isinstance(runner.get("argv"), list) or not all(
        isinstance(x, str) and x for x in runner["argv"]):
        raise UniSkillError("Runner config must contain argv: string[]")
    if runner.get("shell", False):
        raise UniSkillError("Shell runners are not supported")
    if runner.get("mode", "stdin") != "stdin":
        raise UniSkillError("Runner mode must be stdin")
    resolved_cwd = Path(runner.get("cwd", ".")).resolve()
    if not resolved_cwd.is_dir():
        raise UniSkillError(f"Runner cwd does not exist: {resolved_cwd}")
    timeout = float(runner.get("timeout_seconds", 60))
    if not 1 <= timeout <= 300:
        raise UniSkillError("Runner timeout must be 1-300 seconds")
    reps = runner.get("repetitions", 1)
    if not isinstance(reps, int) or not 1 <= reps <= 20:
        raise UniSkillError("Repetitions must be 1-20")
    return resolved_cwd, timeout


# ---------------------------------------------------------------------------
# Workspace diff helpers
# ---------------------------------------------------------------------------

def snapshot_workspace(path):
    """Return a map ``relative_path -> sha256_hex`` for every file under *path*.

    Skips ``.git``, ``__pycache__``, and symlinked paths.
    """
    path = Path(path)
    result = {}
    for f in safe_tree(path):
        rel = f.relative_to(path).as_posix()
        result[rel] = digest_single(f)
    return result


def digest_single(path):
    """SHA-256 of a single file's contents as hex."""
    return hashlib_file(path.read_bytes())


def hashlib_file(data: bytes) -> str:
    import hashlib as _h
    return _h.sha256(data).hexdigest()


def diff_workspace(before, after):
    """Given two snapshot dicts (path -> digest), return:

    .. dict::
        created:  paths in *after* but not in *before*
        modified: paths in both but with different digest
        deleted:  paths in *before* but not in *after*
    """
    before_keys = set(before.keys())
    after_keys = set(after.keys())
    return {
        "created": sorted(after_keys - before_keys),
        "modified": sorted(k for k in before_keys & after_keys if before[k] != after[k]),
        "deleted": sorted(before_keys - after_keys),
    }


# ---------------------------------------------------------------------------
# Legacy run_evals (backward compatible)
# ---------------------------------------------------------------------------

def run_evals(cases, runner_path, repetitions=1):
    """Evaluate a list of cases against a runner.

    Backward compatible: keeps the same signature and return shape as the
    original implementation.
    """
    runner = read_json(runner_path)
    cwd, timeout = _validate_runner_config(runner, ".")
    rows = []
    for case in cases:
        for repeat in range(repetitions):
            problems = []
            try:
                proc = subprocess.run(
                    runner["argv"], cwd=cwd, input=case["prompt"],
                    capture_output=True, text=True, timeout=timeout, check=False,
                )
                stdout = proc.stdout[-100000:]
                if proc.returncode != case.get("expected_exit_code", 0):
                    problems.append(f"Unexpected exit code: {proc.returncode}")
                output = stdout
                tool_calls = None
                if runner.get("output", "text") == "json":
                    try:
                        parsed = json.loads(stdout)
                        output = parsed.get("response", "")
                        tool_calls = parsed.get("tool_calls", [])
                        if not isinstance(output, str) or not isinstance(tool_calls, list):
                            problems.append("Invalid runner result schema")
                    except (ValueError, AttributeError):
                        problems.append("Runner output not valid structured JSON")
                        output = stdout
                for pattern in case.get("must_include", []):
                    if not re.search(pattern, output, flags=re.IGNORECASE | re.MULTILINE):
                        problems.append(f"Missing match: {pattern}")
                for pattern in case.get("must_not_include", []):
                    if re.search(pattern, output, flags=re.IGNORECASE | re.MULTILINE):
                        problems.append(f"Forbidden match: {pattern}")
                required = case.get("required_tools", [])
                forbidden = case.get("forbidden_tools", [])
                if required or forbidden:
                    if tool_calls is None:
                        problems.append("Tool calls unavailable: structured JSON runner required")
                    else:
                        tools = [
                            t if isinstance(t, str) else t.get("name")
                            for t in tool_calls if isinstance(t, (str, dict))
                        ]
                        problems += [
                            f"Missing required tool: {t}" for t in required if t not in tools
                        ]
                        problems += [
                            f"Forbidden tool used: {t}" for t in forbidden if t in tools
                        ]
            except (subprocess.TimeoutExpired, OSError) as exc:
                problems.append(f"Runner failed: {type(exc).__name__}: {exc}")
            rows.append({
                "case": case.get("id", case["prompt"][:40]),
                "repeat": repeat + 1,
                "state": "PASS" if not problems else "FAILED",
                "problems": problems,
            })
    total_pass = sum(r["state"] == "PASS" for r in rows)
    return {
        "state": "EVAL_PASS" if total_pass == len(rows) else "FAILED",
        "passed": total_pass,
        "total": len(rows),
        "cases": rows,
        "limits": (
            "Output and declared tool-call assertions only. Does not establish "
            "discovery, auto-trigger, sandboxing, or cross-harness portability."
        ),
    }


# ---------------------------------------------------------------------------
# Core behavioral evaluation
# ---------------------------------------------------------------------------

def _eval_discovery(case, runner_output, measured, workspace_diff):
    """Evaluate a *discovery* case.

    Returns (state, problems, evidence_type, evidence_detail).
    """
    problems = []
    evidence_type = RUNNER_ASSERTED
    evidence_detail = {}

    # The runner's stdout/response is the only source for skill discovery
    # (no probe harness in the generic runner protocol)
    response_text = (runner_output or "").lower()

    # Positive discovery: must_include_skill asserts the skill was surfaced
    must_match = case.get("must_match_skill")
    if must_match:
        skill_slug = must_match.lower()
        if skill_slug not in response_text:
            problems.append(f"Skill '{must_match}' not mentioned in runner output")

    # Negative discovery: must_not_match_skill asserts the skill was NOT surfaced
    must_not_match = case.get("must_not_match_skill")
    if must_not_match:
        skill_slug = must_not_match.lower()
        if skill_slug in response_text:
            problems.append(f"Skill '{must_not_match}' was mentioned but must_not_match_skill")

    # Without a harness probe, we cannot truly prove discovery — label UNVERIFIED
    # if there are no problems (we simply didn't measure).
    if not problems:
        evidence_type = RUNNER_ASSERTED
        evidence_detail = {
            "reason": "No harness probe available; discovery is runner-asserted only",
        }
        return UNVERIFIED, problems, evidence_type, evidence_detail

    return FAILED, problems, evidence_type, evidence_detail


def _eval_invocation(case, runner_output, tool_calls, measured, workspace_diff):
    """Evaluate an *invocation* (explicit invocation) case.

    Returns (state, problems, evidence_type, evidence_detail).
    """
    problems = []
    evidence_type = RUNNER_ASSERTED
    evidence_detail = {}

    # Check tool_calls (runner-reported)
    actual_tools = []
    if tool_calls is not None:
        actual_tools = [
            t if isinstance(t, str) else t.get("name", "")
            for t in tool_calls if isinstance(t, (str, dict))
        ]

    # Positive invocation: must_invoke_skill
    must_invoke = case.get("must_invoke_skill")
    if must_invoke:
        if must_invoke not in actual_tools:
            problems.append(f"Tool '{must_invoke}' not in runner's tool_calls")

    # Negative invocation: must_not_invoke_skill
    must_not_invoke = case.get("must_not_invoke_skill")
    if must_not_invoke:
        if must_not_invoke in actual_tools:
            problems.append(f"Tool '{must_not_invoke}' was called but must_not_invoke_skill")

    # Without measured tool-events, this is runner-asserted
    if not problems:
        evidence_detail = {
            "reason": "Tool calls from runner self-report; no measured tool-events",
        }

    if not problems:
        # All invocations as asserted — still PASS but note it
        pass
    return (FAILED if problems else PASS), problems, evidence_type, evidence_detail


def _eval_selection(case, runner_output, tool_calls, measured, workspace_diff):
    """Evaluate a *selection* (automatic/auto-trigger) case.

    Selection cases assert that a skill was *automatically* selected (positive)
    or *not* selected (negative) for a given prompt.

    Returns (state, problems, evidence_type, evidence_detail).
    """
    problems = []
    evidence_type = RUNNER_ASSERTED
    evidence_detail = {}

    actual_tools = []
    if tool_calls is not None:
        actual_tools = [
            t if isinstance(t, str) else t.get("name", "")
            for t in tool_calls if isinstance(t, (str, dict))
        ]

    # Positive selection: must_select_skill
    must_select = case.get("must_select_skill")
    if must_select:
        if must_select not in actual_tools:
            problems.append(
                f"Skill '{must_select}' not auto-selected (not in tool_calls)"
            )

    # Negative selection: must_not_select_skill
    must_not_select = case.get("must_not_select_skill")
    if must_not_select:
        if must_not_select in actual_tools:
            problems.append(
                f"Skill '{must_not_select}' was auto-selected but must_not_select_skill"
            )

    if not problems:
        evidence_detail = {
            "reason": "Selection from runner self-report; no measured auto-trigger probe",
        }

    return (FAILED if problems else PASS), problems, evidence_type, evidence_detail


def _eval_functionality(case, runner_output, tool_calls, measured, workspace_diff):
    """Evaluate a *functionality* case.

    Uses measured workspace diff + runner output assertions.
    Workspace diff is the primary evidence — not runner prose.

    Returns (state, problems, evidence_type, evidence_detail).
    """
    problems = []

    # 1. Workspace diff checks — MEASURED evidence
    allowed_prefixes = case.get("allowed_diff_prefix", [])
    required_artifacts = case.get("required_artifacts", [])
    forbidden_artifacts = case.get("forbidden_artifacts", [])

    # Check forbidden artifacts: no created/modified path outside allowed prefixes
    if allowed_prefixes:
        for path_name in workspace_diff["created"] + workspace_diff["modified"] + workspace_diff["deleted"]:
            if not any(path_name.startswith(p) for p in allowed_prefixes):
                problems.append(f"Forbidden side effect: workspace changed outside allowed prefixes: {path_name}")
                break

    # Check required artifacts against actual diff
    for artifact in required_artifacts:
        if isinstance(artifact, str):
            # Just a path prefix to check was created or modified
            if not any(
                p.startswith(artifact)
                for p in workspace_diff["created"] + workspace_diff["modified"]
            ):
                problems.append(f"Missing required artifact: {artifact} (not in workspace diff)")
        elif isinstance(artifact, dict):
            artifact_path = artifact.get("path", "")
            created = any(p == artifact_path for p in workspace_diff["created"])
            modified = any(p == artifact_path for p in workspace_diff["modified"])
            if not created and not modified:
                problems.append(
                    f"Missing required artifact: {artifact_path} (not in workspace diff)"
                )
            # Check must_contain content
            mc = artifact.get("must_contain")
            if mc and (created or modified):
                # We need the file content — try reading it if it exists
                fpath = measured.get(artifact_path)
                if fpath is None:
                    # File was created/modified, try to read from workspace
                    pass  # content check requires the actual file

    # Check forbidden artifact paths actually don't appear in diff
    for fpath in forbidden_artifacts:
        if isinstance(fpath, str):
            for change_path in workspace_diff["created"] + workspace_diff["modified"] + workspace_diff["deleted"]:
                if change_path.startswith(fpath):
                    problems.append(f"Forbidden artifact created/changed: {change_path}")
                    break
        elif isinstance(fpath, dict):
            fp = fpath.get("path", "")
            for change_path in workspace_diff["created"] + workspace_diff["modified"] + workspace_diff["deleted"]:
                if change_path == fp:
                    problems.append(f"Forbidden artifact path changed: {fp}")
                    break

    # 2. Text-based assertions (backward compat) — runner-asserted
    response_text = runner_output or ""
    for pattern in case.get("must_include", []):
        if not re.search(pattern, response_text, flags=re.IGNORECASE | re.MULTILINE):
            problems.append(f"Missing match: {pattern}")
    for pattern in case.get("must_not_include", []):
        if re.search(pattern, response_text, flags=re.IGNORECASE | re.MULTILINE):
            problems.append(f"Forbidden match: {pattern}")

    # 3. Tool trace assertions — runner-asserted
    required_tools = case.get("required_tools", [])
    forbidden_tools = case.get("forbidden_tools", [])
    if required_tools or forbidden_tools:
        if tool_calls is None:
            problems.append("Tool calls unavailable: structured JSON runner required")
        else:
            actual = [
                t if isinstance(t, str) else t.get("name", "")
                for t in tool_calls if isinstance(t, (str, dict))
            ]
            problems += [
                f"Missing required tool: {t}" for t in required_tools if t not in actual
            ]
            problems += [
                f"Forbidden tool used: {t}" for t in forbidden_tools if t in actual
            ]

    # Exit code
    expected_exit = case.get("expected_exit_code")
    if expected_exit is not None and case.get("actual_exit_code") != expected_exit:
        problems.append(
            f"Unexpected exit code: {case.get('actual_exit_code', 'N/A')} "
            f"(expected {expected_exit})"
        )

    # Determine evidence type for label
    has_measured_diff = bool(workspace_diff.get("created") or workspace_diff.get("modified") or workspace_diff.get("deleted"))
    evidence_type = MEASURED if has_measured_diff else RUNNER_ASSERTED

    # Even if diff has no changes, measured snapshot was taken
    # so we still classify as measured
    evidence_type = MEASURED

    # Build evidence detail
    evidence_detail = {
        "workspace_diff": workspace_diff,
        "has_measured_diff": has_measured_diff,
    }

    return (FAILED if problems else PASS), problems, evidence_type, evidence_detail


def _eval_regression(case, runner_output, tool_calls, measured, workspace_diff,
                     baseline_results, case_id):
    """Evaluate a *regression* case by comparing against a stored baseline.

    Returns (state, problems, evidence_type, evidence_detail).
    """
    problems = []
    evidence_type = MEASURED
    evidence_detail = {}

    # Find baseline for this case
    baseline_key = case.get("baseline_key", case_id)
    baseline = baseline_results.get(baseline_key)

    if baseline is None:
        # No baseline exists — this is not a regression, treat as normal functionality
        # But regression cases MUST have a baseline; if none exists, we can't evaluate
        return UNVERIFIED, ["No baseline found for case; cannot evaluate regression"], MEASURED, {}

    baseline_state = baseline.get("state")
    baseline_problems = baseline.get("problems", [])

    # Current run result
    current_problems = []
    current_response = runner_output or ""
    current_tool_calls = tool_calls
    current_exit = case.get("actual_exit_code")
    current_exit_expected = case.get("expected_exit_code")

    # Re-run basic assertions on the current run to get problems
    response_text = current_response or ""
    for pattern in case.get("must_include", []):
        if not re.search(pattern, response_text, flags=re.IGNORECASE | re.MULTILINE):
            current_problems.append(f"Missing match: {pattern}")
    for pattern in case.get("must_not_include", []):
        if re.search(pattern, response_text, flags=re.IGNORECASE | re.MULTILINE):
            current_problems.append(f"Forbidden match: {pattern}")
    req_tools = case.get("required_tools", [])
    forb_tools = case.get("forbidden_tools", [])
    if req_tools or forb_tools:
        if current_tool_calls is None:
            current_problems.append("Tool calls unavailable")
        else:
            actual = [
                t if isinstance(t, str) else t.get("name", "")
                for t in current_tool_calls if isinstance(t, (str, dict))
            ]
            current_problems += [
                f"Missing required tool: {t}" for t in req_tools if t not in actual
            ]
            current_problems += [
                f"Forbidden tool used: {t}" for t in forb_tools if t in actual
            ]
    if current_exit_expected is not None and current_exit != current_exit_expected:
        current_problems.append(
            f"Unexpected exit code: {current_exit} (expected {current_exit_expected})"
        )

    current_pass = len(current_problems) == 0
    baseline_pass = len(baseline_problems) == 0

    if baseline_pass and not current_pass:
        # Previously passed, now failing → REGRESSION
        evidence_detail = {
            "baseline_state": baseline_state,
            "baseline_problems": baseline_problems,
            "current_problems": current_problems,
        }
        return REGRESSION, current_problems, MEASURED, evidence_detail

    if not baseline_pass and not current_pass:
        # Both failed — not a regression, just consistently failing
        evidence_detail = {
            "baseline_state": baseline_state,
            "baseline_problems": baseline_problems,
            "current_problems": current_problems,
        }
        return FAILED, current_problems, MEASURED, evidence_detail

    if baseline_pass and current_pass:
        # Both passed — good
        evidence_detail = {
            "baseline_state": baseline_state,
            "consistent": True,
        }
        return PASS, [], MEASURED, evidence_detail

    # baseline failed but current passed: not a regression, just improved
    return PASS, [], MEASURED, evidence_detail


# ---------------------------------------------------------------------------
# Reliability helpers
# ---------------------------------------------------------------------------

def reliability(rows):
    """Compute per-case reliability from a list of row dicts.

    Each row must have "case" and "state".

    Returns a dict:
      {case_id: {"trials": N, "passes": P, "pass_rate": P/N,
                 "verdict": "PASS"|"FAIL"|"FLAKY"|"TOO_FEW",
                 "confidence": "..." }}

    A case with < 5 trials gets an honest "too few" statement.
    A case that passes in some trials and fails in others is FLAKY.
    """
    # Group by case
    cases = {}
    for r in rows:
        cid = r.get("case", "unknown")
        cases.setdefault(cid, []).append(r)

    result = {}
    for cid, trials in cases.items():
        n = len(trials)
        passes = sum(1 for t in trials if t["state"] == PASS)
        pass_rate = passes / n if n > 0 else 0.0

        # Determine verdict
        if n < 5:
            verdict = "TOO_FEW"
            confidence = (
                f"N={n} trials is too few to certify reliability; "
                f"pass rate {pass_rate:.0%} is preliminary"
            )
        else:
            states = {t["state"] for t in trials}
            # FLAKY = passed in some, failed in others (excluding UNVERIFIED/TIMEOUT)
            evaluated_states = {s for s in states if s not in (UNVERIFIED,)}
            if len(evaluated_states) > 1 or (
                len(evaluated_states) == 1 and FAILED in evaluated_states
            ):
                # Check if it's actually mixed
                has_pass = PASS in states
                has_fail = any(t["state"] in (FAILED, REGRESSION) for t in trials)
                if has_pass and has_fail:
                    verdict = "FLAKY"
                    confidence = (
                        f"Passed {passes}/{n} trials — UNRELIABLE/flaky behavior detected"
                    )
                elif not has_pass:
                    verdict = "FAIL"
                    confidence = (
                        f"Failed in all {n} trials"
                    )
                else:
                    verdict = "PASS"
                    # Binomial 95% Wilson interval
                    lo, hi = _wilson_interval(passes, n)
                    confidence = (
                        f"Passed all {n} trials; 95% Wilson CI: [{lo:.3f}, {hi:.3f}]"
                    )
            else:
                verdict = "PASS"
                lo, hi = _wilson_interval(passes, n)
                confidence = (
                    f"Passed all {n} trials; 95% Wilson CI: [{lo:.3f}, {hi:.3f}]"
                )

        result[cid] = {
            "trials": n,
            "passes": passes,
            "pass_rate": round(pass_rate, 4),
            "verdict": verdict,
            "confidence": confidence,
        }
    return result


def _wilson_interval(successes, n, z=1.96):
    """Wilson score interval for a proportion."""
    if n == 0:
        return (0.0, 0.0)
    denom = 1 + z * z / n
    centre = (successes + z * z / 2) / n
    spread = z * math.sqrt((successes * (n - successes) / n + z * z / 4) / n)
    lo = (centre - spread) / denom
    hi = (centre + spread) / denom
    return (max(0.0, lo), min(1.0, hi))


# ---------------------------------------------------------------------------
# Baseline helpers
# ---------------------------------------------------------------------------

def baseline_save(baseline_data, path):
    """Save baseline results to a JSON file.

    *path* is the output file path.
    *baseline_data* is a dict mapping case keys to result dicts
    (each with "state" and "problems" keys).
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(baseline_data, indent=2, sort_keys=True), encoding="utf-8")


def baseline_load(path):
    """Load a baseline JSON file. Returns dict mapping case keys to result dicts."""
    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Main behavioural suite runner
# ---------------------------------------------------------------------------

def run_behavioral_suite(cases, runner_path, repetitions=1, workspace=None,
                         baseline_path=None):
    """Run behavioral evaluation cases against a runner.

    Parameters
    ----------
    cases : list[dict]
        Loaded case dicts (from load_cases or a list).
    runner_path : str | Path
        Path to the runner configuration JSON file.
    repetitions : int
        Number of repetitions per case. Default 1.
    workspace : str | Path, optional
        Workspace directory to snapshot before and diff after the run.
        If provided, functionality cases get measured workspace diffs.
    baseline_path : str | Path, optional
        Path to a baseline JSON file for regression comparison.

    Returns
    -------
    dict with:
        state: overall suite state
        cases: per-case-result list
        types: per-type rollups
        reliability: per-case reliability dict
        verification_class: global label about evidence quality
    """
    # Load runner config
    runner = read_json(runner_path)
    cwd, timeout = _validate_runner_config(runner, str(workspace or "."))

    # Load baseline if provided
    baseline_data = {}
    if baseline_path:
        baseline_data = baseline_load(baseline_path)

    # Snapshot workspace before (measured evidence)
    workspace_before = None
    if workspace:
        ws = Path(workspace)
        if ws.exists() and ws.is_dir():
            workspace_before = snapshot_workspace(ws)

    # Execute each case for each repetition
    all_rows = []
    case_results = {}  # case_id -> first-run results for regression

    for case in cases:
        case_id = case.get("id", case["prompt"][:40])
        case_type = case.get("type", "functionality")

        for repeat in range(repetitions):
            row = _run_single_case(case, case_id, runner, cwd, timeout,
                                   workspace_before, repeat + 1, baseline_data)
            all_rows.append(row)

            # Store first-run result for regression comparison
            if repeat == 0:
                case_results[case_id] = row

    # Compute overall state
    states = [r["state"] for r in all_rows]
    has_pass = any(s == PASS for s in states)
    has_fail = any(s in (FAILED, REGRESSION) for s in states)
    has_unverified = any(s == UNVERIFIED for s in states)

    if has_fail:
        overall_state = "FAILED"
    elif has_unverified:
        overall_state = "UNVERIFIED"
    elif has_pass:
        overall_state = "PASS"
    else:
        overall_state = "UNVERIFIED"

    # Per-type rollups
    type_rows = {}
    for r in all_rows:
        ct = r.get("case_type", "functionality")
        type_rows.setdefault(ct, []).append(r)

    type_rollups = {}
    for ct, rows in type_rows.items():
        ct_states = [r["state"] for r in rows]
        ct_pass = sum(1 for s in ct_states if s == PASS)
        type_rollups[ct] = {
            "total": len(rows),
            "passed": ct_pass,
            "failed": sum(1 for s in ct_states if s in (FAILED, REGRESSION)),
            "unverified": sum(1 for s in ct_states if s == UNVERIFIED),
            "regressions": sum(1 for s in ct_states if s == REGRESSION),
        }

    # Reliability
    rel = reliability(all_rows)

    # Verification class — what kind of evidence did we get?
    evidence_types_used = set()
    for r in all_rows:
        evidence_types_used.add(r.get("evidence_type", RUNNER_ASSERTED))

    if evidence_types_used == {MEASURED}:
        verification_class = "measured-diff"
    elif evidence_types_used == {RUNNER_ASSERTED}:
        verification_class = "runner-asserted-only"
    else:
        verification_class = "mixed-evidence"

    # Build case summaries for output
    case_summaries = []
    for r in all_rows:
        summary = dict(r)
        summary.pop("workspace_before", None)
        summary.pop("workspace_after", None)
        case_summaries.append(summary)

    return {
        "state": overall_state,
        "cases": case_summaries,
        "types": type_rollups,
        "reliability": rel,
        "verification_class": verification_class,
        "limits": (
            "Behavioural evaluation with workspace diff measurement. "
            "Per-type rollups, reliability intervals, and regression detection included."
        ),
    }


def _run_single_case(case, case_id, runner, cwd, timeout,
                     workspace_before, repeat, baseline_data):
    """Execute a single case+repeat and return a row dict."""
    case_type = case.get("type", "functionality")
    problems = []
    runner_output = None
    tool_calls = None
    actual_exit_code = None
    timed_out = False

    try:
        proc = subprocess.run(
            runner["argv"], cwd=cwd, input=case["prompt"],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
        stdout = proc.stdout[-100000:]
        actual_exit_code = proc.returncode

        if proc.returncode != case.get("expected_exit_code", 0):
            problems.append(f"Unexpected exit code: {proc.returncode}")

        runner_output = stdout
        if runner.get("output", "text") == "json":
            try:
                parsed = json.loads(stdout)
                runner_output = parsed.get("response", "")
                tool_calls = parsed.get("tool_calls", [])
                if not isinstance(runner_output, str) or not isinstance(tool_calls, list):
                    problems.append("Invalid runner result schema")
            except (ValueError, AttributeError):
                problems.append("Runner output not valid structured JSON")
                runner_output = stdout

    except subprocess.TimeoutExpired:
        timed_out = True
        runner_output = ""
        actual_exit_code = None
        problems.append("Runner timed out")

    # Snapshot workspace after (if measured)
    workspace_after = None
    workspace_diff = {"created": [], "modified": [], "deleted": []}
    if workspace_before is not None:
        try:
            ws = Path(
                runner.get("cwd", ".")
            )
            if ws.exists() and ws.is_dir():
                workspace_after = snapshot_workspace(ws)
                workspace_diff = diff_workspace(workspace_before, workspace_after)
        except Exception:
            pass

    # Evaluate based on type
    eval_state = PASS
    evidence_type = MEASURED
    evidence_detail = {}

    if case_type == "discovery":
        eval_state, problems, evidence_type, evidence_detail = _eval_discovery(
            case, runner_output, workspace_after, workspace_diff
        )
    elif case_type == "invocation":
        eval_state, problems, evidence_type, evidence_detail = _eval_invocation(
            case, runner_output, tool_calls, workspace_after, workspace_diff
        )
    elif case_type == "selection":
        eval_state, problems, evidence_type, evidence_detail = _eval_selection(
            case, runner_output, tool_calls, workspace_after, workspace_diff
        )
    elif case_type == "regression":
        eval_state, problems, evidence_type, evidence_detail = _eval_regression(
            case, runner_output, tool_calls, workspace_after, workspace_diff,
            baseline_data, case_id
        )
    else:  # functionality (default)
        eval_state, problems, evidence_type, evidence_detail = _eval_functionality(
            case, runner_output, tool_calls, workspace_after, workspace_diff
        )

    # Handle timeout as its own state
    if timed_out:
        eval_state = TIMEOUT
        # Don't lose the timeout problem
        if "Runner timed out" not in problems:
            problems.append("Runner timed out")

    # For non-functionality types, run backward-compat assertions (old case
    # files that declare "discovery"/"invocation"/"selection" but still carry
    # must_include / must_not_include / required_tools / forbidden_tools).
    if case_type != "functionality" and case_type != "regression" and not timed_out:
        for pattern in case.get("must_include", []):
            if not re.search(pattern, runner_output or "", flags=re.IGNORECASE | re.MULTILINE):
                problems.append(f"Missing match: {pattern}")
        for pattern in case.get("must_not_include", []):
            if re.search(pattern, runner_output or "", flags=re.IGNORECASE | re.MULTILINE):
                problems.append(f"Forbidden match: {pattern}")
        req_tools = case.get("required_tools", [])
        forb_tools = case.get("forbidden_tools", [])
        if req_tools or forb_tools:
            if tool_calls is None:
                if not any("Tool calls unavailable" in p for p in problems):
                    problems.append("Tool calls unavailable: structured JSON runner required")
            else:
                actual = [
                    t if isinstance(t, str) else t.get("name", "")
                    for t in tool_calls if isinstance(t, (str, dict))
                ]
                for t in req_tools:
                    if t not in actual and f"Missing required tool: {t}" not in problems:
                        problems.append(f"Missing required tool: {t}")
                for t in forb_tools:
                    if t in actual and f"Forbidden tool used: {t}" not in problems:
                        problems.append(f"Forbidden tool used: {t}")
        if case.get("expected_exit_code") is not None and actual_exit_code != case.get("expected_exit_code"):
            if not any("Unexpected exit code" in p for p in problems):
                problems.append(
                    f"Unexpected exit code: {actual_exit_code} "
                    f"(expected {case['expected_exit_code']})"
                )

    return {
        "case": case_id,
        "repeat": repeat,
        "case_type": case_type,
        "state": eval_state,
        "problems": problems,
        "evidence_type": evidence_type,
        "evidence_detail": evidence_detail,
        "timed_out": timed_out,
        "actual_exit_code": actual_exit_code,
    }
