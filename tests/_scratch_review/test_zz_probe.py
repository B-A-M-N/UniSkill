import json
from pathlib import Path
from uniskill.cli import main
from uniskill.deploy import state_root, latest_receipt, verify_receipt
from uniskill.harnesses import inspect_harness_skill


def sample(tmp_path, name="cli-skill"):
    p = tmp_path / "canonical" / name
    p.mkdir(parents=True)
    (p / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Use when verifying the repair lifecycle\n---\n\n"
        "# CLI Skill\n\nDo the work with observable evidence.\n")
    return p


def test_diag(tmp_path, monkeypatch, capsys):
    s = sample(tmp_path)
    workspace = tmp_path / "repo"
    assert main(["install", str(s), "--target", "codex", "--workspace",
                 str(workspace), "--apply"]) == 0
    capsys.readouterr()
    # Inspect the harness state exactly as diagnose does
    insp = inspect_harness_skill("cli-skill", "codex", "project",
                                 workspace.resolve(), Path.home())
    print("INSPECT state =", insp["state"], " candidates=", insp["candidates"])
    assert main(["diagnose", "cli-skill", "--workspace", str(workspace)]) == 0
    out = json.loads(capsys.readouterr().out)
    print("STAGE_STATES =", out.get("stage_states"))
    print("DIAGNOSIS =", out.get("diagnosis"))
    assert out["diagnosis"]["state"] in {"DIAGNOSED", "UNPROVEN"}
    assert out["stage_states"]["installed"] == "PASS"