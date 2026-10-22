import json
from pathlib import Path
from uniskill.cli import main


def sample(tmp_path, name="cli-skill"):
    p = tmp_path / "canonical" / name
    p.mkdir(parents=True)
    (p / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Use when verifying the repair lifecycle\n---\n\n"
        "# CLI Skill\n\nDo the work with observable evidence.\n")
    return p


def test_diag_exact(tmp_path, capsys):
    s = sample(tmp_path)
    workspace = tmp_path / "repo"
    assert main(["install", str(s), "--target", "codex", "--workspace",
                 str(workspace), "--apply"]) == 0
    capsys.readouterr()
    assert main(["diagnose", "cli-skill", "--workspace", str(workspace)]) == 0
    out = json.loads(capsys.readouterr().out)
    print("STAGE_STATES =", out.get("stage_states"))
    print("STATE =", out.get("diagnosis", {}).get("state"))
    assert out["diagnosis"]["state"] in {"DIAGNOSED", "UNPROVEN"}, \
        "got " + str(out["diagnosis"].get("state"))
    assert out["stage_states"]["installed"] == "PASS"