# Evaluation

Keep distinct suites for direct positive triggers, paraphrases, nearby negatives, ambiguous requests and collisions with other skills. Explicit `/name` or skill-tool activation only proves invocation, not auto-selection. Grade observable tool use, outputs, side effects and safety invariants over incidental prose.

Baseline -> candidate A/B/C -> held-out cases -> repeatability -> regression. Record harness version, model, permissions, clock, installed digest and known limitations. Use output-regex tests sparingly: their pass condition proves only those assertions, not complete functional correctness.

`uniskill evaluate --runner-config examples/runner.json --execute evals/example.jsonl` launches a *trusted user-configured* program. No subprocess runs without `--execute`. JSON runners may return `response` and `tool_calls` to support assertions. Do not hand arbitrary remote scripts to this runner.
