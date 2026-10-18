# agent-log

An agent-friendly progress view of CLI coding-agent sessions. Bots and orchestrators can use it to check what a droid, Codex, Claude Code, Gemini CLI, OpenCode or pi session is doing, whether it is still running, what it changed and what it answered. The output is compact text or stable JSON.

給 bot / orchestrator 用的 CLI coding agent 進度檢視工具。

It is a single Python file (3.9+) that uses only the standard library and reads the agents' own session logs. It never writes to them.

## Install

```zsh
pipx install git+https://github.com/psylch/agent-log     # or: uv tool install git+https://github.com/psylch/agent-log
agent-log                                                # lists recent sessions of every agent
```

From a checkout, `pip install .` works too. Or copy the single file, since it has no dependencies:

```zsh
cp agent_log.py ~/.local/bin/agent-log   # or: ln -s "$PWD/agent_log.py" ~/.local/bin/agent-log
chmod +x ~/.local/bin/agent-log
```

## Commands

```zsh
agent-log list [--source NAME] [--project SUBSTR] [--since 2h] [--active] [-n 15] [--json]
agent-log summary [SESSION] [--source NAME] [--json]        # one-screen status
agent-log show [SESSION] [--last N] [--since T] [--until T] [--turn N]
               [--with-output] [--no-thinking] [--full] [--max-chars 300] [-f] [--json]
agent-log tail [SESSION] ...                                 # show, last 20 steps by default (--all for everything)
```

- `SESSION` is `latest` (the default), an id prefix, a project path substring, or a session file path. OpenCode SQLite sessions use `<db>#<session-id>` as their path.
- `--source` takes `droid`, `codex`, `claude`, `gemini`, `opencode` or `pi`. `--no-codex` hides Codex.
- `--since` and `--until` accept `30m`, `2h` or `1d` (ago), `HH:MM` (today) or ISO 8601.
- Text output shows times in UTC+8. A step is one assistant message (its text, thinking and tool calls). A turn starts at each user prompt.

## JSON schema (`schema_version: 1`)

All timestamps are ISO 8601 with an offset (`2026-10-01T14:00:01+08:00`). New keys may be added. Existing keys keep their meaning while `schema_version` stays 1.

`summary --json` returns one session object:

| key | type | meaning |
|---|---|---|
| `schema_version` | int | `1` |
| `source` | string | `droid`, `codex`, `claude`, `gemini`, `opencode` or `pi` |
| `session_id` | string | the agent's own session id |
| `path` | string | session file (OpenCode DB: `<db>#<id>`) |
| `cwd` | string or null | project directory |
| `status` | string | `running`, `done`, `failed` or `unknown` |
| `status_reason` | string | `proc:cwd`, `proc:session-id` or `mtime<120s` when running. `final` or `ended on assistant text` when done. `ended on agent/API error` when failed (the last event is an agent-level error such as an API 403, not a failed tool call). `idle, ended on <kind>` or `no events` when unknown |
| `started` | string or null | first event time |
| `updated` | string | last write time of the session file or rows |
| `steps`, `turns` | int | counts |
| `task` | string or null | first user prompt, truncated to 600 characters |
| `files_changed` | [string] | paths written by edit, write or patch tools, in first-touch order. An edit whose own result is an error is not counted |
| `error_count` | int | all errors |
| `errors` | [{`ts`, `tool`, `text`}] | the last 10 errors (failed tool results and agent errors). `tool` is null for agent errors |
| `last_actions` | [{`ts`, `kind`, `text`}] | the last 6 `tool_call` or `say` events |
| `final_answer` | string or null | the latest turn's final message, or the last assistant text if the session is idle. Null while a turn is still in progress |

`list --json` returns `{"schema_version": 1, "sessions": [...]}`. Each item has the same keys as above except `errors`, `error_count`, `last_actions` and `final_answer`.

`show --json` and `tail --json` return `{"schema_version": 1, "session": {...list item...}, "events": [...]}`. Each event is `{ts, kind, tool?, text, paths?, step, turn}`:

