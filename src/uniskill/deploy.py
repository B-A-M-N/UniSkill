"""Dry-run plans, transactional installations, evidence receipts, safe rollback."""
from __future__ import annotations
import os
import shutil
import stat
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from .common import (UniSkillError, assert_no_symlink_chain,
                     digest_tree, dump_json, in_tree, load_signing_key,
                     read_json, sign_receipt_body, verify_receipt_signature)
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
    anchor = (workspace if scope == "project" else home).resolve()
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
    deployment = []
    for root_str in chosen["roots"]:
        dest = Path(root_str) / skill.name
        if not in_tree(dest.parent, workspace if scope == "project" else home):
            raise UniSkillError(f"Destination escapes {'workspace' if scope == 'project' else 'home'}: {dest}")
        if skill.resolve() == dest.resolve(strict=False):
            raise UniSkillError("Source and destination are the same directory")
        if dest.is_symlink() or (dest.exists() and not dest.is_dir()):
            raise UniSkillError(f"Unsafe destination: {dest}")
        old = digest_tree(dest) if dest.exists() else None
        dests.append({"path": str(dest), "existing_digest": old,
                      "action": "unchanged" if old == digest else "replace" if old else "create"})
        chain = []
        cur = anchor
        rel = None
        try:
            rel = dest.parent.relative_to(anchor)
        except ValueError:
            raise UniSkillError(f"Destination not under scope anchor: {dest}")
        for part in rel.parts:
            cur = cur / part
            if cur.exists():
                st = os.lstat(cur)
                chain.append({"name": part, "dev": st.st_dev, "ino": st.st_ino, "existed": True})
            else:
                chain.append({"name": part, "dev": None, "ino": None, "existed": False})
        deployment.append({"path": str(dest), "anchor": str(anchor), "chain": chain,
                           "root": str(dest.parent)})
    return {"skill": skill.name, "source": str(skill.resolve()), "source_digest": digest,
            "scope": scope, "targets": sorted(set(targets)), "destinations": dests,
            "coverage": chosen["coverage"], "duplicate_exposure": chosen["duplicate_exposure"],
            "root_selection": chosen["selection_method"],
            "deployment": deployment, "scope_anchor": str(anchor),
            "existing_discovery_collisions": collisions,
            "harness_evidence": {h: {"tier": registry[h].tier, "source": registry[h].source}
                                 for h in sorted(set(targets))},
            "warnings": report["warnings"], "mutation": "NONE (dry run)"}


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


def _ensure_deployment_parents(anchor, chain):
    """Re-establish the deployment trust chain at apply time.

    From the scope anchor down to the destination's parent: each component must
    exist (or be created step by step), never be a symlink, never be a regular
    file, and every component that already existed at plan time must keep its
    (st_dev, st_ino). Returns the list of directories this call created."""
    anchor = Path(anchor)
    created = []
    if not anchor.exists():
        anchor.mkdir(parents=True, exist_ok=True)
        created.append(anchor)
    if anchor.is_symlink():
        raise UniSkillError(f"Scope anchor is a symlink: {anchor}")
    if not anchor.is_dir():
        raise UniSkillError(f"Scope anchor is not a directory: {anchor}")
    current = anchor
    for comp in chain:
        nxt = current / comp["name"]
        if nxt.is_symlink():
            raise UniSkillError(f"Symlink in deployment path: {nxt}")
        if nxt.exists():
            st = os.lstat(nxt)
            if stat.S_ISLNK(st.st_mode):
                raise UniSkillError(f"Symlink in deployment path: {nxt}")
            if not stat.S_ISDIR(st.st_mode):
                raise UniSkillError(f"Non-directory in deployment path: {nxt}")
            if comp["existed"] and (st.st_dev != comp["dev"] or st.st_ino != comp["ino"]):
                raise UniSkillError(f"Deployment path changed since planning: {nxt}")
        else:
            if comp["existed"]:
                raise UniSkillError(f"Deployment path removed since planning: {nxt}")
            nxt.mkdir()
            created.append(nxt)
            if nxt.is_symlink():
                raise UniSkillError(f"Symlink appeared in deployment path: {nxt}")
        current = nxt
    return created


def _cleanup_created_empty(created):
    for d in sorted(created, key=lambda p: len(p.parts), reverse=True):
        try:
            if d.exists() and d.is_dir() and not d.is_symlink():
                d.rmdir()
        except OSError:
            pass


def _receipt_body(rec):
    return {k: rec[k] for k in ("receipt_id", "skill", "scope", "targets",
                                "source_digest", "created_at", "state", "destinations")}


def _read_ledger(state):
    return read_json(Path(state) / "ownership.json", default={}) or {}


def _write_ledger(state, ledger):
    dump_json(Path(state) / "ownership.json", ledger)


def _dest_owned_by(ledger, path):
    entry = ledger.get(path)
    return entry.get("receipt_id") if isinstance(entry, dict) else None


