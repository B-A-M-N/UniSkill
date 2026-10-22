"""Harness registry, install-root discovery graph and conservative native probes.

A root mapping is evidence of a possible install location, NOT proof that a
running agent discovers or successfully uses the skill.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .common import UniSkillError, digest_tree, read_json, slug

CRUSH_SOURCE = "https://github.com/charmbracelet/crush#agent-skills"
COMMUNITY_SOURCE = "https://github.com/vercel-labs/skills/blob/main/docs/agents.md"
JCODE_SOURCE = "local repository: /home/bamn/jcode/crates/jcode-base/src/skill.rs"

@dataclass(frozen=True)
class Harness:
    executable: str | None
    project: tuple[str, ...]
    user: tuple[str, ...]
    source: str
    tier: str = "documented"  # local-source, documented, community-mapped, custom, legacy
    native_probe: str | None = None
    # Honest capability classification. INSTALL_ONLY means "we only know where
    # files can be copied". It must never be presented as functional verification.
    # DISCOVERY_PROBED: a bounded native probe exists. NATIVE_DOCUMENTED: vendor
    # documents a native mechanism we do not execute. FUNCTIONAL_VERIFIED: only
    # after a real functional probe (no current harness qualifies).
    support_class: str = "INSTALL_ONLY"

# The six core entries and Crush use vendor documentation. jcode was inspected
# directly in its Rust skill loader; community mappings below are not asserted
# to provide discovery or behavioral guarantees.
HARNESSES: dict[str, Harness] = {
    "claude-code": Harness("claude", (".claude/skills",), (".claude/skills",),
        "https://code.claude.com/docs/en/skills"),
    "codex": Harness("codex", (".agents/skills",), (".agents/skills",),
        "https://developers.openai.com/codex/skills"),
    "opencode": Harness("opencode", (".agents/skills", ".opencode/skills", ".claude/skills"),
        (".agents/skills", ".config/opencode/skills", ".claude/skills"),
        "https://opencode.ai/docs/skills/"),
    "gemini": Harness("gemini", (".agents/skills", ".gemini/skills"),
        (".agents/skills", ".gemini/skills"),
        "https://geminicli.com/docs/cli/skills/", native_probe="gemini-list"),
    "cursor": Harness("cursor", (".agents/skills", ".cursor/skills", ".claude/skills"),
        (".agents/skills", ".cursor/skills", ".claude/skills"),
        "https://cursor.com/docs/context/skills"),
    "copilot": Harness("code", (".agents/skills", ".github/skills", ".claude/skills"),
        (".agents/skills", ".copilot/skills", ".claude/skills"),
        "https://code.visualstudio.com/docs/copilot/customization/agent-skills"),
    "crush": Harness("crush", (".agents/skills", ".crush/skills", ".claude/skills", ".cursor/skills"),
        (".agents/skills", ".config/agents/skills", ".config/crush/skills", ".claude/skills"),
        CRUSH_SOURCE, native_probe="crush-config"),
    "jcode": Harness("jcode", (".jcode/skills", ".agents/skills", ".claude/skills"),
        (".jcode/skills", ".agents/skills"), JCODE_SOURCE, tier="local-source",
        native_probe="jcode-layout"),
}

# Cross-agent installer mappings. These are deliberately tagged community-mapped.
# A successful copy must never be promoted to functional compatibility.
EXTRA_MAPPINGS = {
    "cline": ("cline", ".agents/skills", ".agents/skills"),
    "pi": ("pi", ".agents/skills", ".agents/skills"),
    "kimi-code-cli": ("kimi", ".agents/skills", ".agents/skills"),
    "amp": ("amp", ".agents/skills", ".config/agents/skills"),
    "warp": ("warp", ".agents/skills", ".agents/skills"),
    "zed": ("zed", ".agents/skills", ".agents/skills"),
    "goose": ("goose", ".goose/skills", ".config/goose/skills"),
    "hermes-agent": ("hermes", ".hermes/skills", ".hermes/skills"),
    "qwen-code": ("qwen", ".qwen/skills", ".qwen/skills"),
    "droid": ("droid", ".agents/skills", ".factory/skills"),
    "deepagents": ("deepagents", ".agents/skills", ".deepagents/agent/skills"),
    "mistral-vibe": ("vibe", ".vibe/skills", ".vibe/skills"),
    "openhands": ("openhands", ".openhands/skills", ".openhands/skills"),
    "kilo": ("kilo", ".agents/skills", ".kilo/skills"),
    "kiro-cli": ("kiro-cli", ".kiro/skills", ".kiro/skills"),
    "openclaw": ("openclaw", "skills", ".openclaw/skills"),
    "antigravity": (None, ".agents/skills", ".gemini/antigravity/skills"),
    "aider-desk": (None, ".aider-desk/skills", ".aider-desk/skills"),
    "junie": (None, ".junie/skills", ".junie/skills"),
    "continue": (None, ".continue/skills", ".continue/skills"),
    "windsurf": (None, ".windsurf/skills", ".codeium/windsurf/skills"),
    "roo": (None, ".roo/skills", ".roo/skills"),
}
for name, (binary, project_root, user_root) in EXTRA_MAPPINGS.items():
    HARNESSES[name] = Harness(binary, (project_root,), (user_root,), COMMUNITY_SOURCE,
                             tier="legacy" if name == "roo" else "community-mapped")

# Vendor-documented root corrections (additions to the community mappings;
# adapters.py layers session/trust semantics on top of these).
# Kimi Code CLI also reads project-local .kimi-code/skills and user skills
# under $KIMI_CODE_HOME/skills (default ~/.kimi-code).
# Pi natively reads .pi/skills (project) and .pi/agent/skills (global).
for _name, _proj, _user in (
    ("kimi-code-cli", (".agents/skills", ".kimi-code/skills"),
     (".agents/skills", ".kimi-code/skills")),
    ("pi", (".agents/skills", ".pi/skills"), (".agents/skills", ".pi/agent/skills")),
):
    _h = HARNESSES[_name]
    HARNESSES[_name] = Harness(_h.executable, _proj, _user, _h.source,
                               tier=_h.tier, native_probe=_h.native_probe)


def _validate_root(root: str) -> None:
    """Reject paths able to escape the selected workspace/home prefix."""
    if (not isinstance(root, str) or not root or "\\" in root or ":" in root or "\x00" in root
        or root.startswith("~") or PurePosixPath(root).is_absolute()
        or any(p in ("..", ".") for p in root.split("/"))):
        raise UniSkillError(f"Invalid harness discovery root: {root!r}")


def load_registry(path: Path | None = None) -> dict[str, Harness]:
    """Merge opt-in, user-authored JSON harness profiles with safe relative roots.

    Example: {"harnesses": {"mytool": {"executable": "mytool",
    "project_roots": [".mytool/skills"], "user_roots": [".mytool/skills"]}}}
    """
    registry = dict(HARNESSES)
    if path is None:
        return registry
    try:
        data = read_json(path)
    except (ValueError, OSError) as exc:
        raise UniSkillError(f"Cannot read registry {path}: {exc}") from exc
    entries = data.get("harnesses") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        raise UniSkillError("Registry needs a 'harnesses' mapping")
    for name, obj in entries.items():
        if not slug(name) or not isinstance(obj, dict):
            raise UniSkillError(f"Invalid registry harness entry: {name}")
        project, user = obj.get("project_roots", []), obj.get("user_roots", [])
        for arr in (project, user):
            if not isinstance(arr, list) or len(arr) > 16 or any(not isinstance(s, str) for s in arr):
                raise UniSkillError(f"Invalid root list for {name}")
            for r in arr:
                _validate_root(r)
        executable = obj.get("executable")
        if executable is not None and (not isinstance(executable, str)
                                       or not re.fullmatch(r"[A-Za-z0-9_.-]+", executable)):
            raise UniSkillError(f"Invalid executable for {name}")
        # Force explicit operator acknowledgement before overriding an official
        # mapping; this prevents accidental silent changes to all deployments.
        if name in registry and obj.get("override") is not True:
            raise UniSkillError(f"Custom profile overrides built-in '{name}'; set override: true")
        if not project and not user:
            raise UniSkillError(f"Custom harness {name} needs project_roots or user_roots")
        registry[name] = Harness(executable, tuple(project), tuple(user),
                                 obj.get("source", str(path)), tier="custom",
                                 support_class="INSTALL_ONLY")
    return registry


def detect(registry: dict[str, Harness] | None = None):
    records = []
    for name, h in (registry or HARNESSES).items():
        binary = shutil.which(h.executable) if h.executable else None
        # Do not launch arbitrary custom executables merely to detect them.
        version = None
        if binary and h.tier != "custom":
            try:
                proc = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=5)
                if proc.returncode == 0:
                    version = (proc.stdout or proc.stderr).strip()[:160]
            except (OSError, subprocess.TimeoutExpired):
                pass
        records.append({"harness": name, "binary": binary, "version": version,
                        "tier": h.tier, "source": h.source,
                        "note": "Binary presence does not establish runtime skill support"})
    return records


def roots(harness, scope, workspace, home, registry=None):
    h = (registry or HARNESSES)[harness]
    items = h.project if scope == "project" else h.user
    return [((workspace if scope == "project" else home) / r).resolve(strict=False)
            for r in items]


def choose_roots(targets, scope, workspace, home, registry=None):
    """Set-cover search. Forced roots + bounded search; scalable to many harnesses."""
    registry = registry or HARNESSES
    options: dict[Path, set[str]] = {}
    for h in targets:
        if h not in registry:
            raise UniSkillError(f"Unknown harness: {h}")
        locs = roots(h, scope, workspace, home, registry)
        if not locs:
            raise UniSkillError(f"Harness '{h}' has no {scope} installation roots")
        for root in locs:
            options.setdefault(root, set()).add(h)
    required = set(targets)
    best: tuple[tuple[int, int, int], tuple[Path, ...]] | None = None
    by_target = {h: sorted((p for p, covers in options.items() if h in covers), key=str)
                 for h in targets}

    def visit(covered: frozenset[str], selected: tuple[Path, ...]) -> None:
        nonlocal best
        if best and len(selected) > best[0][0]:
            return
        if required <= covered:
            duplicate_count = sum(max(0, sum(h in options[x] for x in selected) - 1)
                                  for h in required)
            rank = (len(selected), duplicate_count, sum(len(str(x)) for x in selected))
            if best is None or rank < best[0]:
                best = rank, selected
            return
        uncovered = required - set(covered)
        next_h = min(uncovered, key=lambda h: len(by_target[h]))
        for p in by_target[next_h]:
            if p in selected:
                continue
            visit(frozenset(set(covered) | options[p]), selected + (p,))

    # A large set of unrelated paths can be solved greedily without pretending
    # that the heuristic is an exact optimization. Small sets use exact search.
    if len(targets) > 22:
        covered, selected = set(), []
        while covered != required:
            p = max(options, key=lambda r: (len(options[r] - covered), -len(str(r))))
            if not options[p] - covered:
                raise UniSkillError("Cannot cover target harnesses")
            selected.append(p)
            covered |= options[p]
        chosen = tuple(selected)
        optimality = "GREEDY"
    else:
        visit(frozenset(), tuple())
        if best is None:
            raise UniSkillError("No documented discovery root")
        chosen, optimality = best[1], "MINIMUM_COPIES_EXACT"
    coverage = {h: [str(x) for x in chosen if h in options[x]] for h in targets}
    return {"roots": [str(x) for x in chosen], "coverage": coverage,
            "selection_method": optimality,
            "duplicate_exposure": {h: len(v) for h, v in coverage.items() if len(v) > 1}}


def capability_profile(name, registry=None):
    h = (registry or HARNESSES)[name]
    # The authoritative support classification lives in adapters.py
    # (SUPPORT_CLASS_OVERRIDES + custom-tier handling), not in the dataclass
    # field `h.support_class` (which is never populated for built-ins). Delegate
    # so the CLI `capabilities`/`harnesses` commands and adapters cannot drift
    # apart. Imported lazily because adapters.py imports this module at top level.
    from . import adapters
    support_class = adapters.support_class(name, registry)
    return {"harness":name, "tier":h.tier, "source":h.source,
            "support_class": support_class,
            "project_roots":list(h.project), "user_roots":list(h.user),
            "file_discovery":"MAPPED_NOT_VERIFIED" if h.tier in {"custom","legacy","community-mapped"}
                             else "SOURCE_DOCUMENTED",
            "native_discovery_probe":"AVAILABLE" if h.native_probe == "gemini-list" else "UNVERIFIED",
            "explicit_invocation":"UNVERIFIED", "automatic_selection":"UNVERIFIED",
            "functional_runner":"EXTERNAL_RUNNER_REQUIRED", "policy_modification":"NEVER_AUTOMATIC"}


def opencode_diagnostics(name, workspace, home):
    notes = []
    for path in (workspace / "opencode.json", workspace / ".opencode/opencode.json",
                 home / ".config/opencode/opencode.json"):
        if not path.exists():
            continue
        try:
            cfg = read_json(path)
        except (json.JSONDecodeError, OSError) as exc:
            notes.append(f"Cannot inspect {path}: {exc}")
            continue
        if not isinstance(cfg, dict):
            continue
        permission = (cfg.get("permission") or {}).get("skill", {})
        if isinstance(permission, str) and permission in {"deny", "ask"}:
            notes.append(f"{path}: global skill permission {permission}")
        if isinstance(permission, dict):
            for pattern, decision in permission.items():
                if fnmatch.fnmatchcase(name, pattern) and decision in {"deny", "ask"}:
                    notes.append(f"{path}: matching skill permission {pattern}={decision} (review precedence)")
        if (cfg.get("tools") or {}).get("skill") is False:
            notes.append(f"{path}: skill tool disabled")
        for agent_name, agent in (cfg.get("agent") or {}).items():
            if isinstance(agent, dict) and (agent.get("tools") or {}).get("skill") is False:
                notes.append(f"{path}: {agent_name} agent disables skill tool")
    return notes


def crush_diagnostics(name, workspace, home):
    """Best-effort static inspection; never mutate the user's Crush policies."""
    notes = []
    for path in (workspace / "crush.json", workspace / ".crush/crush.json",
                 home / ".config/crush/crush.json"):
        if not path.is_file():
            continue
        try:
            cfg = read_json(path)
        except (ValueError, OSError):
            notes.append(f"{path}: unable to parse config (Crush also accepts crushrc)")
            continue
        if not isinstance(cfg, dict):
            continue
        options = cfg.get("options", {})
        if isinstance(options, dict):
            disabled = options.get("disabled_skills", [])
            if name in disabled or "*" in disabled:
                notes.append(f"{path}: skill disabled via options.disabled_skills")
    # Current Crush also accepts a line-oriented crushrc configuration. Parse
    # only the known explicit disable pattern; a positive match is diagnostic,
    # not permission to edit the source configuration.
    for path in (workspace / "crushrc", workspace / ".crush/crushrc",
                 home / ".config/crush/crushrc"):
        if not path.is_file():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                m = re.match(r"^\s*(?:option\s+)?disable-skill\s+(\S+)", line)
                if m and m.group(1) in {name, "*"}:
                    notes.append(f"{path}: disabled by crushrc directive")
        except (UnicodeError, OSError) as exc:
            notes.append(f"{path}: cannot inspect crushrc: {exc}")
    if os.environ.get("CRUSH_SKILLS_DIR"):
        notes.append("CRUSH_SKILLS_DIR is set; static default root may not be active. "
                     "Review configured path or specify a custom registry profile")
    return notes


