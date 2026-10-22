import json

import pytest

from uniskill.cli import main


def sample(tmp_path, name="cli-skill"):
    p = tmp_path / "canonical" / name
    p.mkdir(parents=True)
    (p / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Use when verifying the repair lifecycle\n---\n\n"
        "# CLI Skill\n\nDo the work with observable evidence.\n")
    return p


def deployed(tmp_path, capsys=None):
    """A skill installed into a project root; returns (skill, workspace, state)."""
    s = sample(tmp_path)
    workspace = tmp_path / "repo"
    assert main(["install", str(s), "--target", "codex", "--workspace",
                 str(workspace), "--apply"]) == 0
    if capsys is not None:
        capsys.readouterr()  # discard the install receipt
    return s, workspace, workspace / ".uniskill"


def test_status_layers_installation_from_behavior(tmp_path, capsys):
    _s, workspace, _state = deployed(tmp_path, capsys)
    assert main(["status", "cli-skill", "--workspace", str(workspace)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["deployment"]["state"] == "INSTALLED"
    assert out["deployment"]["evidence"] == "digest-tree-match"
    assert out["targets"]["codex"]["discovery"] == "UNVERIFIED"
    assert out["targets"]["codex"]["functional"] == "UNVERIFIED"
    assert out["targets"]["codex"]["certified"] == "UNVERIFIED"
    assert out["earliest_unproven_stage"] == {"harness": "codex", "stage": "discovered"}


def test_status_does_not_certify_without_probe(tmp_path, capsys):
    # status has no --probe flag: discovery/invocation/selection/functional are
    # UNVERIFIED unless a real probe was executed.
    _s, workspace, _state = deployed(tmp_path, capsys)
    assert main(["status", "cli-skill", "--workspace", str(workspace)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["targets"]["codex"]["certified"] == "UNVERIFIED"
    assert "no runtime probe executed" in str(
        out["targets"]["codex"]["evidence"]).lower()


def test_diagnose_reports_earliest_stage(tmp_path, capsys):
    _s, workspace, _state = deployed(tmp_path, capsys)
    assert main(["diagnose", "cli-skill", "--workspace", str(workspace)]) == 0
    out = json.loads(capsys.readouterr().out)
    # Files are present but no runtime probe ran, so no stage FAILED: the
    # diagnosis is CLEAN (nothing to fix) rather than a fabricated failure.
    assert out["diagnosis"]["state"] == "CLEAN"
    assert out["stage_states"] == {"installed": "PASS"}
    assert all(v["state"] == "FILES_PRESENT"
               for v in out["inspections"].values())


def test_diagnose_detects_drift(tmp_path, capsys):
    s, workspace, _state = deployed(tmp_path, capsys)
    dest = workspace / ".agents/skills/cli-skill"
    (dest / "SKILL.md").write_text("operator edited this\n")
    assert main(["diagnose", "cli-skill", "--workspace", str(workspace)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["stage_states"]["installed"] == "DRIFTED"
    assert out["diagnosis"]["stage"] == "installed"
    assert out["diagnosis"]["intervention"] == "RECONCILE_TRANSACTION"


def test_repair_reports_nothing_to_do_when_clean(tmp_path, capsys):
    """A deployed-but-unprobed skill has no FAILING stage; repair must say so
    rather than invent a repair or error out."""
    _s, workspace, _state = deployed(tmp_path, capsys)
    assert main(["repair", "cli-skill", "--workspace", str(workspace)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["state"] == "NO_REPAIR"
    assert out["repair"] is None
    assert "No failing stage" in out["reason"]
    assert out["budget"]["attempts"] == 3  # untouched: nothing was spent


def test_repair_drift_is_advisory_not_fake_success(tmp_path, capsys):
    """A drifted install IS repairable, but with no handler registered the
    engine must report ADVISORY and never claim REPAIRED."""
    _s, workspace, _state = deployed(tmp_path, capsys)
    (workspace / ".agents/skills/cli-skill/SKILL.md").write_text("operator edit\n")
    assert main(["repair", "cli-skill", "--workspace", str(workspace)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["repair_plan"]["intervention"] == "RECONCILE_TRANSACTION"

    assert main(["repair", "cli-skill", "--workspace", str(workspace),
                 "--execute"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["repair"]["state"] == "NO_ACTION"
    assert out["repair"]["trace"][0]["result"] == "ADVISORY"
    assert all(t["result"] != "REPAIRED" for t in out["repair"]["trace"])
    # The operator's edit is never silently reverted.
    assert (workspace / ".agents/skills/cli-skill/SKILL.md").read_text() == "operator edit\n"


def test_diagnose_reports_policy_blocker_over_missing_files(tmp_path, capsys):
    """A policy-denied harness is a real blocker and must outrank a harness
    that merely lacks probe evidence."""
    s = sample(tmp_path)
    workspace = tmp_path / "repo"
    (workspace / ".crush/skills").mkdir(parents=True)
    import shutil
    shutil.copytree(s, workspace / ".crush/skills/cli-skill")
    (workspace / "crushrc").write_text("option disable-skill cli-skill\n")
    (workspace / "crush.json").write_text(json.dumps(
        {"options": {"disabled_skills": ["cli-skill"]}}))
    assert main(["install", str(s), "--target", "codex", "--workspace",
                 str(workspace), "--apply"]) == 0
    capsys.readouterr()
    assert main(["diagnose", "cli-skill", "--workspace", str(workspace),
                 "--target", "crush", "codex"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["stage_states"]["discovered"] == "POLICY_DENIED"
    assert out["diagnosis"]["intervention"] == "REPORT_POLICY_BLOCKER"
    assert out["diagnosis"]["blocked"] is True


def test_repair_never_claims_success_without_handler(tmp_path, capsys):
    _s, workspace, _state = deployed(tmp_path, capsys)
    (workspace / ".agents/skills/cli-skill/SKILL.md").write_text("operator edit\n")
    assert main(["repair", "cli-skill", "--workspace", str(workspace),
                 "--execute"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["repair"]["safety"]["invariant_tradeaway_blocked"] is True
    for effect in ("weaken_permission", "grant_credential", "skip_consent", "drop_invariant"):
        assert effect in out["repair"]["safety"]["never"]


def test_diagnose_without_receipt_is_unverified(tmp_path, capsys):
    assert main(["diagnose", "missing-skill", "--workspace", str(tmp_path / "nope")]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "UNVERIFIED"


def test_diagnose_rejects_unknown_target(tmp_path, capsys):
    _s, workspace, _state = deployed(tmp_path, capsys)
    # main() reports a CLI error as exit code 2 with a JSON error on stderr.
    assert main(["diagnose", "cli-skill", "--workspace", str(workspace),
                 "--target", "no-such-harness"]) == 2
    assert json.loads(capsys.readouterr().err)["state"] == "ERROR"