def install(p, state, replace=False):
    src, state = Path(p["source"]), Path(state)
    if digest_tree(src) != p["source_digest"]:
        raise UniSkillError("Source changed since planning")
    if any(d["action"] == "replace" for d in p["destinations"]) and not replace:
        raise UniSkillError("Existing divergent skill: --replace required")
    rid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:10]
    created_at = datetime.now(timezone.utc).isoformat()
    actions = {d["action"] for d in p["destinations"]}
    state_field = "INSTALLED" if (actions & {"create", "replace"}) else "UNCHANGED"
    receipt = {"receipt_id": rid, "skill": p["skill"], "scope": p["scope"],
               "targets": p["targets"], "source_digest": p["source_digest"],
               "created_at": created_at, "state": state_field,
               "destinations": []}
    deployment = {d["path"]: d for d in p.get("deployment", [])}
    prepared, modified, created_all = [], [], []
    with locked(state):
        try:
            ledger = _read_ledger(state)
            for i, d in enumerate(p["destinations"]):
                dest = Path(d["path"])
                binding = deployment.get(str(dest))
                if not binding:
                    raise UniSkillError(f"Plan lacks deployment binding for: {dest}")
                anchor = Path(binding["anchor"])
                created_all += _ensure_deployment_parents(binding["anchor"], binding["chain"])
                if not in_tree(dest, anchor):
                    raise UniSkillError(f"Destination escapes scope anchor: {dest}")
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
                owner = _dest_owned_by(ledger, str(dest))
                supersedes = owner if owner and owner != rid else None
                prepared.append((dest, stage, backup, current, supersedes,
                                 binding["anchor"], str(binding["root"])))
            # Re-verify the full deployment chain immediately before publish.
            for dest, stage, backup, old, supersedes, anchor, root in prepared:
                created_all += _ensure_deployment_parents(anchor, deployment[str(dest)]["chain"])
                if not in_tree(dest, anchor) or in_tree(dest, src):
                    raise UniSkillError(f"Unsafe destination before publish: {dest}")
            for dest, stage, backup, old, supersedes, anchor, root in prepared:
                if old is not None:
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(dest, backup)
                modified.append((dest, backup, old))
                os.replace(stage, dest)
                receipt["destinations"].append({
                    "path": str(dest), "digest": p["source_digest"],
                    "previous_digest": old, "backup": str(backup) if old else None,
                    "action": "unchanged" if old == p["source_digest"] else ("replace" if old else "create"),
                    "anchor": anchor, "root": root,
                    "supersedes": supersedes if supersedes else None})
            # Post-condition: every destination must hold the source digest.
            for dest, stage, backup, old, supersedes, anchor, root in prepared:
                if digest_tree(dest) != p["source_digest"]:
                    raise UniSkillError(f"Post-install digest mismatch: {dest}")
            # Mint receipt and claim ownership.
            for entry in receipt["destinations"]:
                ledger[str(entry["path"])] = {
                    "receipt_id": rid, "digest": p["source_digest"],
                    "timestamp": created_at, "superseded_by": None}
            _write_ledger(state, ledger)
            key = load_signing_key(state)
            receipt["signature"] = sign_receipt_body(key, _receipt_body(receipt))
            dump_json(state / "receipts" / (rid + ".json"), receipt)
        except Exception:
            for dest, backup, old in reversed(modified):
                if dest.exists():
                    shutil.rmtree(dest)
                if old is not None and backup.exists():
                    os.replace(backup, dest)
            _cleanup_created_empty(created_all)
            raise
        finally:
            for _, stage, _, _, _, _, _ in prepared:
                if stage.exists():
                    shutil.rmtree(stage)
    return receipt


def read_receipt(state, rid):
    if not rid or any(part in rid for part in ("/", "\\", "..")):
        raise UniSkillError("Invalid receipt ID")
    state = Path(state)
    rec = read_json(state / "receipts" / (rid + ".json"))
    if not rec:
        raise UniSkillError(f"Receipt not found: {rid}")
    key = load_signing_key(state)
    if not verify_receipt_signature(key, _receipt_body(rec), rec.get("signature")):
        raise UniSkillError(f"Receipt signature invalid or tampered: {rid}")
    return rec


def verify_receipt(receipt, state=None):
    if state is not None:
        key = load_signing_key(state)
        if not verify_receipt_signature(key, _receipt_body(receipt), receipt.get("signature")):
            raise UniSkillError(f"Receipt signature invalid or tampered: {receipt.get('receipt_id')}")
    rows = []
    for d in receipt["destinations"]:
        path = Path(d["path"])
        try:
            found = digest_tree(path) if path.exists() else None
        except UniSkillError:
            found = "INVALID_TREE"
        rows.append({"path": str(path), "state": "INSTALLED" if found == d["digest"]
                     else "FAILED", "observed_digest": found})
    return {"receipt_id": receipt["receipt_id"],
            "state": "INSTALLED" if rows and all(x["state"] == "INSTALLED" for x in rows)
            else "FAILED", "destinations": rows}


