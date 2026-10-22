# RED-proof reference: verbatim reconstruction of the ORIGINAL (vulnerable)
# deploy.py, reconstructed from the session read before the security remediation.
# Used ONLY to demonstrate that the adversarial tests in test_deploy_security.py
# fail against the vulnerable implementation. Not part of the deployed package.
"""Dry-run plans, transactional installations, evidence receipts, safe rollback."""
from __future__ import annotations
import os
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from .common import UniSkillError, digest_tree, dump_json, in_tree, read_json
from .harnesses import HARNESSES, choose_roots, roots
from .validator import validate_skill

def state_root(scope, workspace, home, override=None):
    return Path(override) if override else (workspace / ".uniskill" if scope == "project"
                                             else home / ".local/state/uniskill")

def plan(skill, targets, scope, workspace, home, registry=None):
    skill, workspace, home = Path(skill), Path(workspace), Path(home)
    report = validate_skill(skill)
    if report["state"] != "VALID":
        raise UniSkillError("Skill invalid: " + "; ".join(report["errors"]))
    registry = registry or HARNESSES
    if scope not in {"project", "user"} or not targets or set(targets) - set(registry):
        raise UniSkillError("Invalid scope or harness target")
    chosen = choose_roots(sorted(set(targets)), scope, workspace, home, registry)
    digest = digest_tree(skill)
    collisions = []
    planned_roots = set(chosen["roots"])
    for h in sorted(set(targets)):
        for root in roots(h, scope, workspace, home, registry):
            if str(root) in planned_roots:
                continue
            old_path = root / skill.name
            if old_path.exists() or old_path.is_symlink():
                try:
                    old_digest = digest_tree(old_path)
                except UniSkillError:
                    old_digest = "INVALID_TREE"
                collisions.append({"harness": h, "path": str(old_path),
                                   "digest": old_digest,
                                   "same_content": old_digest == digest})
    dests = []
    for root_str in chosen["roots"]:
        dest = Path(root_str) / skill.name
        if not in_tree(dest.parent, workspace if scope == "project" else home):
            raise UniSkillError(f"Destination escapes {'workspace' if scope == 'project' else 'home'}: {dest}")
        if skill.resolve() == dest.resolve(strict=False):
            raise UniSkillError("Source and destination are the same directory")
        if dest.is_symlink() or (dest.exists() and not dest.is_dir()):
            raise UniSkillError(f"Unsafe destination: {dest}")
        old = digest_tree(dest) if dest.exists() else None
        dests.append({"path":str(dest), "existing_digest":old,
                      "action":"unchanged" if old == digest else "replace" if old else "create"})
    return {"skill":skill.name, "source":str(skill.resolve()), "source_digest":digest,
            "scope":scope, "targets":sorted(set(targets)), "destinations":dests,
            "coverage":chosen["coverage"], "duplicate_exposure":chosen["duplicate_exposure"],
            "root_selection":chosen["selection_method"],
            "existing_discovery_collisions":collisions,
            "harness_evidence":{h:{"tier":registry[h].tier,"source":registry[h].source}
                                for h in sorted(set(targets))},
            "warnings":report["warnings"], "mutation":"NONE (dry run)"}

@contextmanager
def locked(state):
    state.mkdir(parents=True, exist_ok=True)
    marker = state / ".lock"
    try:
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise UniSkillError(f"Deployment state locked: {marker}") from exc
    try:
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))
        yield
    finally:
        marker.unlink(missing_ok=True)

