"""Small shared safe filesystem helpers."""
from __future__ import annotations
import hashlib
import json
import os
import re
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