def _validate_receipt_dest(state, rid, d):
    dest = Path(d["path"])
    anchor = d.get("anchor")
    root = d.get("root")
    if not anchor or not root:
        raise UniSkillError(f"Receipt lacks anchor/root binding: {rid}")
    anchor, root = Path(anchor), Path(root)
    if not in_tree(dest, root):
        raise UniSkillError(f"Receipt destination outside its install root: {dest}")
    if not in_tree(dest, anchor):
        raise UniSkillError(f"Receipt destination escapes scope anchor: {dest}")
    assert_no_symlink_chain(anchor, dest.parent)
    backup = d.get("backup")
    if backup:
        b = Path(backup)
        bstate = Path(state) / "backups" / rid
        if not in_tree(b, bstate):
            raise UniSkillError(f"Receipt backup escapes state backups: {backup}")


def _dest_matches_receipt(rec, dest_path):
    for d in rec["destinations"]:
        if str(Path(d["path"])) == str(Path(dest_path)):
            try:
                return digest_tree(dest_path) == d["digest"]
            except (UniSkillError, OSError):
                return False
    return False


def _release_ownership(state, ledger, receipt, rid):
    for d in receipt["destinations"]:
        dp = d["path"]
        entry = ledger.get(dp)
        if not (isinstance(entry, dict) and entry.get("receipt_id") == rid):
            continue
        pred = d.get("supersedes") or receipt.get("supersedes")
        if pred:
            try:
                recp = read_receipt(state, pred)
                eligible = (recp.get("state") != "ROLLED_BACK"
                            and _dest_matches_receipt(recp, dp))
            except UniSkillError:
                eligible = False
            if eligible:
                ledger[dp] = {"receipt_id": pred, "digest": recp["source_digest"],
                              "timestamp": recp["created_at"], "superseded_by": None}
                continue
        ledger.pop(dp, None)
    _write_ledger(state, ledger)


def rollback(state, rid):
    state = Path(state)
    with locked(state):
        receipt = read_receipt(state, rid)
        if receipt["state"] == "ROLLED_BACK":
            return {"receipt_id": rid, "state": "ROLLED_BACK", "note": "Already rolled back"}
        ledger = _read_ledger(state)
        for d in receipt["destinations"]:
            _validate_receipt_dest(state, rid, d)
            owner = _dest_owned_by(ledger, d["path"])
            if owner and owner != rid:
                raise UniSkillError(
                    f"Destination {d['path']} is owned by active receipt {owner}; "
                    f"roll back the newest receipt first")
        if verify_receipt(receipt, state)["state"] != "INSTALLED":
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
        # Re-verify the restored state and report what actually happened.
        failures = []
        for d in receipt["destinations"]:
            dest = Path(d["path"])
            if d["previous_digest"] is None:
                if dest.exists() or dest.is_symlink():
                    failures.append(str(dest))
            elif digest_tree(dest) != d["previous_digest"]:
                failures.append(str(dest))
        if failures:
            raise UniSkillError("Rollback failed to restore: " + ", ".join(failures))
        _release_ownership(state, ledger, receipt, rid)
        receipt["state"] = "ROLLED_BACK"
        receipt["signature"] = sign_receipt_body(load_signing_key(state), _receipt_body(receipt))
        dump_json(state / "receipts" / (rid + ".json"), receipt)
    return {"receipt_id": rid, "state": "ROLLED_BACK"}


def latest_receipt(state, skill):
    candidates = []
    state = Path(state)
    for file in (state / "receipts").glob("*.json"):
        try:
            record = read_receipt(state, file.stem)
            if record.get("skill") == skill and record.get("state") != "ROLLED_BACK":
                candidates.append(record)
        except (UniSkillError, ValueError, OSError):
            pass
    return max(candidates, key=lambda r: r["created_at"]) if candidates else None


def ownership(state, skill=None):
    """Return the current deployment ledger owner (or empty dict if none)."""
    ledger = _read_ledger(state)
    if not ledger:
        return {}
    result = {}
    for path, entry in ledger.items():
        if not isinstance(entry, dict) or not entry.get("receipt_id"):
            continue
        if skill is not None:
            receivers = (Path(state) / "receipts")
            rec_file = receivers / (entry["receipt_id"] + ".json")
            if not rec_file.exists():
                continue
            rec = read_json(rec_file)
            if not rec or rec.get("skill") != skill:
                continue
        result[path] = {
            "receipt_id": entry["receipt_id"], "digest": entry.get("digest"),
            "timestamp": entry.get("timestamp"), "superseded_by": entry.get("superseded_by")}
    return result