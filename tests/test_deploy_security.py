"""Adversarial security tests for the deployment trust boundary (Gate A).

Every test here must FAIL against the original (vulnerable) deploy.py and PASS
against the remediated one. They are written to be run against whichever
`uniskill` package is importable, so the same file drives both the normal suite
and the RED proof harness (PYTHONPATH pointing at a scratch package containing
the reconstructed original deploy.py).
"""
from pathlib import Path
import json
import os
import shutil
import hashlib
import pytest

from uniskill.common import UniSkillError, digest_tree
from uniskill.deploy import (install, plan, rollback, verify_receipt,
                            read_receipt, latest_receipt)
try:
    from uniskill.deploy import ownership
except ImportError:  # original has no ownership API -> REL-01 tests must fail (RED)
    ownership = None


def skill(tmp, name="example-skill", text="Body"):
    s = tmp / "canonical" / name
    s.mkdir(parents=True)
    (s / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: a reproducible skill\n---\n{text}\n", encoding="utf-8")
    return s


def install_once(tmp, name="example-skill", labels=("codex",)):
    s = skill(tmp, name)
    ws = tmp / "ws"
    state = tmp / "state"
    rec = install(plan(s, list(labels), "project", ws, tmp), state)
    return s, ws, state, rec, rec["destinations"][0]["path"]


def test_symlink_swap_between_plan_and_install_refuses(tmp_path):
    """SEC-01: swapping a parent of the destination to a symlink between plan()
    and install() must not write outside the workspace and must not report
    INSTALLED."""
    s = skill(tmp_path)
    ws = tmp_path / "ws"
    state = tmp_path / "state"
    p = plan(s, ["codex"], "project", ws, tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    ws.mkdir()
    os.symlink(str(outside), ws / ".agents")  # redirect .agents tree outside
    with pytest.raises(UniSkillError):
        install(p, state)
    assert not (outside / "example-skill").exists()
    assert not (ws / ".agents" / "example-skill").exists()
    assert not (state / "receipts").exists() or not (state / "receipts").glob("*.json")


def test_forged_receipt_cannot_authorize_deletion(tmp_path):
    """SEC-02: a receipt with valid-looking JSON but no valid signature must be
    refused at rollback and the unrelated target must survive."""
    _, _, state, _, _ = install_once(tmp_path)
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "SKILL.md").write_text("---\nname: victim\ndescription: x\n---\nV\n", encoding="utf-8")
    state_receipts = state / "receipts"
    rid = "forged-1"
    payload = {
        "receipt_id": rid, "skill": "victim", "scope": "project",
        "targets": ["codex"], "source_digest": "deadbeef",
        "created_at": "2020-01-01T00:00:00+00:00", "state": "INSTALLED",
        "destinations": [{"path": str(victim), "digest": digest_tree(victim),
                          "previous_digest": None, "backup": None}],
    }
    (state_receipts / (rid + ".json")).write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(UniSkillError):
        rollback(state, rid)
    assert (victim / "SKILL.md").exists()


def test_tampered_receipt_rejected(tmp_path):
    """SEC-02: editing a destination path in a genuinely-signed receipt (without
    re-signing) must be rejected and the unrelated target must survive."""
    _, _, state, rec, _ = install_once(tmp_path)
    rid = rec["receipt_id"]
    rf = state / "receipts" / (rid + ".json")
    data = json.loads(rf.read_text(encoding="utf-8"))
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "SKILL.md").write_text("---\nname: t\ndescription: x\n---\nEVIL\n", encoding="utf-8")
    data["destinations"][0]["path"] = str(victim)
    data["destinations"][0]["digest"] = digest_tree(victim)
    data["destinations"][0]["backup"] = None
    data["destinations"][0]["previous_digest"] = None
    rf.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(UniSkillError):
        rollback(state, rid)
    # read_receipt / verify must also reject a tampered receipt.
    with pytest.raises(UniSkillError):
        read_receipt(state, rid)
    with pytest.raises(UniSkillError):
        verify_receipt(data, state)
    assert (victim / "SKILL.md").exists()


def test_rollback_older_receipt_after_supersede_refuses(tmp_path):
    """REL-01: after installing the same content twice, rolling back the OLDER
    receipt must be refused; files stay and the newer receipt still verifies."""
    s = skill(tmp_path)
    ws = tmp_path / "ws"
    state = tmp_path / "state"
    r1 = install(plan(s, ["codex"], "project", ws, tmp_path), state)
    r2 = install(plan(s, ["codex"], "project", ws, tmp_path), state)
    with pytest.raises(UniSkillError):
        rollback(state, r1["receipt_id"])
    dest = Path(r2["destinations"][0]["path"])
    assert dest.exists()
    assert verify_receipt(read_receipt(state, r2["receipt_id"]), state)["state"] == "INSTALLED"


def test_rollback_newest_releases_ownership_to_predecessor(tmp_path):
    """REL-01: rolling back the newest receipt after a supersede works and
    ownership returns to the predecessor receipt."""
    s = skill(tmp_path)
    ws = tmp_path / "ws"
    state = tmp_path / "state"
    r1 = install(plan(s, ["codex"], "project", ws, tmp_path), state)
    r2 = install(plan(s, ["codex"], "project", ws, tmp_path), state)
    dest = Path(r2["destinations"][0]["path"])
    assert rollback(state, r2["receipt_id"])["state"] == "ROLLED_BACK"
    assert ownership is not None, "ownership() API missing (RED)"
    owners = ownership(state)
    # ownership returned to the predecessor receipt id for this destination.
    assert str(dest) in owners
    assert owners[str(dest)]["receipt_id"] == r1["receipt_id"]
    assert dest.exists()
    assert verify_receipt(read_receipt(state, r1["receipt_id"]), state)["state"] == "INSTALLED"


