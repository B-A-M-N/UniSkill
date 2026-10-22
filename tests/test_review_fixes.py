"""Regression tests pinning the confirmed adversarial-review fixes.

Each test here maps to a specific confirmed finding:
  FINDING 1  diagnose/status agreement on an unproven discovery stage, and the
             POLICY_DENIED > UNVERIFIED rollup precedence.
  FINDING 2  capability_profile() delegates to adapters.support_class() so the
             CLI `capabilities` and the adapter layer cannot contradict each
             other (no duplicated table in harnesses.py).
  FINDING 3  repair --execute can actually repair a missing tree (REINSTALL /
             REPLAN_ROOTS) deterministically, and never silently overwrites a
             drifted/operator-edited tree (RECONCILE_TRANSACTION FAILs).
             Receipt re-plan context (source/workspace/home) is signed; old
             receipts still verify and the new fields cannot be stripped.
  FINDING 4  (documentation-only; no runtime assert).

Nothing here may claim DISCOVERED / CERTIFIED / REPAIRED without real evidence.
"""
import json
import shutil

import pytest

from uniskill.cli import main
from uniskill.common import UniSkillError, digest_tree, load_signing_key, sign_receipt_body
from uniskill.deploy import (_receipt_body, install, latest_receipt, plan,
                             read_receipt, verify_receipt)
from uniskill.harnesses import capability_profile, load_registry


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def sample(tmp_path, name="fix-skill"):
    p = tmp_path / "canonical" / name
    p.mkdir(parents=True)
    (p / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Use when pinning review fixes\n---\n\n"
        "# Review fix skill\n\nDo the work with observable evidence.\n")
    return p


def deployed(tmp_path, capsys=None, name="fix-skill"):
    """Install a skill into codex (no native probe) via the CLI."""
    s = sample(tmp_path, name)
    workspace = tmp_path / "repo"
    assert main(["install", str(s), "--target", "codex", "--workspace",
                 str(workspace), "--apply"]) == 0
    if capsys is not None:
        capsys.readouterr()  # discard the install receipt
    return s, workspace, workspace / ".uniskill"


# ---------------------------------------------------------------------------
# FINDING 1
# ---------------------------------------------------------------------------