def install(p, state, replace=False):
    src, state = Path(p["source"]), Path(state)
    if digest_tree(src) != p["source_digest"]:
        raise UniSkillError("Source changed since planning")
    if any(d["action"] == "replace" for d in p["destinations"]) and not replace:
        raise UniSkillError("Existing divergent skill: --replace required")
    rid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:10]
    receipt = {"receipt_id":rid, "skill":p["skill"], "scope":p["scope"],
               "targets":p["targets"], "source_digest":p["source_digest"],
               "created_at":datetime.now(timezone.utc).isoformat(),
               "state":"INSTALLED", "discovery":"UNVERIFIED", "destinations":[]}
    prepared, modified = [], []
    with locked(state):
        try:
            for i, d in enumerate(p["destinations"]):
                dest = Path(d["path"])
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.is_symlink():
                    raise UniSkillError(f"Destination became symlink: {dest}")
                current = digest_tree(dest) if dest.exists() else None
                if current != d["existing_digest"]:
                    raise UniSkillError(f"Destination changed since preflight: {dest}")
                stage = Path(tempfile.mkdtemp(prefix=".uniskill-stage-", dir=dest.parent))
                shutil.rmtree(stage)
                shutil.copytree(src, stage, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
                if digest_tree(stage) != p["source_digest"]:
                    raise UniSkillError("Staging digest mismatch")
                backup = state / "backups" / rid / str(i)
                prepared.append((dest, stage, backup, current))
            for dest, stage, backup, old in prepared:
                if old is not None:
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(dest, backup)
                modified.append((dest, backup, old))
                os.replace(stage, dest)
                receipt["destinations"].append({"path":str(dest), "digest":p["source_digest"],
                                                 "previous_digest":old,
                                                 "backup":str(backup) if old else None})
            dump_json(state / "receipts" / (rid + ".json"), receipt)
        except Exception:
            for dest, backup, old in reversed(modified):
                if dest.exists():
                    shutil.rmtree(dest)
                if old is not None and backup.exists():
                    os.replace(backup, dest)
            raise
        finally:
            for _, stage, _, _ in prepared:
                if stage.exists():
                    shutil.rmtree(stage)
    return receipt

def read_receipt(state, rid):
    if not rid or any(part in rid for part in ("/", "\\", "..")):
        raise UniSkillError("Invalid receipt ID")
    rec = read_json(Path(state) / "receipts" / (rid + ".json"))
    if not rec:
        raise UniSkillError(f"Receipt not found: {rid}")
    return rec

def verify_receipt(receipt):
    rows = []
    for d in receipt["destinations"]:
        path = Path(d["path"])
        try:
            found = digest_tree(path) if path.exists() else None
        except UniSkillError:
            found = "INVALID_TREE"
        rows.append({"path":str(path), "state":"INSTALLED" if found == d["digest"]
                     else "FAILED", "observed_digest":found})
    return {"receipt_id":receipt["receipt_id"],
            "state":"INSTALLED" if rows and all(x["state"] == "INSTALLED" for x in rows)
            else "FAILED", "destinations":rows}

def rollback(state, rid):
    state = Path(state)
    with locked(state):
        receipt = read_receipt(state, rid)
        if receipt["state"] == "ROLLED_BACK":
            return {"receipt_id":rid, "state":"ROLLED_BACK", "note":"Already rolled back"}
        if verify_receipt(receipt)["state"] != "INSTALLED":
            raise UniSkillError("Installed files were changed; refusing destructive rollback")
        for d in receipt["destinations"]:
            if d["previous_digest"] is not None:
                backup = Path(d["backup"])
                if not backup.exists() or digest_tree(backup) != d["previous_digest"]:
                    raise UniSkillError(f"Missing/changed backup: {backup}")
        for d in reversed(receipt["destinations"]):
            dest = Path(d["path"])
            shutil.rmtree(dest)
            if d["previous_digest"] is not None:
                os.replace(d["backup"], dest)
        receipt["state"] = "ROLLED_BACK"
        dump_json(state / "receipts" / (rid + ".json"), receipt)
    return {"receipt_id":rid, "state":"ROLLED_BACK"}

def latest_receipt(state, skill):
    candidates = []
    for file in (Path(state) / "receipts").glob("*.json"):
        try:
            record = read_json(file)
            if record.get("skill") == skill and record.get("state") != "ROLLED_BACK":
                candidates.append(record)
        except (ValueError, OSError):
            pass
    return max(candidates, key=lambda r:r["created_at"]) if candidates else None