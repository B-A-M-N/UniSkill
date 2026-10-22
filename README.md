# UniSkill

**Build, validate, deploy, and evaluate agent skills across multiple coding harnesses.**

UniSkill is a Python CLI and agent-facing skill for engineering portable `SKILL.md` packages. It combines contract-based scaffolding, structural validation, harness-aware installation, deployment rollback, troubleshooting, and evidence-based evaluation in one workflow.

Instead of assuming a skill works because its files exist in the right directory, UniSkill distinguishes **installation**, **runtime discovery**, **invocation**, **automatic selection**, and **functional correctness**.

> [!IMPORTANT]
> **Project status — v0.2.0:** UniSkill is a working, early-stage CLI, not a fully autonomous skill optimizer or a certification of universal harness compatibility. Installation is implemented for mapped targets; live functional verification is not yet available for most harnesses. See [Current limitations](#current-limitations).

## Why UniSkill?

Agent skills are increasingly portable in format, but their discovery paths, activation rules, permissions, and execution behavior differ among harnesses. A valid `SKILL.md` does not guarantee that a particular agent will load it—or use it when appropriate.

UniSkill addresses the repeatable parts of that problem:

- **Create from contracts:** generate consistent skill scaffolds from explicit behavioral requirements.
- **Validate before deployment:** check package structure and reject unsafe payload features, including symlinked skill content.
- **Target multiple harnesses:** inspect documented and community-mapped install roots through a data-driven adapter registry.
- **Plan before writing:** show proposed destinations, shared-root deduplication, existing installations, and possible shadowing.
- **Install with rollback:** apply changes explicitly, record file digests and receipts, and protect manually modified files.
- **Diagnose failures:** distinguish missing files, conflicts, trust requirements, policy restrictions, and unverified runtime stages.
- **Evaluate behavior:** run explicitly authorized external test runners, inspect real workspace changes, repeat trials, and compare regression baselines.

## Quick start

**Requirements:** Python 3.10+; Python package dependencies are installed from `pyproject.toml`. Clone the repository and install locally:

```bash
git clone https://github.com/B-A-M-N/UniSkill.git
cd UniSkill
python -m pip install -e .
```

Explore the available harness mappings, validate the included skill, and create a new package from the sample contract:

```bash
uniskill harnesses
uniskill validate skills/universal-skill-engineer
uniskill create contracts/concurrency-audit.yaml --out /tmp/concurrency-audit
```

Preview deployment to several harnesses within a project:

```bash
uniskill plan /tmp/concurrency-audit \
  --target jcode crush opencode codex \
  --workspace /path/to/project
```

**`plan` does not write files.** Installation also requires an explicit `--apply`:

```bash
uniskill install /tmp/concurrency-audit \
  --target jcode crush opencode codex \
  --workspace /path/to/project --apply

uniskill status concurrency-audit --workspace /path/to/project
uniskill doctor concurrency-audit \
  --target jcode crush opencode \
  --workspace /path/to/project
```

Each applied installation returns a `receipt_id`. If installed files have not subsequently changed, UniSkill can reverse the deployment:

```bash
uniskill rollback '<receipt-id>' --workspace /path/to/project
```

Use `--scope user --home /path/to/home` for user-scoped deployment. `--state-root` allows receipts and backups to be stored separately.

## Core workflow

| Stage | Command | Purpose |
| --- | --- | --- |
| Author | `create CONTRACT --out PATH` | Generate a deterministic `SKILL.md` package from a contract. |
| Validate | `validate PATH [--target NAME]` | Check portable package structure; optionally inspect target-specific constraints. |
| Discover targets | `harnesses`, `capabilities NAME`, `detect` | List adapter metadata and find locally available harness executables. |
| Preview | `plan PATH --target X Y` | Inspect destinations, shared roots, installed digests, and conflicts without writing. |
| Deploy | `install PATH --target X Y --apply` | Copy the package and record a reversible deployment receipt. |
| Inspect | `verify NAME --probe`, `status NAME`, `doctor NAME` | Check installed content and available discovery evidence. |
| Troubleshoot | `diagnose NAME`, `repair NAME [--execute]` | Identify the earliest supported failure and plan or execute a bounded repair. |
| Roll back | `rollback RECEIPT` | Revert an unchanged deployment without discarding later/manual edits. |
| Test | `evaluate CASES --runner-config FILE --execute` | Run operator-configured behavioral tests and collect evidence. |

**Safe deployment is the default.** UniSkill does not silently overwrite different installed content. The `--replace` option explicitly authorizes replacement and preserves the previous version for supported rollback operations. Rollback refuses to delete content changed outside UniSkill or owned by a newer receipt.

## Harness compatibility

UniSkill separates *knowledge of a harness's skill locations* from *proof that the harness successfully loads and executes a skill*.

| Mapping tier | Basis | Representative harnesses |
| --- | --- | --- |
| **Local-source** | Install roots inspected in harness source code | jcode |
| **Documented** | Install locations described by vendor documentation | Claude Code, Codex, OpenCode, Gemini CLI, Cursor, GitHub Copilot, Crush |
| **Community-mapped** | Published ecosystem mappings; runtime behavior not verified | Hermes, Pi, Kimi Code CLI, Cline, Goose, Amp, Zed, Warp, OpenHands, Qwen Code, Kiro, DeepAgents |
| **Legacy** | Mapping retained for an older or archived integration | Roo Code |
| **Custom** | Explicit user-defined roots | Local agents, forks, proprietary or experimental harnesses |

The complete registry is available from `uniskill harnesses`; use `uniskill capabilities NAME` to inspect an individual target's support class, roots, and provenance.

### Important harness-specific behavior

**jcode:** the adapter uses source-inspected project roots (`.jcode/skills`, `.agents/skills`, `.claude/skills`) and user roots (`~/.jcode/skills`, `~/.agents/skills`). UniSkill installs files but does not call jcode's live `skill_manage` tool or interact with a running daemon.

**Crush:** native and shared skill directories are mapped. Skills may still be hidden by configuration (including `disable-skill`), and custom `CRUSH_SKILLS_DIR` or other configured paths require an explicit registry profile.

**Other harnesses:** root mappings describe possible installation locations, not evidence of discovery or successful execution. Currently, Gemini CLI has a native discovery-text probe; the other harnesses are not universally runtime-probed or functionally certified.

### Add an experimental or private harness

The adapter registry is data-driven. Start with [`examples/custom-harnesses.json`](examples/custom-harnesses.json), define safe project/user-relative skill locations, and pass the registry before the command:

```bash
uniskill --registry examples/custom-harnesses.json capabilities inhouse-agent

uniskill --registry examples/custom-harnesses.json \
  plan skills/universal-skill-engineer \
  --target inhouse-agent jcode \
  --workspace /path/to/project
```

Custom mappings do **not** create a runtime driver. Built-in profiles require `override: true` to be replaced. Arbitrary system paths are not accepted as discovery roots.

## Evidence and evaluation

UniSkill models skill readiness as separate stages:

```text
SPECIFIED → VALID → INSTALLABLE → INSTALLED → DISCOVERED
          → INVOKABLE → AUTOSELECTED → FUNCTIONAL
          → REGRESSION_PASS → PORTABLE
```

`BLOCKED`, `FAILED`, `UNSUPPORTED`, and `UNVERIFIED` represent different outcomes. A receipt and matching file hashes support an *installed* claim; they do not establish runtime discovery or correct behavior. Likewise, a runner's claim that a tool was called is not automatically proof that the harness performed it.

### Behavioral test types

| Type | What it checks |
| --- | --- |
| `discovery` | Whether the native harness identifies a skill, including negative discovery cases |
| `invocation` | Whether the skill is explicitly invoked, or must not be invoked |
| `selection` | Whether the harness selects the skill automatically, including negative cases |
| `functionality` | Required behavior and artifacts, prohibited side effects, and output constraints |
| `regression` | Whether previously established behavior continues to pass against a baseline |

Evaluation uses an **explicitly configured, trusted external runner**. UniSkill supplies prompts and evaluates outputs and measurements; it does not silently launch agent sessions or provision their credentials.

Evidence includes:

- **Measured workspace diffs:** pre/post file hashes verify actual artifacts and detect forbidden changes or edits outside allowed prefixes.
- **Process observations:** exit codes and timeouts are independently observed.
- **Runner assertions:** response text and reported tool calls are supported but labeled *runner-asserted*, not independently measured.
- **Repeated trials:** pass rates and Wilson 95% confidence intervals characterize reliability. Fewer than five trials are preliminary; inconsistent outcomes are flagged as unreliable.
- **Regression baselines:** previously passing behavior can be compared with a new run to identify regressions rather than treating them as ordinary failures.

Example:

```bash
uniskill evaluate evals/functionality-cases.jsonl \
  --runner-config examples/runner.json \
  --repetitions 5 --execute \
  --workspace /path/to/measured/tree \
  --baseline evals/baselines/my-skill.json
```

The measured workspace must be the same tree the runner actually changes. See [`evals/`](evals/) for typed example cases and [`examples/runner.json`](examples/runner.json) for the runner configuration format.

> [!WARNING]
> The included `echo_runner.py` is a test fixture, not a real coding agent. A passing fixture validates evaluation mechanics—not a skill's performance in Claude Code, Codex, OpenCode, jcode, or another harness. `--execute` can launch trusted local subprocesses; do not point it at untrusted programs.

## Diagnosis and repair

`doctor` inspects candidate files and known configuration issues, while `diagnose` identifies the earliest lifecycle stage for which a supported failure is observed. A stage that has not been probed remains **unverified**, not automatically failed or passed.

`repair` uses bounded attempts and costs (`--max-attempts`, `--max-cost`). Without `--execute`, it reports a proposed action. It cannot manufacture missing runtime evidence or bypass an unsupported diagnosis.

Trust requirements, quarantines, and permission denials are surfaced as blockers. UniSkill does not secretly relax a harness's security policy, grant credentials, or bypass consent to make an installation appear successful. Policy-related repair operations require explicit `--allow-policy` authorization.

## Project layout

| Path | Contents |
| --- | --- |
| [`src/uniskill/`](src/uniskill/) | CLI, contracts, adapters, validation, deployment, status, repair, evaluation |
| [`skills/universal-skill-engineer/`](skills/universal-skill-engineer/) | Agent-facing skill with authoring instructions and references |
| [`methodology/`](methodology/) and [`sources/`](sources/) | Curated engineering principles and source provenance |
| [`contracts/`](contracts/) | Example behavioral contracts |
| [`evals/`](evals/) and [`examples/`](examples/) | Test cases, runner configurations, custom harness mappings |
| [`tests/`](tests/) | Isolated behavior, correctness, and security tests |

### Development

```bash
python -m pip install -e '.[dev]'
python -m pytest
python -m ruff check .
```

See [`pyproject.toml`](pyproject.toml) for the package version, supported Python versions, and development dependencies.

## Current limitations

UniSkill does **not** currently provide:

- LLM-driven skill synthesis, iterative self-optimization, or semantic grading of arbitrary agent behavior.
- Built-in live execution of Codex, Claude Code, OpenCode, jcode, Crush, and other harness sessions for end-to-end certification.
- Universal measurement of automatic skill triggering or compatibility guarantees across harness/model versions.
- Automatic refresh of third-party source provenance or platform-wide execution sandbox guarantees.
- Windows filesystem installation support.

A valid package and successful deployment should therefore be interpreted as **necessary setup**, not a certified result. Deterministic authorization still belongs in harness policy, hooks, and tool guards; live data and privileged actions belong in appropriately controlled tools or MCP integrations.

## License

Licensed under the [Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for applicable notices.

## Acknowledgment and disclaimer

AI inference used during UniSkill's development was provided by [FreeInference (FI)](https://freeinference.org/). **UniSkill was developed independently:** FI did not direct, commission, review, approve, or endorse this project or the claims made in this README.

If you find UniSkill useful, please consider supporting FI. Its service helps expand access to AI tools for people who might otherwise have limited or no access.
