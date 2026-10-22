# UniSkill

**Portable, evidence-driven Agent Skills engineering.** UniSkill creates deterministic `SKILL.md` scaffolds from behavioral contracts, validates skill packages, plans deduplicated multi-harness deployment, installs transactionally, diagnoses common discovery failures, and runs explicitly configured evaluation cases. It reports exactly what was measured.

This is a **working Python CLI and an agent-facing skill**, not a completed autonomous skill optimizer or a production-certified universal runtime. In particular, installation alone never proves a harness loaded, invoked, selected, or correctly executed a skill.

## Install and try it

Python 3.10+ required. Run from the extracted source tree:

```bash
python -m pip install -e .
uniskill harnesses
uniskill validate skills/universal-skill-engineer
uniskill create contracts/concurrency-audit.yaml --out /tmp/concurrency-audit
uniskill plan /tmp/concurrency-audit --target jcode crush opencode codex \
  --workspace /path/to/test-repo
```

**No files are written by `plan` or by `install` without `--apply`.** Inspect the plan before deploying:

```bash
uniskill install /tmp/concurrency-audit --target jcode crush opencode codex \
  --workspace /path/to/test-repo --apply
uniskill status concurrency-audit --workspace /path/to/test-repo
uniskill doctor concurrency-audit --target jcode crush opencode \
  --workspace /path/to/test-repo
```

`install` outputs a `receipt_id`. To reverse that deployment (only while the deployed files still match the receipt):

```bash
uniskill rollback '<receipt-id>' --workspace /path/to/test-repo
```

`--replace` explicitly authorizes replacing a previously different target; UniSkill saves the original in its backup store for rollback. It refuses to remove manually edited installed files on rollback.

## Harness coverage is layered

| Support tier | Meaning | Examples |
| --- | --- | --- |
| `local-source` | Installation roots inspected in local source | `jcode` |
| `documented` | Native locations backed by vendor documentation | Claude Code, Codex, OpenCode, Gemini CLI, Cursor, Copilot, Crush |
| `community-mapped` | Published installer root mapping; runtime not proven | Hermes, Pi, Kimi Code CLI, Cline, Goose, Amp, Zed, Warp, OpenHands, Qwen Code, DeepAgents, Kiro, etc. |
| `legacy` | Root mapping for an older/archived harness | Roo Code |
| `custom` | User-supplied roots; zero implied runtime support | Any in-house agent, wrapper, fork, new harness |

All tiers still require separate native functional tests. See `uniskill harnesses` or `uniskill capabilities jcode` for the current detailed map.

### jcode: source-inspected native roots

The `jcode` adapter is based on its Rust loader (`crates/jcode-base/src/skill.rs`), not an assumption that it uses Codex or OpenCode's configuration. Native discovery locations:

- Project: `.jcode/skills`, `.agents/skills`, `.claude/skills`.
- User: `~/.jcode/skills`, `~/.agents/skills`.

Project definitions are per-workspace overlays, not global daemon state. jcode can load skills through its `skill_manage` tool; the CLI in this release **does not drive a live jcode daemon or call that tool automatically**. If a skill does not appear, inspect the active session working directory, whether a global skill with the same name is shadowed, and reload behavior. `uniskill doctor` checks file candidates, not live session bindings.

### Crush: documented native and shared roots

Crush recognizes `.crush/skills`, `.agents/skills`, `.claude/skills`, and `.cursor/skills` in a project and its configured global locations. `option disable-skill` can hide a skill even when it is correctly installed; extra paths can be configured through Crush. This version covers standard static roots; for custom `CRUSH_SKILLS_DIR` or configured paths, provide an explicit custom registry profile rather than assuming that filesystem presence proves activation.

### Private / exotic harness support

The adapter registry is deliberately **data-driven**. Copy `examples/custom-harnesses.json`, define documented relative project/user skill roots, and pass it with the global `--registry` option before the subcommand:

```bash
uniskill --registry examples/custom-harnesses.json capabilities inhouse-agent
uniskill --registry examples/custom-harnesses.json plan skills/universal-skill-engineer \
  --target inhouse-agent jcode --workspace /path/to/test-repo
```

This works for forks and experimental TUIs without pretending they expose a native discovery API. A profile **does not automatically add a runtime driver**; for executable validation, supply a trusted harness runner described below. For built-ins, `override: true` is required to override defaults. Discovery-root paths must be safe, relative to the selected workspace or home; custom arbitrary system paths are refused.

## Commands

| Command | What it proves |
| --- | --- |
| `create CONTRACT --out PATH` | Deterministic skill source generated; not optimized by an LLM |
| `validate PATH [--target NAME]` | Portable structure valid; target-specific features remain unverified |
| `harnesses`, `capabilities NAME` | Profile mapping and provenance |
| `detect` | Harness executables on `PATH` (does not establish support) |
| `plan PATH --target X Y` | Dry-run destinations, shared coverage, prior digests, shadowing candidates |
| `install PATH --target X Y --apply` | File tree copied and hashed; no native discovery claim |
| `verify NAME --probe` | Installed hash verified; Gemini CLI native list check when available |
| `status NAME` | Layered per-stage status; nothing beyond installation is claimed without a probe |
| `doctor NAME --target X Y` | Known on-disk candidates and applicable static policy issues, with or without receipts |
| `diagnose NAME [--target X Y]` | Earliest failing lifecycle stage and its single matched intervention |
| `repair NAME [--execute]` | Bounded repair plan for that diagnosis; never claims success it cannot evidence |
| `rollback RECEIPT` | Reverts a deployment only if the installed tree is untouched and no newer receipt owns it |
| `evaluate CASES --runner-config FILE --execute` | Operator-approved evaluator subprocess and output/tool-trace assertions |

