# Security rules

Structural validation never runs skill scripts or installs packages. Reject symlinked skill resources and out-of-tree destination paths. Use dry-run planning before all mutations. Require explicit `--apply` and separate `--replace` for divergent installs. Keep backups, content digests and receipts; rollback refuses to destroy unexpected user edits.

Inventory filesystem access, shell/process, package install, network, secrets, MCP, hooks and privileged effects. Harness permission denies, prompts or disabled tools must be escalated as operator decisions, never patched automatically. Execute functional tests only with `--execute` and a trusted runner inside appropriate sandbox and privilege boundaries.
