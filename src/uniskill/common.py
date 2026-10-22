"""Small shared safe filesystem helpers."""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
from pathlib import Path

class UniSkillError(Exception):
    pass

def dump_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".uniskill-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def read_json(path, default=None):
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else default

def slug(value):
    return isinstance(value, str) and len(value) <= 64 and bool(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value))

def safe_tree(root):
    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        raise UniSkillError(f"Not a normal directory: {root}")
    result = []
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in {".git", "__pycache__"})
        for d in dirs:
            if (Path(base) / d).is_symlink():
                raise UniSkillError(f"Symlinked directory refused: {Path(base) / d}")
        for f in sorted(files):
            p = Path(base) / f
            if p.is_symlink() or not p.is_file():
                raise UniSkillError(f"Symlink or special file refused: {p}")
            if p.stat().st_size > 10 * 1024 * 1024:
                raise UniSkillError(f"File too large (>10 MiB): {p}")
            if p.suffix not in {".pyc", ".pyo"}:
                result.append(p)
    return result

def digest_tree(root):
    root = Path(root)
    h = hashlib.sha256()
    for p in safe_tree(root):
        name = p.relative_to(root).as_posix().encode()
        data = p.read_bytes()
        h.update(len(name).to_bytes(4,"big") + name)
        h.update(len(data).to_bytes(8,"big") + data)
    return h.hexdigest()

def in_tree(path, parent):
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False


def canonical_bytes(data):
    """Deterministic UTF-8 JSON encoding used for HMAC signing."""
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


def load_signing_key(state):
    """Return a state-local HMAC key, creating it once (0600, never overwritten).

    The secret lives in state/secret.key and is elevated to a 32-byte HMAC key
    via SHA-256. A missing or unreadable secret must never silently approve a
    receipt, so this fails closed.
    """
    state = Path(state)
    secret = state / "secret.key"
    if not secret.exists():
        secret.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(secret, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass  # raced with another creator; read the existing secret below
        else:
            try:
                os.write(fd, secrets.token_bytes(32))
            finally:
                os.close(fd)
    try:
        data = secret.read_bytes()
    except OSError as exc:
        raise UniSkillError(f"Cannot read deployment secret {secret}: {exc}") from exc
    if not data or len(data) < 32:
        raise UniSkillError(f"Deployment secret malformed or empty: {secret}")
    return hashlib.sha256(data).digest()


def sign_receipt_body(key, body):
    """HMAC-SHA256 over canonical JSON; returns hex digest."""
    return hmac.new(key, canonical_bytes(body), hashlib.sha256).hexdigest()


def verify_receipt_signature(key, body, sig_hex):
    """Constant-time signature check. Fails closed on missing/malformed input."""
    if not isinstance(sig_hex, str) or not sig_hex:
        return False
    want = hmac.new(key, canonical_bytes(body), hashlib.sha256).hexdigest()
    try:
        return hmac.compare_digest(want, sig_hex)
    except TypeError:
        return False


def assert_no_symlink_chain(anchor, leaf_child):
    """Refuse if any component between anchor and leaf_child parent (inclusive)
    is a symlink. Uses lstat-based checks so a mid-chain symlink swap is caught
    instead of being silently followed."""
    anchor, leaf_child = Path(anchor), Path(leaf_child)
    current = anchor
    rel = os.path.relpath(leaf_child, anchor)
    if not rel or rel == ".":
        return
    for part in Path(rel).parts:
        if part == "..":
            raise UniSkillError(f"Deployment/rollback path escapes anchor: {leaf_child}")
        current = current / part
        if current.is_symlink() or (current.exists() and os.path.islink(current)):
            raise UniSkillError(f"Symlink in deployment/rollback path: {current}")
        if current.exists() and not current.is_dir():
            raise UniSkillError(f"Non-directory in deployment/rollback path: {current}")