Use `--scope user --home /path/to/home` for a user-scoped plan/install. Use `--workspace` for project scope. Use `--state-root` if you want receipts and backups in a nondefault location.

### Diagnosis and repair

`diagnose` reduces the lifecycle to **one** actionable failure: the earliest stage that is actually broken. A later stage failing is treated as a consequence, not repaired independently.

- A skill whose files are present but whose runtime was never probed diagnoses as `CLEAN` — files are a precondition for discovery, not evidence of it. `repair` then reports `NO_REPAIR` rather than inventing work.
- Policy denials, required project trust, and security quarantine are reported as **blockers**. UniSkill stops and asks for operator action; it never edits harness policy to make a problem disappear.
- `repair` runs under an attempt/cost budget (`--max-attempts`, `--max-cost`), reports `ADVISORY` when no automatic handler exists for an intervention, and never weakens a permission, grants a credential, skips consent, or drops an invariant to improve a result. Policy-touching actions require `--allow-policy`.

## Evidence model

These stages are independent:

`SPECIFIED → VALID → INSTALLABLE → INSTALLED → DISCOVERED → INVOKABLE → AUTOSELECTED → FUNCTIONAL → REGRESSION_PASS → PORTABLE`

`BLOCKED`, `FAILED`, `UNSUPPORTED` and `UNVERIFIED` are **not** synonyms. Missing runtime proof stays `UNVERIFIED`. A denied skill permission is not a pretext for weakening policy. `uniskill status` currently only certifies the installed stage from receipts/hashes. Do not present a test runner's matching output regex as proof of trigger selection or real-harness compatibility.

### Harness support is a classified claim, not a vibe

Each harness carries a `support_class`, and the classes are deliberately unequal:

| Class | Meaning | Harnesses |
| --- | --- | --- |
| `FUNCTIONAL_VERIFIED` | UniSkill executed the harness and observed correct behavior | none |
| `DISCOVERY_PROBED` | A real native probe exists and can be run | `gemini` |
| `NATIVE_DOCUMENTED` | The vendor documents a mechanism UniSkill does not itself execute | `kimi-code-cli` |
| `INSTALL_ONLY` | Copy targets are known; nothing about runtime behavior is known | the other 27, and every custom profile |

Knowing where a harness reads skills is **not** knowing that it loaded one. `discover()` returns `UNVERIFIED` with a reason unless a genuine probe ran, so filesystem presence alone can never produce a discovery claim. Where a harness needs project trust before loading (Hermes) or is disabled by configuration (Crush `crushrc`), the adapter reports the blocker instead of the copy.

## Evaluations

UniSkill supports a **trusted, explicitly configured** external runner protocol. The runner receives the test prompt through stdin. The safest runner starts an isolated agent instance with separately managed credentials, permissions, budget and throwaway workspace. It returns text or structured JSON:

```json
{"response":"Audit complete", "tool_calls":[{"name":"search"}]}
```

Create `cases.jsonl` with `prompt`, optional `must_include`, `must_not_include`, `required_tools`, `forbidden_tools`, and optional `expected_exit_code`. See `evals/example.jsonl` and `examples/runner.json`. Run:

```bash
uniskill evaluate evals/example.jsonl \
  --runner-config examples/runner.json --repetitions 2 --execute
```

The included `echo_runner.py` is a fixture, **not** a real agent execution backend. Its passing assertions prove that the evaluation machinery works, not that a particular skill works. Execution can run arbitrary trusted local binaries, so it must be requested with `--execute`; do not point it at untrusted scripts.

## What is built and what remains

**Implemented:** canonical contract validation/scaffolding; portable static validation; rejection of symlinked skill payloads; digest-based deployment; preflight shared-root planning; safe relative custom adapter registry; existing duplicate/shadow detection; multiple harness roots; explicit apply/replace gates; receipt and backup storage; protected rollback; Gemini CLI discovery-text probe; jcode/Crush discovery-root adapters; OpenCode/Crush configuration diagnostics; basic external evaluation runners; pytest suite and GitHub Actions CI.

**Not implemented/proven:** LLM-driven synthesis/iterative candidate optimizer; executing real Codex/Claude/jcode/Crush/etc. sessions from within UniSkill; universal auto-trigger measurement; semantic grading of agent behavior; platform-wide sandbox guarantees; automated source provenance updates; live compatibility certification across harness/model versions; Windows filesystem installations. The lack of these capabilities must remain visible in the CLI, rather than substituting a green status for a guess.

## Project files

- `src/uniskill/`: CLI, contracts, validator, adapters, deployment and evaluation.
- `skills/universal-skill-engineer/`: agent-facing instruction skill and references.
- `methodology/`, `sources/`: rationale/authoring guidance, manually curated source provenance.
- `contracts/`, `evals/`, `examples/`: sample contract, evaluation cases and adapter profiles.
- `tests/`: isolated behavior and security tests.

## Design constraints

A skill is a reusable behavioral package, **not** a privileged enforcement engine. Deterministic authorization belongs in hooks/host policy/tool guards; live data and authenticated actions belong in controlled tools/MCP integrations. UniSkill doesn't autonomously disable permissions, edit harness configurations, send network requests, or run installed skill scripts during validation. Source-derived methodology should be revised and tested rather than applied to every installed skill without consent.

Licensed Apache-2.0; see `LICENSE`.
