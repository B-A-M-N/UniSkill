# Unified skill authoring method

The source of truth is a behavioral contract, *not* a vendor's preferred SKILL.md format. Import guidance with source, date, confidence and classification. Distinguish hard standard requirements from useful suggestions, conditional patterns, harness-dependent behaviors and unverified heuristics.

Start with real use cases and the no-skill baseline. Draft minimal decision-changing guidance. Select workflow techniques only where relevant: sequential dependency ordering, multi-MCP coordination, iterative refinement, context-sensitive tool selection, or encoded domain rules. Increase determinism for fragile work and preserve model flexibility for judgment-based work.

Don't use a skill as an authorization mechanism. Hooks or external enforcement must handle requirements that must run even if the model declines to follow instructions. Preserve user intent, named boundaries and rollback paths.

See `methodology/canonical-principles.yaml` and `sources/source-registry.yaml` for machine-readable provenance in the checkout.