def test_diagnose_matches_status_that_discovery_is_unproven(tmp_path, capsys):
    """After a clean install into a harness with no native probe, status and
    diagnose must agree that discovery is unproven: status reports UNVERIFIED
    and diagnose must NOT report CLEAN."""
    _s, workspace, _state = deployed(tmp_path, capsys)
    assert main(["status", "fix-skill", "--workspace", str(workspace)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["targets"]["codex"]["discovery"] == "UNVERIFIED"
    assert status["earliest_unproven_stage"] == {"harness": "codex", "stage": "discovered"}

    assert main(["diagnose", "fix-skill", "--workspace", str(workspace)]) == 0
    diag = json.loads(capsys.readouterr().out)
    # installation is proven, discovery is not -> diagnose must not be CLEAN.
    assert diag["stage_states"]["installed"] == "PASS"
    assert diag["stage_states"]["discovered"] == "UNVERIFIED"
    assert diag["diagnosis"]["state"] != "CLEAN"
    assert diag["diagnosis"]["failure"] == "UNVERIFIED"
    assert diag["diagnosis"]["blocked"] is True


def test_policy_denied_outranks_unverified_in_rollup(tmp_path, capsys):
    """A policy-denied harness must outrank an UNVERIFIED (merely unprobed)
    harness in the diagnose rollup."""
    s = sample(tmp_path)
    workspace = tmp_path / "repo"
    # Policy-deny the skill for the crush harness.
    shutil.copytree(s, workspace / ".crush/skills/fix-skill")
    (workspace / "crushrc").write_text("option disable-skill fix-skill\n")
    (workspace / "crush.json").write_text(json.dumps(
        {"options": {"disabled_skills": ["fix-skill"]}}))
    assert main(["install", str(s), "--target", "crush", "--workspace",
                 str(workspace), "--apply"]) == 0
    capsys.readouterr()
    assert main(["diagnose", "fix-skill", "--workspace", str(workspace),
                 "--target", "crush", "codex"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["stage_states"]["discovered"] == "POLICY_DENIED"
    assert out["diagnosis"]["intervention"] == "REPORT_POLICY_BLOCKER"
    assert out["diagnosis"]["blocked"] is True


# ---------------------------------------------------------------------------
# FINDING 2
# ---------------------------------------------------------------------------

def test_capability_profile_matches_adapters_gemini(tmp_path):
    assert capability_profile("gemini")["support_class"] == "DISCOVERY_PROBED"


def test_capability_profile_kimi_native_documented(tmp_path):
    assert capability_profile("kimi-code-cli")["support_class"] == "NATIVE_DOCUMENTED"


def test_capability_profile_codex_install_only(tmp_path):
    assert capability_profile("codex")["support_class"] == "INSTALL_ONLY"


def test_capability_profile_custom_profile_install_only(tmp_path):
    reg_path = tmp_path / "registry.json"
    reg_path.write_text(json.dumps({"harnesses": {"my-agent": {
        "executable": "my-agent",
        "project_roots": [".myagent/skills"],
        "user_roots": [".myagent/skills"]}}}))
    registry = load_registry(reg_path)
    assert capability_profile("my-agent", registry)["support_class"] == "INSTALL_ONLY"


def test_capability_profile_is_single_source_of_truth(tmp_path):
    """capability_profile must agree with adapters.support_class for the same
    registry — the table lives in adapters, not duplicated in harnesses.py."""
    from uniskill.adapters import adapter_table
    table = adapter_table()
    for name in table:
        assert capability_profile(name)["support_class"] == table[name], name
    assert table["gemini"] == capability_profile("gemini")["support_class"]
    assert table["codex"] == capability_profile("codex")["support_class"]


# ---------------------------------------------------------------------------
# FINDING 3
# ---------------------------------------------------------------------------

def test_repair_execute_restores_missing_tree(tmp_path, capsys):
    """A missing deployed tree repaired with --execute reaches REPAIRED and the
    files come back with the correct digest (rebuilt from the signed receipt)."""
    s, workspace, state = deployed(tmp_path, capsys)
    dest = workspace / ".agents/skills/fix-skill"
    assert dest.is_dir()
    # Remove the deployed tree, then diagnose: it becomes a repairable
    # installed/MISSING stage.
    shutil.rmtree(dest)
    assert not dest.exists()
    assert main(["diagnose", "fix-skill", "--workspace", str(workspace)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["stage_states"]["installed"] == "MISSING"
    assert out["diagnosis"]["stage"] == "installed"
    assert out["diagnosis"]["intervention"] == "REINSTALL"

    assert main(["repair", "fix-skill", "--workspace", str(workspace),
                 "--execute"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["repair"]["state"] == "REPAIRED"
    assert any(t["result"] == "REPAIRED" for t in out["repair"]["trace"])
    # Files are restored with the correct digest.
    assert dest.is_dir()
    assert digest_tree(dest) == digest_tree(s)
    # And the active receipt verifies as INSTALLED.
    r = latest_receipt(state, "fix-skill")
    assert verify_receipt(r, state)["state"] == "INSTALLED"
    # Repair restored files, it did NOT fabricate a discovery claim.
    assert out["diagnosis"]["stage"] != "discovered"


def test_repair_execute_never_overwrites_drifted_tree(tmp_path, capsys):
    """A drifted (operator-edited) tree is NOT silently overwritten: the edit
    survives and the reconcile handler reports FAILED with the drifted path."""
    _s, workspace, _state = deployed(tmp_path, capsys)
    dest = workspace / ".agents/skills/fix-skill"
    (dest / "SKILL.md").write_text("operator made this edit\n")
    assert main(["repair", "fix-skill", "--workspace", str(workspace),
                 "--execute"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["repair"]["state"] == "STOPPED"
    assert any(t["result"] == "FAILED" for t in out["repair"]["trace"])
    # Operator edit survives, untouched.
    assert (dest / "SKILL.md").read_text() == "operator made this edit\n"
    # The FAILED handler names the drifted path.
    detail = json.dumps(out["repair"]["trace"])
    assert "fix-skill/SKILL.md" in detail or str(dest) in detail


def test_no_probe_harness_cannot_be_repaired_into_discovery(tmp_path, capsys):
    """FINDING 1+3: a harness with no native probe (codex) cannot be 'repaired'
    into a discovery claim. Repair stops (blocked DECLARE_UNVERIFIABLE); no
    trace ever yields DISCOVERED/CERTIFIED."""
    _s, workspace, _state = deployed(tmp_path, capsys)
    assert main(["repair", "fix-skill", "--workspace", str(workspace),
                 "--execute"]) == 0
    out = json.loads(capsys.readouterr().out)
    # The one plan is the blocked DECLARE_UNVERIFIABLE -> BLOCKED, not a fix.
    assert out["repair"]["state"] == "STOPPED"
    assert out["repair"]["trace"][0]["result"] == "BLOCKED"
    assert out["repair"]["trace"][0]["intervention"] == "DECLARE_UNVERIFIABLE"
    assert "DISCOVERED" not in json.dumps(out["repair"]["trace"])
    assert "CERTIFIED" not in json.dumps(out["repair"]["trace"])


def test_repair_advisory_actions_never_fabricate_success(tmp_path, capsys):
    """Interventions that need an LLM/operator (optimization, instruction
    revision, declaration, policy) must stay ADVISORY/blocked, never auto-run.
    A policy-denied blocker stops with a blocked plan, not REPAIRED."""
    s = sample(tmp_path)
    workspace = tmp_path / "repo"
    shutil.copytree(s, workspace / ".crush/skills/fix-skill")
    (workspace / "crushrc").write_text("option disable-skill fix-skill\n")
    (workspace / "crush.json").write_text(json.dumps(
        {"options": {"disabled_skills": ["fix-skill"]}}))
    assert main(["install", str(s), "--target", "crush", "--workspace",
                 str(workspace), "--apply"]) == 0
    capsys.readouterr()
    assert main(["repair", "fix-skill", "--workspace", str(workspace),
                 "--target", "crush", "--execute"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["repair"]["trace"][0]["result"] == "BLOCKED"
    assert out["repair"]["trace"][0]["intervention"] == "REPORT_POLICY_BLOCKER"
    assert all(t["result"] != "REPAIRED" for t in out["repair"]["trace"])


# ---------------------------------------------------------------------------
# FINDING 3 - receipt schema/backward-compatibility
# ---------------------------------------------------------------------------

def test_receipt_records_and_signs_replan_source(tmp_path):
    """The receipt must record the absolute source/workspace/home needed to
    re-plan (signed fields), and stripping any of them must break the
    signature rather than silently downgrading the plan context."""
    s = sample(tmp_path)
    ws = tmp_path / "ws"
    state = tmp_path / "state"
    rec = install(plan(s, ["codex"], "project", ws, tmp_path), state)
    assert rec["source"] == str(s.resolve())
    assert rec["workspace"] == str(ws.resolve())
    assert "home" in rec

    rid = rec["receipt_id"]
    rf = state / "receipts" / (rid + ".json")
    # Sanity: the untouched receipt verifies.
    assert read_receipt(state, rid)["receipt_id"] == rid
    # Tampering: strip the signed re-plan context but keep the signature. The
    # canonical body then differs from what was signed, so verification fails.
    data = json.loads(rf.read_text(encoding="utf-8"))
    for k in ("source", "workspace", "home"):
        data.pop(k, None)
    rf.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(UniSkillError):
        read_receipt(state, rid)


def test_old_receipt_without_replan_fields_still_verifies(tmp_path):
    """Backward compatibility: a receipt signed before the re-plan fields
    existed (body without source/workspace/home) must still verify — the
    canonicalization omits the new fields when absent."""
    s = sample(tmp_path)
    ws = tmp_path / "ws"
    state = tmp_path / "state"
    rec = install(plan(s, ["codex"], "project", ws, tmp_path), state)
    rid = rec["receipt_id"]
    # Re-sign an 'old-style' body: the exact pre-fix canonicalization (no new
    # fields), written over the receipt file.
    old_body = {k: rec[k] for k in ("receipt_id", "skill", "scope", "targets",
                                    "source_digest", "created_at", "state",
                                    "destinations")}
    old_body["signature"] = sign_receipt_body(load_signing_key(state), old_body)
    (state / "receipts" / (rid + ".json")).write_text(
        json.dumps(old_body), encoding="utf-8")
    # `_receipt_body` on a receipt lacking the new fields yields exactly the old
    # body, so the old signature still verifies.
    assert read_receipt(state, rid)["receipt_id"] == rid
    assert verify_receipt(old_body, state)["state"] == "INSTALLED"