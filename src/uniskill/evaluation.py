"""Opt-in executable test runner with text and structured tool-trace assertions.

No agent is silently launched. A user-supplied runner is a trusted local executable;
real harness discovery/invocation are not inferred from test output alone.
"""
from __future__ import annotations
import json
import re
import subprocess
from pathlib import Path
from .common import UniSkillError, read_json

def load_cases(path):
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
        for key in ("must_include", "must_not_include", "required_tools", "forbidden_tools"):
            if not isinstance(row.get(key, []), list) or any(
                not isinstance(x, str) for x in row.get(key, [])):
                raise UniSkillError(f"Case line {i}: {key} must be string list")
        rows.append(row)
    if not rows:
        raise UniSkillError("No test cases")
    return rows

def run_evals(cases, runner_path, repetitions=1):
    runner = read_json(runner_path)
    if not isinstance(runner, dict) or not isinstance(runner.get("argv"), list) or not all(
        isinstance(x, str) and x for x in runner["argv"]):
        raise UniSkillError("Runner config must contain argv: string[]")
    if runner.get("shell", False):
        raise UniSkillError("Shell runners are not supported")
    if runner.get("mode", "stdin") != "stdin":
        raise UniSkillError("Runner mode must be stdin")
    cwd = Path(runner.get("cwd", ".")).resolve()
    if not cwd.is_dir():
        raise UniSkillError(f"Runner cwd does not exist: {cwd}")
    timeout = float(runner.get("timeout_seconds", 60))
    if not 1 <= timeout <= 300:
        raise UniSkillError("Runner timeout must be 1-300 seconds")
    if not 1 <= repetitions <= 20:
        raise UniSkillError("Repetitions must be 1-20")
    rows = []
    for case in cases:
        for repeat in range(repetitions):
            problems = []
            try:
                proc = subprocess.run(runner["argv"], cwd=cwd, input=case["prompt"],
                                      capture_output=True, text=True, timeout=timeout, check=False)
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
                        tools = [t if isinstance(t, str) else t.get("name")
                                 for t in tool_calls if isinstance(t, (str, dict))]
                        problems += [f"Missing required tool: {t}" for t in required if t not in tools]
                        problems += [f"Forbidden tool used: {t}" for t in forbidden if t in tools]
            except (subprocess.TimeoutExpired, OSError) as exc:
                problems.append(f"Runner failed: {type(exc).__name__}: {exc}")
            rows.append({"case":case.get("id", case["prompt"][:40]), "repeat":repeat + 1,
                         "state":"PASS" if not problems else "FAILED", "problems":problems})
    total_pass = sum(r["state"] == "PASS" for r in rows)
    return {"state":"EVAL_PASS" if total_pass == len(rows) else "FAILED",
            "passed":total_pass, "total":len(rows), "cases":rows,
            "limits":"Output and declared tool-call assertions only. Does not establish discovery, auto-trigger, sandboxing, or cross-harness portability."}
