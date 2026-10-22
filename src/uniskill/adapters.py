"""Capability-aware per-harness adapters.

Layered on top of harnesses.py WITHOUT changing its pinned behavior. Every
operation returns a structured result whose explicit evidence state must never
overclaim: filesystem presence is not discovery, a binary is not a runtime, and
a profile that only knows copy targets is INSTALL_ONLY.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from .common import UniSkillError
from .harnesses import HARNESSES, Harness, load_registry, roots, crush_diagnostics

# Evidence states
UNVERIFIED = "UNVERIFIED"
DISCOVERED = "DISCOVERED"
FAILED = "FAILED"

SUPPORT_CLASSES = ("INSTALL_ONLY", "DISCOVERY_PROBED", "NATIVE_DOCUMENTED",
                   "FUNCTIONAL_VERIFIED")

# Per-harness support classes. Everything not listed defaults to INSTALL_ONLY.
# DISCOVERY_PROBED: native_discovery implements a bounded, opt-in probe.
# NATIVE_DOCUMENTED: vendor documents a native mechanism we deliberately do not
# execute (no functional probe exists, so this is NOT functional verification).
SUPPORT_CLASS_OVERRIDES = {
    "gemini": "DISCOVERY_PROBED",      # gemini skills list probe, opt-in
    "jcode": "INSTALL_ONLY",           # layout inspected in source; no live probe
    "crush": "INSTALL_ONLY",           # config inspection only; daemon untouched
    "kimi-code-cli": "NATIVE_DOCUMENTED",  # documented /skill:name, not executed
    "hermes-agent": "INSTALL_ONLY",    # trust/quarantine gate we cannot verify
    "pi": "INSTALL_ONLY",
}

# Extended frontmatter fields some harnesses accept (documented; adding them to
# a harness that ignores them is harmless, but we only emit where documented).
EXTENDED_FRONTMATTER = {
    "kimi-code-cli": ["name", "description", "version", "metadata"],
    "claude-code": ["name", "description", "metadata"],
}

_KIMI_CODE_HOME_DEFAULT = ".kimi-code"


def _registry(registry=None) -> dict[str, Harness]:
    return registry or HARNESSES


def support_class(name, registry=None) -> str:
    h = _registry(registry)[name]
    if h.tier == "custom":
        return "INSTALL_ONLY"
    return SUPPORT_CLASS_OVERRIDES.get(name, "INSTALL_ONLY")


class AdapterResult(dict):
    """dict with required evidence-state keys, so callers can never forget them."""
    def __init__(self, state: str, reason: str | None = None, **extra):
        super().__init__(state=state, reason=reason, **extra)


class HarnessAdapter:
    """Base adapter: honest INSTALL_ONLY behavior for every operation."""

    harness_name: str = ""

    def __init__(self, registry: dict[str, Harness] | None = None,
                 name: str | None = None):
        if name:
            self.harness_name = name
        self.registry = registry or HARNESSES
        if self.harness_name and self.harness_name not in self.registry:
            raise UniSkillError(f"Unknown harness: {self.harness_name}")

    @property
    def spec(self) -> Harness:
        return self.registry[self.harness_name]

    # -- detect ------------------------------------------------------------
    def detect(self) -> AdapterResult:
        """Identify real executable + version WITHOUT launching custom profiles."""
        exe = self.spec.executable
        binary = shutil.which(exe) if exe else None
        version = None
        if binary and self.spec.tier != "custom":
            # Only built-in, non-custom binaries are probed, with a hard timeout.
            import subprocess
            try:
                proc = subprocess.run([binary, "--version"], capture_output=True,
                                      text=True, timeout=5)
                if proc.returncode == 0:
                    version = (proc.stdout or proc.stderr).strip()[:160]
            except (OSError, subprocess.TimeoutExpired):
                version = None
        return AdapterResult(
            UNVERIFIED if not binary else "PRESENT",
            None if binary else f"Executable {exe!r} not found on PATH",
            binary=binary, version=version,
            note="Binary presence does not establish runtime skill support")

    # -- inspect -----------------------------------------------------------
    def inspect(self, workspace: Path, home: Path) -> AdapterResult:
        """Read-only effective configuration; never mutates user policy."""
        return AdapterResult(UNVERIFIED,
                             "No harness-specific configuration inspection implemented",
                             restrictions=[], read_only=True)

    # -- capabilities ------------------------------------------------------
    def capabilities(self, scope: str, workspace: Path, home: Path) -> AdapterResult:
        cls = support_class(self.harness_name, self.registry)
        return AdapterResult(
            UNVERIFIED,
            "No runtime capability probe exists for this harness",
            support_class=cls, scope=scope,
            capabilities=["file_copy"] if cls == "INSTALL_ONLY" else [])

    # -- roots ---------------------------------------------------------------
    def roots(self, scope: str, workspace: Path, home: Path) -> list[Path]:
        """Effective discovery paths in precedence order."""
        return roots(self.harness_name, scope, workspace, home, self.registry)

    # -- adapt ---------------------------------------------------------------
    def adapt(self, skill_dir: Path) -> AdapterResult:
        """Harness-specific packaging. Must never invent capability."""
        fields = EXTENDED_FRONTMATTER.get(self.harness_name)
        if not fields:
            return AdapterResult(
                UNVERIFIED,
                "This harness is not documented to require packaging differences "
                "from the canonical skill format",
                packaging="none")
        return AdapterResult(
            UNVERIFIED,
            "Documented frontmatter fields identified; no transform applied",
            packaging="frontmatter_fields", fields=fields)

    # -- discover ------------------------------------------------------------
    def discover(self, skill: str, workspace: Path, home: Path,
                 cwd: Path | None = None, probe: bool = False) -> AdapterResult:
        """Prove the RUNTIME sees the skill. Opt-in probe only."""
        from .harnesses import native_discovery
        if not probe or self.spec.native_probe is None:
            return AdapterResult(
                UNVERIFIED,
                "No native discovery probe executed; filesystem presence is not discovery")
        result = native_discovery(self.harness_name, skill, cwd or workspace)
        return AdapterResult(result["state"], result.get("reason"), **{
            k: v for k, v in result.items() if k not in {"state", "reason"}})

    # -- invoke --------------------------------------------------------------
    def invoke(self, skill: str) -> AdapterResult:
        """Explicit activation, only where a native mechanism is documented."""
        return AdapterResult(
            UNVERIFIED,
            "No native invocation mechanism documented for this harness")

    # -- observe_selection -----------------------------------------------------
    def observe_selection(self, skill: str, workspace: Path) -> AdapterResult:
        """Observe model-initiated selection, including negatives."""
        return AdapterResult(
            UNVERIFIED,
            "Automatic (model-initiated) selection cannot be observed statically")

    # -- run_case --------------------------------------------------------------
    def run_case(self, case: dict, runner: dict) -> AdapterResult:
        """Functional acceptance test. Requires an explicitly configured runner."""
        if not runner or not runner.get("argv"):
            return AdapterResult(
                FAILED,
                "Functional cases require an explicitly configured external runner; "
                "no implicit execution")
        return AdapterResult(
            UNVERIFIED,
            "This adapter does not interpret runner output; use evaluation.run_evals")

    # -- diagnose ---------------------------------------------------------------
    def diagnose(self, skill: str, workspace: Path, home: Path) -> AdapterResult:
        """Earliest failing stage, as far as read-only inspection can tell."""
        stages = []
        detected = self.detect()
        if detected["state"] != "PRESENT":
            stages.append({"stage": "detect", "state": detected["state"],
                           "reason": detected["reason"]})
        else:
            found = False
            for scope in ("project", "user"):
                for root in self.roots(scope, workspace, home):
                    if (root / skill).exists() or (root / skill).is_symlink():
                        found = True
                        break
                if found:
                    break
            stages.append({"stage": "install", "state": "FILES_PRESENT" if found else "ABSENT",
                           "reason": None if found else
                           f"Skill not found under any {self.harness_name} root"})
            stages.append({"stage": "discover", "state": UNVERIFIED,
                           "reason": "Native discovery not probed (read-only diagnosis)"})
        return AdapterResult(
            UNVERIFIED,
            "Diagnosis is read-only; final stage is UNVERIFIED without a native probe",
            stages=stages, read_only=True)


class JcodeAdapter(HarnessAdapter):
    """jcode resolves its skill registry against each ACTIVE SESSION'S WORKSPACE.

    A probe must run WITHOUT mutating its shared daemon, so no live daemon check
    is performed here at all; everything is static and read-only.
    """

    harness_name = "jcode"

    def roots(self, scope, workspace, home):
        # Workspace-relative first (session-scoped), then home-relative. jcode
        # resolves these against the active session's workspace, so the returned
        # paths are only valid relative to the session we were told about.
        if scope == "project":
            ordered = [".jcode/skills", ".agents/skills", ".claude/skills"]
            return [(Path(workspace) / r).resolve(strict=False) for r in ordered]
        ordered = [".jcode/skills", ".agents/skills"]
        return [(Path(home) / r).resolve(strict=False) for r in ordered]

    def detect(self):
        result = super().detect()
        return AdapterResult(result["state"], result["reason"],
                             binary=result["binary"], version=result["version"],
                             daemon_check="NOT_PERFORMED",
                             note="jcode shares a daemon; no live check was "
                                  "performed to avoid mutating session state")

    def discover(self, skill, workspace, home, cwd=None, probe=False):
        return AdapterResult(
            UNVERIFIED,
            "jcode discovery would require querying its shared daemon; a live "
            "daemon check was not performed (read-only policy). Filesystem "
            "presence is not discovery.")

    def diagnose(self, skill, workspace, home):
        result = super().diagnose(skill, workspace, home)
        result["note"] = ("Static, read-only diagnosis. jcode's skill registry is "
                          "resolved against the active session's workspace and no "
                          "live daemon check was performed.")
        result["session_scoped"] = True
        return result


class CrushAdapter(HarnessAdapter):
    """Extends crush_diagnostics: crushrc disable directives, manual-only vs
    model-invocable metadata, and effective-path overrides (CRUSH_SKILLS_DIR)."""

    harness_name = "crush"

    def inspect(self, workspace, home):
        # Reuse and extend the existing read-only diagnostics for the generic
        # "*" question; the per-skill variant is in skill_metadata().
        base = crush_diagnostics("*", workspace, home)
        overrides = []
        if os.environ.get("CRUSH_SKILLS_DIR"):
            overrides.append({"kind": "env", "name": "CRUSH_SKILLS_DIR",
                              "value": os.environ["CRUSH_SKILLS_DIR"],
                              "note": "Effective skill path override; static "
                                      "default roots may not be active"})
        return AdapterResult(UNVERIFIED,
                             "Static inspection only; runtime restrictions not proven",
                             restrictions=base, read_only=True,
                             effective_path_overrides=overrides)

    def skill_metadata(self, skill: str, workspace: Path, home: Path) -> AdapterResult:
        """Distinguish MANUAL-ONLY vs MODEL-INVOCABLE metadata for a skill.

        Crush docs: a skill that is disabled is neither; a skill whose metadata
        marks it manual-only is excluded from model invocation.
        """
        policy = crush_diagnostics(skill, workspace, home)
        disabled = bool(policy)
        # Manual-only marker: Crush honors a `manual-only: true` frontmatter
        # field (and crushrc `disable-skill-model` directives).
        manual_only = False
        for scope in ("project", "user"):
            for root in roots("crush", scope, workspace, home):
                path = root / skill / "SKILL.md"
                if not path.is_file():
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if "manual-only: true" in text or "manual_only: true" in text:
                    manual_only = True
        for path in (workspace / "crushrc", workspace / ".crush/crushrc",
                     home / ".config/crush/crushrc"):
            if not path.is_file():
                continue
            try:
                import re
                for line in path.read_text(encoding="utf-8").splitlines():
                    if re.match(r"^\s*(?:option\s+)?disable-skill-model\s+" +
                                re.escape(skill) + r"\s*$", line):
                        manual_only = True
            except (UnicodeError, OSError):
                pass
        if disabled:
            state = "DISABLED"
        elif manual_only:
            state = "MANUAL_ONLY"
        else:
            state = "MODEL_INVOCABLE_CANDIDATE"
        return AdapterResult(
            UNVERIFIED if state != "DISABLED" else FAILED,
            None if state != "DISABLED" else "Skill is disabled by Crush configuration",
            metadata_class=state, policy_diagnostics=policy,
            manual_only=manual_only, disabled=disabled,
            note="Classification is static metadata inspection, not proof the "
                 "Crush model can or will invoke the skill")

    def diagnose(self, skill, workspace, home):
        result = super().diagnose(skill, workspace, home)
        meta = self.skill_metadata(skill, workspace, home)
        result["stages"].append({"stage": "policy", "state": meta["state"],
                                 "reason": meta["reason"]})
        if meta["state"] == "DISABLED":
            result["reason"] = "Earliest failing stage: policy (skill disabled)"
        return result


class HermesAdapter(HarnessAdapter):
    """hermes-agent: project trust, project-local discovery, security
    quarantine, and effective precedence all gate whether a copied skill loads.
    Copying files is NOT sufficient."""

    harness_name = "hermes-agent"

    TRUST_FILE = ".hermes/trust.json"

    def inspect(self, workspace, home):
        trust_state = self._trust_state(workspace, home)
        quarantine = self._quarantine(workspace, home)
        return AdapterResult(
            UNVERIFIED,
            "Trust and quarantine state are static observations; Hermes runtime "
            "behavior is not proven",
            trust=trust_state, quarantine=quarantine, read_only=True,
            precedence=["project-local skills (trusted projects only)",
                        "user skills", "security quarantine blocks untrusted copies"])

    def _trust_state(self, workspace, home):
        for base in (workspace, home):
            path = base / self.TRUST_FILE
            if path.is_file():
                try:
                    import json
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    return {"state": "UNPARSEABLE", "source": str(path)}
                if isinstance(data, dict) and data.get("trusted") is False:
                    return {"state": "UNTRUSTED", "source": str(path)}
                if isinstance(data, dict) and data.get("trusted") is True:
                    return {"state": "TRUSTED", "source": str(path)}
                return {"state": "UNSPECIFIED", "source": str(path)}
        return {"state": "NO_TRUST_RECORD",
                "source": None,
                "note": "No trust record found; Hermes may refuse to load "
                        "project-local skills from untrusted projects"}

    def _quarantine(self, workspace, home):
        marks = []
        for scope, base in (("project", workspace), ("user", home)):
            qdir = base / ".hermes" / "quarantine"
            if qdir.is_dir():
                marks.append({"scope": scope, "path": str(qdir),
                              "present": True})
        return {"quarantine_dirs": marks,
                "note": "Files under a Hermes quarantine directory are not "
                        "loaded; presence here means the copy did NOT take effect"}

    def discover(self, skill, workspace, home, cwd=None, probe=False):
        present = any((root / skill).exists() or (root / skill).is_symlink()
                      for scope in ("project", "user")
                      for root in self.roots(scope, workspace, home))
        trust = self._trust_state(workspace, home)
        if present and trust["state"] in {"UNTRUSTED", "NO_TRUST_RECORD",
                                          "UNSPECIFIED", "UNPARSEABLE"}:
            return AdapterResult(
                UNVERIFIED,
                f"Skill files are present but project trust state is "
                f"{trust['state']}; Hermes may not load project-local skills "
                f"from untrusted projects. Filesystem presence is not discovery.",
                trust=trust)
        if present:
            return AdapterResult(
                UNVERIFIED,
                "Skill files are present and project appears trusted, but no "
                "native Hermes probe exists; discovery is not proven",
                trust=trust)
        return AdapterResult(UNVERIFIED,
                             "Skill not present under any Hermes root; no native probe")

    def diagnose(self, skill, workspace, home):
        result = super().diagnose(skill, workspace, home)
        trust = self._trust_state(workspace, home)
        quarantine = self._quarantine(workspace, home)
        result["trust"] = trust
        result["quarantine"] = quarantine
        result["stages"].append({
            "stage": "trust", "state": trust["state"],
            "reason": "Hermes gates project-local skill loading on project trust"})
        if trust["state"] != "TRUSTED":
            result["reason"] = (f"Earliest failing stage beyond install: project "
                                f"trust is {trust['state']}")
        result["precedence"] = ("project-local skills take precedence for "
                                "trusted projects; quarantine suppresses copies")
        return result


class KimiCodeAdapter(HarnessAdapter):
    """Kimi Code CLI: .kimi-code/skills (project), $KIMI_CODE_HOME/skills (user,
    default ~/.kimi-code), native /skill:name invocation, extended frontmatter."""

    harness_name = "kimi-code-cli"

    @staticmethod
    def kimi_code_home(home: Path) -> Path:
        env = os.environ.get("KIMI_CODE_HOME")
        if env:
            return Path(env).expanduser()
        return Path(home) / _KIMI_CODE_HOME_DEFAULT

    def roots(self, scope, workspace, home):
        if scope == "project":
            return [(Path(workspace) / r).resolve(strict=False)
                    for r in (".agents/skills", ".kimi-code/skills")]
        return [self.kimi_code_home(home) / "skills",
                (Path(home) / ".agents/skills").resolve(strict=False)]

    def invoke(self, skill: str) -> AdapterResult:
        return AdapterResult(
            UNVERIFIED,
            "Documented native invocation is the slash command /skill:name; "
            "UniSkill does not execute Kimi Code CLI on your behalf",
            command=f"/skill:{skill}", native=True)

    def adapt(self, skill_dir: Path) -> AdapterResult:
        return AdapterResult(
            UNVERIFIED,
            "Kimi Code CLI accepts extended frontmatter fields; no transform applied",
            packaging="frontmatter_fields",
            fields=EXTENDED_FRONTMATTER["kimi-code-cli"])


class PiAdapter(HarnessAdapter):
    """Pi: native .pi/skills (project) and .pi/agent/skills (global)."""

    harness_name = "pi"

    def roots(self, scope, workspace, home):
        if scope == "project":
            return [(Path(workspace) / r).resolve(strict=False)
                    for r in (".agents/skills", ".pi/skills")]
        return [(Path(home) / r).resolve(strict=False)
                for r in (".agents/skills", ".pi/agent/skills")]


class CustomAdapter(HarnessAdapter):
    """Custom/external profiles are always INSTALL_ONLY and can never claim
    DISCOVERED: their executables are never launched and no probe exists."""

    def __init__(self, name: str, registry: dict[str, Harness] | None = None):
        self.harness_name = name
        super().__init__(registry)

    def detect(self):
        # Never launch a custom executable; presence via shutil.which only.
        exe = self.spec.executable
        binary = shutil.which(exe) if exe else None
        return AdapterResult(
            "PRESENT" if binary else UNVERIFIED,
            None if binary else f"Executable {exe!r} not found on PATH",
            binary=binary, version=None,
            note="Custom executables are never launched for detection")

    def discover(self, skill, workspace, home, cwd=None, probe=False):
        return AdapterResult(
            UNVERIFIED,
            "Custom profiles are INSTALL_ONLY; no probe exists and filesystem "
            "presence cannot establish runtime discovery")


_ADAPTERS = {
    "jcode": JcodeAdapter,
    "crush": CrushAdapter,
    "hermes-agent": HermesAdapter,
    "kimi-code-cli": KimiCodeAdapter,
    "pi": PiAdapter,
}


def get_adapter(name: str, registry: dict[str, Harness] | None = None) -> HarnessAdapter:
    reg = registry or HARNESSES
    if name not in reg:
        raise UniSkillError(f"Unknown harness: {name}")
    if reg[name].tier == "custom":
        return CustomAdapter(name, reg)
    return _ADAPTERS.get(name, HarnessAdapter)(reg, name)


def adapter_table(registry: dict[str, Harness] | None = None) -> dict[str, str]:
    """support_class for every harness in the registry, honestly classified."""
    reg = _registry(registry)
    return {name: support_class(name, reg) for name in reg}
