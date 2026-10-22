---
name: universal-skill-engineer
description: Build, audit, adapt, install, and evaluate Agent Skills across Claude Code, Codex, OpenCode, Gemini CLI, Cursor, Copilot, Crush, jcode, Hermes, Pi, Kimi Code, Goose, and other built-in or custom harnesses. Use for skill authoring, combining vendor guidance, debugging missing or denied skills, portable deployment planning, and proving invocation or functional behavior. Do not use as a generic agent scheduler.
license: Apache-2.0
compatibility: Requires Python 3.10+ and UniSkill CLI for deterministic deployment; native verification needs local access to the target harness.
metadata:
  version: "0.2.0"
  methodology: evidence-driven
---

# Universal Skill Engineer

Never confuse a generated file, an installation, or an explicit invocation with a functionally verified skill.

## Engineering sequence

1. Normalize task into a behavioral contract with triggers (including negatives), preconditions, capabilities, invariants, outputs and acceptance criteria.
2. Assess existing no-skill baseline and whether the requested feature should be a skill, hook, MCP connection, agent or standalone tool.
3. Consult [methodology](references/methodology.md), retaining source version and rule classification. Choose only applicable patterns; use minimal effective instruction.
4. Produce one or more `SKILL.md` candidates (and optional scripts/references) sharing exactly the same behavioral contract. Use `uniskill create` for a deterministic starting scaffold.
5. Validate portable structure using `uniskill validate` and inspect bundled scripts for security concerns separately.
6. Build positive, paraphrased, negative, ambiguous and collision trigger cases. Separate explicit invocation from auto-selection.
7. Compare no-skill baseline to candidate behavior on held-out functional checks and adversarial cases, including permission/tool side effects.
8. Use `uniskill plan` to inspect paths, duplication, prior contents and required approvals. Only `uniskill install --apply` mutates.
9. Use `uniskill doctor <skill> --target <harness>` to inspect even non-UniSkill deployments. Use `uniskill verify --probe` to collect supported native discovery evidence; independently execute invocation and functional checks through configured trusted harness runners.
10. Diagnose the earliest failed stage, make bounded changes without weakening security policy, and rerun regression tests. Keep receipts, evidence and rollback route. For unknown harnesses, add a reviewed registry profile; never guess a skill root or claim behavioral compatibility from file copying.

## Evidence discipline

Always differentiate `SPECIFIED`, `VALID`, `INSTALLED`, `DISCOVERED`, `INVOKABLE`, `AUTOSELECTED`, `EVAL_PASS`, `REGRESSION_PASS`, and cross-harness `PORTABLE`. Missing proofs remain `UNVERIFIED`. A permission denial is `BLOCKED`, not authorization to change policy. Never claim real-harness success if a harness was not executed.

Read [evaluation](references/evaluation.md), [harnesses](references/harnesses.md) and [security](references/security.md) as needed. For source provenance, read `sources/source-registry.yaml` from the UniSkill checkout.