def native_discovery(name, skill, cwd):
    """Never infer discovery from a file copy or a binary's mere presence."""
    if name != "gemini":
        return {"state":"UNVERIFIED", "reason":"Native discovery probe not implemented"}
    if not shutil.which("gemini"):
        return {"state":"UNVERIFIED", "reason":"Gemini CLI not found"}
    try:
        proc = subprocess.run(["gemini", "skills", "list", "--all"], cwd=cwd,
                              capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"state":"UNVERIFIED", "reason":str(exc)}
    if proc.returncode:
        return {"state":"UNVERIFIED", "reason":"Native command failed",
                "exit_code":proc.returncode, "stderr_tail":proc.stderr[-300:]}
    pattern = re.compile(r"(?<![\w-])" + re.escape(skill) + r"(?![\w-])")
    matched = [line.strip() for line in proc.stdout.splitlines() if pattern.search(line)]
    return ({"state":"DISCOVERED", "matched_lines":matched[:4],
             "note":"Human-readable list; cannot certify enabled state or invocation"} if matched
            else {"state":"FAILED", "reason":"Absent from native Gemini list"})


def inspect_harness_skill(name, harness, scope, workspace, home, registry=None):
    """Nonexecuting discovery-root diagnostic for skills installed by any tool.

    Filesystem presence is reported as FILES_PRESENT, never DISCOVERED. An
    invalid candidate can explain failures without touching user policies.
    """
    from .validator import validate_skill
    found = []
    for root in roots(harness, scope, workspace, home, registry):
        path = root / name
        if not (path.exists() or path.is_symlink()):
            continue
        validation = validate_skill(path)
        found.append({"path":str(path), "state":validation["state"],
                      "digest":digest_tree(path) if validation["state"] == "VALID" else None,
                      "errors":validation["errors"]})
    unique = {f["digest"] for f in found if f["digest"]}
    policy = []
    if harness == "opencode":
        policy = opencode_diagnostics(name,workspace,home)
    if harness == "crush":
        policy = crush_diagnostics(name,workspace,home)
    return {"harness":harness, "state":"FILES_PRESENT" if found else "ABSENT",
            "candidates":found, "conflict":len(unique) > 1,
            "policy_diagnostics":policy,
            "discovery":"UNVERIFIED", "invocation":"UNVERIFIED", "functional":"UNVERIFIED",
            "note":"Files and configuration inspected; native runtime not executed"}