def test_ownership_survives_deleted_ledger(tmp_path):
    """REL-01 hardening: ownership is derived from the SIGNED receipt chain, so
    deleting the mutable ownership ledger must not let a stale receipt delete
    files that a newer active receipt owns."""
    s = skill(tmp_path)
    ws = tmp_path / "ws"
    state = tmp_path / "state"
    r1 = install(plan(s, ["codex"], "project", ws, tmp_path), state)
    r2 = install(plan(s, ["codex"], "project", ws, tmp_path), state)
    (state / "ownership.json").unlink()
    with pytest.raises(UniSkillError, match="roll back the newest receipt first"):
        rollback(state, r1["receipt_id"])
    dest = Path(r2["destinations"][0]["path"])
    assert dest.exists()
    assert verify_receipt(read_receipt(state, r2["receipt_id"]), state)["state"] == "INSTALLED"


def test_ownership_survives_hand_edited_ledger(tmp_path):
    """REL-01 hardening: a hand-edited ledger that assigns the destination back
    to the older receipt must not override the signed supersede chain."""
    s = skill(tmp_path)
    ws = tmp_path / "ws"
    state = tmp_path / "state"
    r1 = install(plan(s, ["codex"], "project", ws, tmp_path), state)
    r2 = install(plan(s, ["codex"], "project", ws, tmp_path), state)
    ledger_path = state / "ownership.json"
    ledger = json.loads(ledger_path.read_text())
    for entry in ledger.values():
        entry["receipt_id"] = r1["receipt_id"]
    ledger_path.write_text(json.dumps(ledger))
    with pytest.raises(UniSkillError, match="roll back the newest receipt first"):
        rollback(state, r1["receipt_id"])
    assert Path(r2["destinations"][0]["path"]).exists()


def test_rollback_of_lone_receipt_still_works_without_ledger(tmp_path):
    """The tamper defense must not break the normal case: with a single active
    receipt, deleting the ledger is harmless and rollback still succeeds."""
    s = skill(tmp_path)
    ws = tmp_path / "ws"
    state = tmp_path / "state"
    r1 = install(plan(s, ["codex"], "project", ws, tmp_path), state)
    (state / "ownership.json").unlink()
    assert rollback(state, r1["receipt_id"])["state"] == "ROLLED_BACK"
    assert not Path(r1["destinations"][0]["path"]).exists()


def test_post_install_digest_mismatch_reported_failed(tmp_path, monkeypatch):
    """No-false-success: if a destination no longer holds the source digest after
    publishing, install() must FAIL (raise) rather than mint an INSTALLED receipt,
    and must not leave the newly created destination behind."""
    s = skill(tmp_path)
    ws = tmp_path / "ws"
    state = tmp_path / "state"
    p = plan(s, ["codex"], "project", ws, tmp_path)
    dest = Path(p["destinations"][0]["path"])

    real_replace = os.replace
    corrupted = {"done": False}

    def evil_replace(a, b):
        real_replace(a, b)
        # Corrupt the just-published destination right after the final publish.
        if not corrupted["done"] and str(b) == str(dest) and Path(b).is_dir():
            corrupted["done"] = True
            (Path(b) / "SKILL.md").write_text("CORRUPTED", encoding="utf-8")

    monkeypatch.setattr("uniskill.deploy.os.replace", evil_replace)
    before = sorted((state / "receipts").glob("*.json")) if (state / "receipts").exists() else []
    with pytest.raises(UniSkillError):
        install(p, state)
    after = sorted((state / "receipts").glob("*.json")) if (state / "receipts").exists() else []
    assert before == after  # no new receipt minted
    assert not dest.exists() or list(dest.iterdir()) == []  # partial work rolled back


def test_receipt_destination_outside_scope_anchor_rejected(tmp_path):
    """SEC-02: a validly-signed destination whose chain is redirected outside the
    scope anchor (via a symlink swap) must be refused at rollback; nothing outside
    the anchor may be deleted."""
    s, ws, state, rec, dpath = install_once(tmp_path)
    rid = rec["receipt_id"]
    outside = tmp_path / "outside"
    outside.mkdir()
    # Move the installed tree out and replace the parent dir with a symlink so
    # the recorded destination now resolves outside the scope anchor.
    os.replace(ws / ".agents" / "skills", outside / "skills")
    os.rmdir(ws / ".agents")
    os.symlink(outside, ws / ".agents")
    assert (outside / "skills" / s.name).exists()
    with pytest.raises(UniSkillError):
        rollback(state, rid)
    assert (outside / "skills" / s.name).exists()


def test_latest_receipt_and_fresh_install_state(tmp_path):
    """A fresh install reports INSTALLED; latest_receipt resolves correctly; no
    false 'unchanged' claims for a brand-new destination."""
    s, ws, state, rec, _ = install_once(tmp_path)
    assert rec["state"] == "INSTALLED"
    assert latest_receipt(state, s.name)["receipt_id"] == rec["receipt_id"]
    assert ownership is not None, "ownership() API missing (RED)"
    assert ownership(state, skill=s.name)