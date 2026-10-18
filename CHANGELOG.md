# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The JSON output has its own `schema_version`, which only changes on breaking changes to the JSON.

## [Unreleased]

### Changed

- PyPI package name is `agent-log-cli` (the CLI command remains `agent-log`). The `agent-log`
  name on PyPI belongs to an unrelated project.

### Added

- README rewrite with a demo, install options, a JSON schema reference, orchestrator recipes, a comparison
  with related tools and an FAQ; a Simplified Chinese README (`README.zh-CN.md`).
- `examples/`: `demo_home.py` (try agent-log on bundled sample sessions), `wait-for-agent.sh`,
  `notify-on-done.sh` and an agent skill (`skill/SKILL.md`).
- `docs/assets/`: banner, social preview and a terminal demo generated from real output (`make_demo_svg.py`).

## [0.1.0] - 2026-10-18

First public release.

### Added

- Read-only session readers for six CLI coding agents: droid (Factory), Codex CLI, Claude Code,
  Gemini CLI (0.6x JSONL and older JSON), opencode (1.19 SQLite and the older JSON storage tree) and pi.
- Commands: `list`, `summary`, `show` and `tail` (with `-f` to follow a running session).
  Bare `agent-log` runs `list`.
- `--json` output with a stable, documented schema (`schema_version: 1`) for bots and orchestrators.
- Session status: `running`, `done`, `failed` or `unknown`, with a `status_reason`. Running detection
  matches live agent processes by session id or cwd through `/proc`, then `ps` and `lsof` (macOS), then
  a recent-write check.
- Files changed, errors, last actions and the final answer for each session. Edits whose own result
  is an error are not counted as changed files.
- Window flags for transcripts: `--last`, `--since`, `--until`, `--turn`, `--with-output`, `--no-thinking`,
  `--full` and `--max-chars`.
- Display in the local timezone by default, with `--tz` / `AGENT_LOG_TZ` to override.
- `AGENT_LOG_HOME_<AGENT>` to point any agent at another directory.
- A per-file cache for `list` in `$XDG_CACHE_HOME/agent-log`, keyed by size and mtime
  (`--no-cache` or `AGENT_LOG_NO_CACHE=1` to disable).
- Exit codes: 0 ok, 1 nothing found, 2 usage error. User errors print a message, not a traceback.
- Packaging: `pyproject.toml` (`agent-log-cli` on PyPI) with an `agent-log` console script, installable with `pipx`, `uv tool` or `pip`.
  No runtime dependencies; the optional `zstd` extra reads Codex `.jsonl.zst` archives without the `zstd` binary.
- CI on Linux and macOS for Python 3.9 to 3.13, plus a build-and-install smoke test and a ruff lint.
- Contributor docs, issue templates (including one for requesting a new agent) and a security policy.

### Fixed

- Session files are read as UTF-8 regardless of locale, and JSONL is split on `\n` only, so
  U+2028/U+2029 inside JSON strings no longer break records.
- One malformed record no longer drops a whole session.
- A session that ends on an agent/API error (such as an HTTP 403) is reported as `failed`.
- A session written in the last 120 s only counts as running while a process of that agent is alive.
- `list` sorts by mtime and parses only the rows it shows, so it stays fast with thousands of sessions.
- Gemini CLI: upserted records and `$set` patches, injected `<session_context>` prompts, and shell
  results with a non-zero `Exit Code` reported as errors.
- opencode: ids share a long prefix, so text output shows 14 characters; tool calls use their own start time.
- Text output now uses the same status words as `--json` (`done`, `failed`, `unknown`,
  `running`) instead of `-` and `idle`.
- With no sessions at all, `list` prints every directory it checked and how to point it elsewhere.

[Unreleased]: https://github.com/psylch/agent-log/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/psylch/agent-log/releases/tag/v0.1.0