- `kind` is one of `user`, `say`, `think`, `tool_call`, `tool_result`, `error`, `compact` or `final`. A failed tool result is reported as `error`.
- For `tool_call`, `text` is a one-line summary such as `Execute $ npm test` or `Edit /path/file`.
- `tool_result`, `error` and `compact` text is clipped to 4 × `--max-chars` (1200 by default). Use `--full` to get the full text.
- The window flags (`--last`, `--since`, `--turn` and so on) and `--no-thinking` apply. `--follow` is text-only.

## Supported agents

| source | session files | running detection | notes |
|---|---|---|---|
| `droid` | `~/.factory/sessions/<escaped-cwd>/<id>.jsonl` | `droid` process | `compaction_state` records are reported as `compact` |
| `codex` | `~/.codex/sessions/**/*.jsonl`, `~/.codex/archived_sessions/**` (`$CODEX_HOME`) | `codex` process, `codex resume <id>` | `.jsonl.zst` archives are read through the `zstandard` module or the `zstd` binary. They are skipped silently when neither exists. `task_complete` is reported as `final` |
| `claude` | `~/.claude/projects/<escaped-cwd>/<id>.jsonl` (`$CLAUDE_CONFIG_DIR`) | `claude` process (native or node) | Sidechain (subagent) records and meta and caveat messages are skipped. Slash commands are shown as `/cmd args` |
| `gemini` | `~/.gemini/tmp/<project>/chats/session-*.jsonl` (Gemini CLI 0.6x) or `session-*.json` (older) | `gemini` process | JSONL: a metadata header line, then message records upserted by `id` (the last copy wins) and `$set` patches. The cwd comes from `<project>/.project_root`. The injected `<session_context>` message and `functionResponse`-only user records are not prompts. A shell result with `Exit Code: N` (N≠0) counts as an error even when its status is `success`. `info` messages are skipped |
| `opencode` | `~/.local/share/opencode/opencode*.db` (SQLite, read-only) and the older `storage/{session,message,part}/` JSON tree (`$XDG_DATA_HOME`) | `opencode` process | OpenCode 1.19 stores `session`, `message` and `part` tables whose JSON matches the old storage files; a `session_v2`/`session_message` layout is also read. Subagent child sessions are hidden. Ids share their first 8 characters, so text output shows 14 (`ses_eb24f4645f`) |
| `pi` | `~/.pi/agent/sessions/--<cwd>--/<ts>_<id>.jsonl` (`$PI_CODING_AGENT_DIR`, `$PI_CODING_AGENT_SESSION_DIR`) | `pi` process | Only the active branch of the session tree is shown |

Running detection scans `/proc/*/cmdline` for these process names, including `node`, `bun` and `deno` wrappers. A match against a session id flag claims that session. Otherwise the process claims the newest session of its kind whose cwd matches the process's `--cwd` or `/proc/<pid>/cwd`. A session written in the last 120 seconds also counts as running, but only while some process of that agent is alive, so a finished `claude -p` does not look busy for two minutes. Process scanning works on Linux only. Elsewhere, status relies on the mtime check alone.

Each provider's base directory can be overridden with `AGENT_LOG_HOME_<NAME>`, for example `AGENT_LOG_HOME_CODEX=/data/codex`. The value replaces `~/.factory`, `~/.codex`, `~/.claude`, `~/.gemini`, `~/.local/share/opencode` or `~/.pi/agent`. `AGENT_LOG_PROC` replaces `/proc`, which the tests use.

## Tests

```zsh
python3 -m unittest discover -s tests
```

The fixtures under `tests/fixtures/home` are laid out as a fake `$HOME`. Each format has a small synthetic session. Claude Code 2.1, Gemini CLI 0.64 and OpenCode 1.19 also have real sessions recorded through a local gateway (`/fx/*-real`, paths sanitized), including one run that ended on an API 403. The OpenCode databases are built from `tests/fixtures/opencode_db.sql` (synthetic `session_v2` layout) and `tests/fixtures/opencode_v1_db.sql` (real 1.19 layout).

## Credits

Knowledge of the session formats was taken from [SpecStory CLI](https://github.com/specstoryai/getspecstory) (Apache-2.0). No code was copied. agent-log is an independent Python reimplementation.

## License

MIT, see [LICENSE](LICENSE).
