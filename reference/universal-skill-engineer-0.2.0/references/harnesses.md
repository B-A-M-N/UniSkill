# Adapter engineering: skill roots are not capability proof

The CLI ships vendor-documented adapters for Claude Code, Codex, OpenCode, Gemini CLI, Cursor, Copilot and Crush; a source-inspected adapter for local jcode; and a separate group of community-mapped/legacy adapters for unusual clients. A dynamic JSON registry supports private forks and new tools.

Use `uniskill harnesses` for an exact list with support tier and provenance; use `uniskill capabilities <name>` before making a support claim. `uniskill plan` selects a set of overlapping discovery roots, then reports existing same-named packages in the other roots so a shadowing definition cannot silently pass as installed successfully.

Special cases:

- **jcode** loads globals from `~/.jcode/skills`, `~/.agents/skills` and locally installed Claude plugin skill directories. Per-workspace overlays are `.jcode/skills`, `.agents/skills`, `.claude/skills`. Its native Rust skill loader defines actual precedence and overlays. Run runtime checks in the active session workspace rather than treating the daemon launch cwd as the project.
- **Crush** discovers project `.agents`, `.crush`, `.claude`, and `.cursor` skills and configured globals. `disable-skill`, `CRUSH_SKILLS_DIR` and extra configured paths can change visibility; this version inspects only common JSON policy hints and static roots. Non-default roots require a reviewed profile.
- **Hermes, Pi, Kimi Code, Goose, Cline, Zed, Qwen Code, OpenHands, etc.** have independently published installation mappings; the CLI makes no claim that installing proves auto-selection, activation, or behavior.
- **Agent wrappers / controllers** can use a JSON profile for their own skill store or delegate skill deployment to their actual child execution harness(es). Avoid fake wrapper directories if the wrapper only proxies an underlying harness.

For installation, prefer a single shared root when discovery overlaps, but examine preexisting collisions and real harness precedence. Never overwrite dissimilar skill versions without `--replace`; preserve backups. Report structural validation, installed hash, native discovery, explicit invocation, auto-selection and functional acceptance as distinct results. A runner must genuinely exercise the target if it is used for behavioral proof.

See project README for the profile format and trust model.
