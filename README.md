<p align="center">
  <img src="docs/assets/banner.svg" alt="agent-log: tail -f for your coding agents" width="100%">
</p>

<h1 align="center">agent-log</h1>

<p align="center">
  <b><code>tail -f</code> for your coding agents, built for the agents that launched them.</b><br>
  Check on Claude Code, Codex CLI, Gemini CLI, opencode, droid and pi from a script:<br>
  one screen of text or one JSON object with the status, files changed, errors and final answer.
</p>

<p align="center">
  <a href="https://github.com/psylch/agent-log/actions/workflows/ci.yml"><img src="https://github.com/psylch/agent-log/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/python-3.9%E2%80%933.13-3776AB?logo=python&logoColor=white" alt="Python 3.9 to 3.13">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT license"></a>
  <img src="https://img.shields.io/badge/dependencies-0-brightgreen" alt="zero dependencies">
  <a href="#supported-agents"><img src="https://img.shields.io/badge/agents-6-8A2BE2" alt="6 supported agents"></a>
</p>

<p align="center"><b>English</b> | <a href="README.zh-CN.md">简体中文</a></p>

<p align="center">
  <img src="docs/assets/demo.svg" alt="agent-log listing sessions from six coding agents, then a one-screen summary of a Claude Code session" width="860">
</p>

## Why

You (or your bot, or your main agent) started a few coding agents in the background: `claude -p`, `codex exec`, `droid exec`.
Now you need to know three things. Are they done? What did they change? Did anything go wrong?

Every agent writes a session log, but the logs are hard to use:

- **Big.** Long runs produce megabytes of JSONL full of tool output, thinking and metadata.
- **Scattered.** They sit under escaped paths like `~/.claude/projects/-work-api/<uuid>.jsonl`, and every agent picks its own layout.
- **Different.** Six agents means six formats, and the formats change between versions.
- **Silent about status.** No record says "still running" or "finished". You have to infer it from the last record and the process table.

**agent-log** reads all of them and answers in one screen, or in one JSON object:
the task, the status (`running`, `done`, `failed`, `unknown`), the files changed, the errors, the last actions and the final answer.
It is a single Python file with no dependencies. It only reads the logs, never writes to them, and never touches the network.

## Contents

[Install](#install) · [Quickstart](#quickstart) · [Supported agents](#supported-agents) · [Commands](#commands) ·
[JSON output](#json-output) · [Use with orchestrators](#use-with-orchestrators) · [Why not X?](#why-not-x) ·
[FAQ and limitations](#faq-and-limitations) · [Contributing](#contributing)

## Install

Python 3.9 or newer, on Linux or macOS. Pick one:

```sh
pipx install agent-log-cli
uv tool install agent-log-cli
uvx agent-log-cli                              # run once without installing
```

From git (same command name after install):

```sh
pipx install git+https://github.com/psylch/agent-log
uv tool install git+https://github.com/psylch/agent-log
uvx --from git+https://github.com/psylch/agent-log agent-log
```

Or copy the single file. It has no dependencies:

```sh
mkdir -p ~/.local/bin
curl -fsSL https://raw.githubusercontent.com/psylch/agent-log/master/agent_log.py -o ~/.local/bin/agent-log
chmod +x ~/.local/bin/agent-log
```

> [!NOTE]
> The CLI command is `agent-log`. The PyPI package is named `agent-log-cli` because
> `agent-log` on PyPI belongs to an unrelated project — `pip install agent-log` installs the wrong thing.

To read archived Codex sessions (`.jsonl.zst`), install either the `zstd` command or the `zstandard` Python package. Without one of them, those archives are skipped.

### Try it without any agent installed

The repository ships sanitized sessions from all six agents. This builds a throwaway `$HOME` from them:

```sh
git clone https://github.com/psylch/agent-log && cd agent-log
python3 examples/demo_home.py /tmp/agent-log-demo
export HOME=/tmp/agent-log-demo AGENT_LOG_PROC=/tmp/agent-log-demo/.proc
python3 agent_log.py
python3 agent_log.py summary 134298f6
```

Every output shown in this README comes from that demo home, with `AGENT_LOG_TZ=UTC`.

## Quickstart

```sh
agent-log                                   # recent sessions of every agent, newest first
agent-log list --active                     # only the ones running right now
agent-log summary                           # the newest session on one screen
agent-log summary 134298f6                  # a specific session: id prefix, project substring or file path
agent-log tail -f                           # follow the newest session live (Ctrl-C to stop)
agent-log summary 134298f6 --json | jq -r .status
```

`agent-log` (the same as `agent-log list`):

```text
last(UTC)    kind     id             steps turns  status                   project
10-18 06:26  opencode ses_eb24f4645f     5     1  done                     /fx/opencode-real
10-18 06:24  opencode ses_eb2512259f     0     1  failed                   /fx/opencode-real
10-18 06:24  gemini   d9840123           4     1  done                     /fx/gemini-real
10-18 06:23  claude   134298f6           6     1  done                     /fx/claude-real
10-18 06:23  claude   7ede2b2f           7     1  failed                   /fx/claude-real
10-01 09:01  opencode ses_db1            1     1  failed                   /fx/opencode-db
10-01 09:00  opencode ses_legacy1        2     1  done                     /fx/opencode
10-01 06:00  pi       66666666           3     2  done                     /fx/pi
10-01 05:00  gemini   55555555           2     1  done                     /fx/gemini
10-01 04:00  claude   44444444           3     2  running proc:cwd         /fx/claude
10-01 03:00  codex    22222222           3     1  done                     /fx/codex
10-01 02:00  droid    11111111           3     1  done                     /fx/droid
status: done | failed (agent/API error) | unknown | running (proc:* = agent process matched, mtime<120s = just written). Details: agent-log summary <id>
```

`agent-log summary 134298f6`, a real Claude Code session:

```text
[claude] /fx/claude-real  134298f6  done (ended on assistant text)
  steps 6, turns 1, 10-18 06:23 → 10-18 06:23 (UTC), elapsed 0m07s
TASK: Read notes.txt. Fix the bug in calc.py so add returns a+b. Then run: python3 -c 'import calc; print(calc.add(2,3))'. Then run: cat missing_file.txt (it will fail, that's expected). Reply DONE briefly.
FILES CHANGED (1): calc.py
ERRORS (1), last 3:
  06:23:50 #5 Bash: Exit code 1 cat: missing_file.txt: No such file or directory
LAST ACTIONS:
  06:23:48 #3 SAY Now I'll fix the bug in calc.py:
  06:23:48 #3 ▶ Edit /fx/claude-real/calc.py
  06:23:50 #4 SAY Now running the commands:
  06:23:50 #4 ▶ Bash $ python3 -c 'import calc; print(calc.add(2,3))'
  06:23:50 #5 ▶ Bash $ cat missing_file.txt
  06:23:52 #6 SAY DONE. Fixed calc.py (a - b → a + b), test returned 5 correctly, and missing_file.txt failed as expected.
FINAL (last assistant text): DONE. Fixed calc.py (a - b → a + b), test returned 5 correctly, and missing_file.txt failed as expected.
```

`agent-log tail 134298f6 --last 3 --with-output` shows the transcript, including tool output:

```text
[claude] /fx/claude-real  134298f6  done (ended on assistant text)
  steps 6, turns 1, 10-18 06:23 → 10-18 06:23 (UTC), elapsed 0m07s
------------------------------------------------------------
06:23:50 #4   SAY   Now running the commands:
06:23:50 #4   ▶ Bash $ python3 -c 'import calc; print(calc.add(2,3))'
06:23:50 #4     ↳ 5
06:23:50 #5   ▶ Bash $ cat missing_file.txt
06:23:50 #5   ✗ ERROR Bash: Exit code 1 cat: missing_file.txt: No such file or directory
06:23:52 #6   SAY   DONE. Fixed calc.py (a - b → a + b), test returned 5 correctly, and missing_file.txt failed as expected.
```

A few `jq` one-liners:

```sh
# what is running right now, and where
agent-log list --json | jq -r '.sessions[] | select(.status == "running") | "\(.source) \(.session_id[:8]) \(.cwd)"'
# claude 44444444 /fx/claude

# the short version of one session
agent-log summary 134298f6 --json | jq '{status, files_changed, error_count, final_answer}'

# why did it fail?
agent-log summary 7ede2b2f --json | jq -r '.errors[-1].text'
# Failed to authenticate. API Error: 403 Factory: Forbidden — the provider refused the request; check the model policy and plan
```

## Supported agents

| Agent | `--source` | Reads | Native override | Tested with |
|---|---|---|---|---|
| [Claude Code](https://github.com/anthropics/claude-code) | `claude` | `~/.claude/projects/<escaped-cwd>/<id>.jsonl` | `CLAUDE_CONFIG_DIR` | real sessions (2.1) |
| [Codex CLI](https://github.com/openai/codex) | `codex` | `~/.codex/sessions/**/*.jsonl`, `~/.codex/archived_sessions/**` (also `.jsonl.zst`) | `CODEX_HOME` | real sessions |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | `gemini` | `~/.gemini/tmp/<project>/chats/session-*.jsonl` (0.6x) or `session-*.json` (older) | none | real sessions (0.64) |
| [opencode](https://github.com/anomalyco/opencode) | `opencode` | `~/.local/share/opencode/opencode*.db` (SQLite, opened read-only) and the older `storage/` JSON tree | `XDG_DATA_HOME` | real sessions (1.19) |
| [droid](https://factory.ai) (Factory) | `droid` | `~/.factory/sessions/<escaped-cwd>/<id>.jsonl` | none | real sessions |
| [pi](https://github.com/earendil-works/pi) | `pi` | `~/.pi/agent/sessions/--<cwd>--/<ts>_<id>.jsonl` | `PI_CODING_AGENT_DIR`, `PI_CODING_AGENT_SESSION_DIR` | real sessions |

Every agent can also be pointed elsewhere with `AGENT_LOG_HOME_<AGENT>`, for example `AGENT_LOG_HOME_CODEX=/data/codex`.
The value replaces the agent's base directory (`~/.codex`, `~/.claude`, `~/.gemini`, `~/.local/share/opencode`, `~/.factory` or `~/.pi/agent`).

Your agent is missing? [Ask for it](https://github.com/psylch/agent-log/issues/new?template=new_agent_support.yml) or [add it](CONTRIBUTING.md#adding-a-new-agent). The smallest provider (droid) is about 35 lines.

<details>
<summary>Format notes per agent</summary>

- **Claude Code**: sidechain (subagent) records, meta messages and caveat messages are skipped. Slash commands show as `/cmd args`.
- **Codex CLI**: `task_complete` is the final answer. Reasoning is shown only where Codex stores a readable summary; encrypted reasoning cannot be read. A tool result whose output reports a non-zero exit code counts as an error.
- **Gemini CLI**: JSONL is a metadata line, then message records upserted by `id` (the last copy wins) and `$set` patches. The cwd comes from `<project>/.project_root`. The injected `<session_context>` message and `functionResponse`-only records are not prompts. A shell result with `Exit Code: N` (N ≠ 0) is an error even when its status is `success`.
- **opencode**: reads the 1.19 `session`/`message`/`part` tables, the `session_v2`/`session_message` layout and the older JSON storage tree. Subagent child sessions are hidden. Ids share their first 8 characters, so text output shows 14 (`ses_eb24f4645f`).
- **droid**: `compaction_state` records are reported as context compaction.
- **pi**: only the active branch of the session tree is shown.

</details>

## Commands

```text
agent-log list    [--active] [--since WHEN] [-n N] [--source NAME] [--project SUBSTR] [--json]
agent-log summary [SESSION] [--source NAME] [--project SUBSTR] [--json]
agent-log show    [SESSION] [--last N] [--since WHEN] [--until WHEN] [--turn N]
                  [--with-output] [--no-thinking] [--full] [--max-chars N] [-f] [--json]
agent-log tail    [SESSION] ...same flags as show; last 20 steps unless you pass a window or --all
```

| Command | What it does |
|---|---|
| `list` | Sessions of every agent, newest first. `--active` keeps only running ones. `-n 0` shows all (default 15). Bare `agent-log` runs `list`. |
| `summary` | One screen per session: status, task, latest prompt, files changed, the last errors, the last actions and the final answer. |
| `show` | The whole transcript: prompts, messages, tool calls, errors and context compactions. Thinking is shown dimmed; `--no-thinking` hides it. |
| `tail` | `show` limited to the last 20 steps. `-f` keeps printing new events while the agent works. |

- **SESSION** is `latest` (the default), an id prefix, a substring of the project path, or a session file path. opencode database sessions use `<db>#<session-id>`.
- **WHEN** is `30m`, `2h` or `1d` (ago), `HH:MM` (today), or an ISO 8601 date or time.
- A **step** is one assistant message (its text, thinking and tool calls). A **turn** starts at each user prompt.
- Common flags: `--source NAME`, `--project SUBSTR`, `--no-codex`, `--json`, `--tz TZ`, `--no-cache`, `--version`.

### Status

| `status` | Meaning | `status_reason` |
|---|---|---|
| `running` | A live agent process was matched to the session, or the file was written in the last 120 s while a process of that agent is alive. | `proc:session-id`, `proc:cwd`, `mtime<120s` |
| `done` | Idle, and the latest turn ended with a final message or with assistant text. | `final`, `ended on assistant text` |
| `failed` | Idle, and the last event is an agent or API error (for example an HTTP 403), not a failed tool call. | `ended on agent/API error` |
| `unknown` | Idle, anything else: killed, crashed, stopped on a tool call, or empty. | `idle, ended on <kind>`, `no events` |

### Exit codes

| Code | Meaning |
|---|---|
| `0` | OK. `list` also exits 0 when nothing matches, and says so |
| `1` | `summary`, `show` or `tail` found nothing: no sessions at all, or no session matches SESSION |
| `2` | Usage error: unknown flag, bad value, bad time or timezone |
| `130` | Interrupted with Ctrl-C |

### Environment

| Variable | Effect |
|---|---|
| `AGENT_LOG_HOME_<AGENT>` | Base directory for one agent (`CLAUDE`, `CODEX`, `GEMINI`, `OPENCODE`, `DROID`, `PI`). |
| `AGENT_LOG_TZ` | Display timezone: `local` (default), `UTC`, `+08:00` or an IANA name like `Europe/Berlin`. Same as `--tz`. |
| `AGENT_LOG_NO_CACHE=1` | Do not read or write the `list` cache. Same as `--no-cache`. |
| `AGENT_LOG_CACHE_DIR` | Cache location (default `$XDG_CACHE_HOME/agent-log`, else `~/.cache/agent-log`). |
| `AGENT_LOG_PROC` | Process table to read instead of `/proc`. Used by the tests and the demo. A path that does not exist disables process matching. |
| `NO_COLOR` | Disable colors. Colors are also off when stdout is not a terminal. |

## JSON output

Every command takes `--json`. The output is meant to be read by programs, and it stays stable:

> **Stability promise.** Every object has `"schema_version": 1`. New keys may be added in any release.
> Existing keys keep their name, type and meaning while `schema_version` is 1. Renaming or removing a key bumps it.

All timestamps are ISO 8601 with an offset, in the display timezone (`--tz`, default local).
Every key is always present; unknown values are `null` or `[]`.

### `summary --json`

One session object:

| Key | Type | Meaning |
|---|---|---|
| `schema_version` | int | `1` |
| `source` | string | `claude`, `codex`, `gemini`, `opencode`, `droid` or `pi` |
| `session_id` | string | The agent's own session id |
| `path` | string | Session file (opencode database: `<db>#<id>`) |
| `cwd` | string or null | Project directory the agent ran in |
| `status` | string | `running`, `done`, `failed` or `unknown` (see [Status](#status)) |
| `status_reason` | string | Why that status was chosen |
| `started` | string or null | Time of the first event |
| `updated` | string or null | Last write to the session file or rows |
| `steps` | int | Assistant messages |
| `turns` | int | User prompts |
| `task` | string or null | The first prompt, up to 600 characters |
| `files_changed` | [string] | Paths written by edit, write or patch tools, in first-touch order. An edit whose own result is an error is not counted |
| `error_count` | int | All errors in the session |
| `errors` | [{`ts`, `tool`, `text`}] | The last 10 errors: failed tool results and agent errors. `tool` is null for agent errors. `text` up to 300 characters |
| `last_actions` | [{`ts`, `kind`, `text`}] | The last 6 tool calls (`tool_call`) and messages (`say`). `text` up to 200 characters |
| `final_answer` | string or null | The latest turn's final message, or the last assistant text once the session is idle. Null while a turn is in progress |

Real output (`agent-log summary 134298f6 --json`):

```json
{
  "schema_version": 1,
  "source": "claude",
  "session_id": "134298f6-a5a5-45ef-ad9e-e4b2524e1076",
  "path": "/tmp/agent-log-demo/.claude/projects/-fx-claude-real/134298f6-a5a5-45ef-ad9e-e4b2524e1076.jsonl",
  "cwd": "/fx/claude-real",
  "status": "done",
  "status_reason": "ended on assistant text",
  "started": "2026-10-18T06:23:44+00:00",
  "updated": "2026-10-18T06:23:55+00:00",
  "steps": 6,
  "turns": 1,
  "task": "Read notes.txt. Fix the bug in calc.py so add returns a+b. Then run: python3 -c 'import calc; print(calc.add(2,3))'. Then run: cat missing_file.txt (it will fail, that's expected). Reply DONE briefly.",
  "files_changed": [
    "/fx/claude-real/calc.py"
  ],
  "error_count": 1,
  "errors": [
    {
      "ts": "2026-10-18T06:23:50+00:00",
      "tool": "Bash",
      "text": "Exit code 1 cat: missing_file.txt: No such file or directory"
    }
  ],
  "last_actions": [
    { "ts": "2026-10-18T06:23:48+00:00", "kind": "say", "text": "Now I'll fix the bug in calc.py:" },
    { "ts": "2026-10-18T06:23:48+00:00", "kind": "tool_call", "text": "Edit /fx/claude-real/calc.py" },
    { "ts": "2026-10-18T06:23:50+00:00", "kind": "say", "text": "Now running the commands:" },
    { "ts": "2026-10-18T06:23:50+00:00", "kind": "tool_call", "text": "Bash $ python3 -c 'import calc; print(calc.add(2,3))'" },
    { "ts": "2026-10-18T06:23:50+00:00", "kind": "tool_call", "text": "Bash $ cat missing_file.txt" },
    { "ts": "2026-10-18T06:23:52+00:00", "kind": "say", "text": "DONE. Fixed calc.py (a - b → a + b), test returned 5 correctly, and missing_file.txt failed as expected." }
  ],
  "final_answer": "DONE. Fixed calc.py (a - b → a + b), test returned 5 correctly, and missing_file.txt failed as expected."
}
```

(`last_actions` is folded onto one line per item here; the real output is indented normally.)

### `list --json`

`{"schema_version": 1, "sessions": [...]}`. Each item has the summary keys except `error_count`, `errors`, `last_actions` and `final_answer`, which would need every file to be read in full.

### `show --json` and `tail --json`

`{"schema_version": 1, "session": {...list item...}, "events": [...]}`. Each event:

| Key | Type | Meaning |
|---|---|---|
| `ts` | string or null | Event time |
| `kind` | string | `user`, `say`, `think`, `tool_call`, `tool_result`, `error`, `compact` or `final`. A failed tool result is reported as `error` |
| `tool` | string or null | Tool name for tool calls, results and tool errors |
| `text` | string | For `tool_call`, a one-line summary such as `Bash $ npm test` or `Edit /path/file`. `tool_result`, `error` and `compact` text is clipped to 4 × `--max-chars` (1200 by default); `--full` disables that |
| `paths` | [string] | Files a `tool_call` writes |
| `step`, `turn` | int | Step and turn numbers, as in the text output |

The window flags (`--last`, `--since`, `--until`, `--turn`) and `--no-thinking` apply. `--follow` is text only.

## Use with orchestrators

agent-log answers the question an orchestrator keeps asking: *is it done yet, and how did it go?*
These recipes use the [`examples/`](examples) folder. They need `agent-log` and `jq` on your `PATH`.

### 1. Launch in the background, poll, decide

Claude Code lets you choose the session id up front, which makes the lookup exact:

```sh
id=$(python3 -c 'import uuid; print(uuid.uuid4())')
claude -p --session-id "$id" "Fix the failing tests" > /dev/null 2>&1 &
pid=$!

until agent-log summary "$id" --json > /dev/null 2>&1; do sleep 5; done   # wait for the first write
examples/wait-for-agent.sh "$id"                                          # polls every 30 s, prints the summary JSON
```

Agents without a session-id flag: look up the newest session in the project that was written after the launch.

```sh
start=$(date -u +%Y-%m-%dT%H:%M:%SZ)
codex exec --full-auto "Bump the dependencies" > /dev/null 2>&1 &
pid=$!
until id=$(agent-log list --source codex --project "$(pwd -P)" --since "$start" -n 1 --json |
           jq -re '.sessions[0].session_id // empty'); do sleep 5; done
examples/wait-for-agent.sh "$id" --source codex
```

[`wait-for-agent.sh`](examples/wait-for-agent.sh) exits `0` when the session is `done`, `1` when it is `failed` or `unknown`,
and `3` when the session is still marked running but has written nothing for `STALL` seconds (default 600).
That last case is where your orchestrator decides whether to interrupt the agent (`kill "$pid"`) or keep waiting.

From Python, the same check is one subprocess call:

```python
import json, subprocess

def agent_status(session, source=None):
    cmd = ["agent-log", "summary", session, "--json"] + (["--source", source] if source else [])
    r = subprocess.run(cmd, capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 else None   # 1 = not found (yet)

s = agent_status("134298f6")
if s and s["status"] != "running":
    print(s["status"], s["files_changed"], s["final_answer"])
```

### 2. Get notified when the agent finishes

[`notify-on-done.sh`](examples/notify-on-done.sh) runs the agent, finds the session it created and sends the summary when it exits.
Use it to wake up an orchestrator, a chat bot or yourself:

```sh
# print the summary JSON (plus "exit_code") when the run ends
examples/notify-on-done.sh claude -p "Fix the failing tests"

# POST it to a webhook, shaped as a chat message
NOTIFY_URL=https://example.com/hooks/agents \
NOTIFY_JQ='{text: "\(.source) \(.status): \(.final_answer // "no final answer")"}' \
  examples/notify-on-done.sh codex exec --full-auto "Bump the dependencies" &

# or hand it to any command on stdin
NOTIFY_CMD='my-bot wake --stdin' examples/notify-on-done.sh droid exec "Add a CHANGELOG entry" &
```

The core of it is three lines:

```sh
start=$(date -u +%Y-%m-%dT%H:%M:%SZ); "$@"
id=$(agent-log list --project "$(pwd -P)" --since "$start" -n 1 --json | jq -r '.sessions[0].session_id // empty')
[ -n "$id" ] && agent-log summary "$id" --json | curl -fsS -H 'Content-Type: application/json' --data-binary @- "$NOTIFY_URL"
```

### 3. Teach your agent to use it

Give the orchestrating agent the skill in [`examples/skill/SKILL.md`](examples/skill/SKILL.md).
For Claude Code, copy it to `~/.claude/skills/agent-log/SKILL.md`. Agents without skill support can take the same text in `AGENTS.md`, `CLAUDE.md` or `GEMINI.md`. The short version:

```markdown
## Checking on background coding agents
- Use `agent-log`, never the raw session files under ~/.claude, ~/.codex, ~/.gemini, ~/.factory, ~/.pi or ~/.local/share/opencode.
- `agent-log list --active --json` shows what is running. `agent-log summary <id-or-project> --json` gives
  status, files_changed, errors, last_actions and final_answer for one session.
- status: running = check again in 30-60 s; done = read final_answer; failed = read errors[-1];
  unknown = it stopped without an answer (killed, crashed, or ended on a tool call).
- `agent-log tail <id> --last 5` shows the last steps when you need detail.
```

### 4. MCP

There is no MCP server yet. Any agent that can run a shell command can use the CLI today, and `--json` keeps the output small.
An MCP server is on the [roadmap](#roadmap); upvote or comment in the issues if you need it.

## Why not X?

There are good tools for reading agent sessions. Most are built for people looking back at their history. agent-log is built for a program checking on a run *right now*.

| Tool | Best at | Output | Runs as |
|---|---|---|---|
| **agent-log** | "Is it done, what changed, what failed?" for one session, from a script or another agent | One screen of text or one JSON object, with a status | One Python file, standard library only, no index or daemon |
| [cass](https://github.com/Dicklesworthstone/coding_agent_session_search) | Searching all your past sessions across many agents | TUI, plus a JSON robot mode for agents | Rust binary with a search index |
| [SpecStory CLI](https://github.com/specstoryai/getspecstory) | Keeping your AI conversations as searchable Markdown | `.specstory/history/*.md`, optional cloud sync | Go binary |
| [Agent Sessions](https://github.com/jazzyalex/agent-sessions) | Browsing, searching and resuming sessions with a GUI | Desktop app | Native macOS app |
| [ccusage](https://github.com/ccusage/ccusage) | Token usage and cost | Usage reports | `npx ccusage` |
| `tail -f` + `jq` on the raw files | Anything, if you write it | Raw records, different for each agent and version | Already installed |

Use cass or SpecStory to search your history, ccusage to watch cost, and agent-log when a script needs a status.
They work fine side by side. agent-log is deliberately small: nothing to set up, no index to build, nothing kept besides an optional cache, so a bot can call it every 30 seconds.

## FAQ and limitations

**The agents' formats are undocumented. Will this break?**
Probably, now and then. agent-log is tested against real sessions from current versions (see [Supported agents](#supported-agents)) and against synthetic ones for older layouts. One malformed record never drops a whole session. When an agent update breaks parsing, please [open an issue](https://github.com/psylch/agent-log/issues/new?template=bug_report.yml). Your own scripts are protected by the JSON `schema_version`.

**How does it know an agent is still running?**
It looks for live agent processes in `/proc` (Linux) or with `ps` and `lsof` (macOS), including `node`, `bun` and `deno` wrappers. A process that names its session (`--session-id`, `resume <id>` and similar) claims that session. Otherwise it claims the newest session of the same agent whose project directory matches the process's working directory. As a fallback, a session written in the last 120 seconds counts as running, but only while some process of that agent is alive. These are heuristics:

- If you run two sessions of the same agent in the same directory, the newest one is matched first. Pass a session id where the agent allows it (`claude --session-id`) to be exact.
- Right after a run ends, its session can read `running (mtime<120s)` for up to two minutes if another process of the same agent is still alive, for example an interactive `claude` in another terminal. [`notify-on-done.sh`](examples/notify-on-done.sh) avoids this because it knows the process exited.
- If the agent runs in another container or on another machine, agent-log cannot see its process. Set `AGENT_LOG_PROC` to a path that does not exist to rely on the recent-write check alone.

**Why is there no thinking for Codex sessions?**
Codex stores most of its reasoning encrypted. agent-log shows the reasoning summaries Codex keeps in plain text, when there are any.

**Does it send my data anywhere?**
No. agent-log only reads local files and never opens a network connection. It never modifies session files (opencode databases are opened read-only). The only thing it writes is a small cache for `list` in `~/.cache/agent-log` (mode 0600), which holds the id, project path, first prompt and changed file names of each session. Use `--no-cache` or `AGENT_LOG_NO_CACHE=1` to turn it off.

**Is it fast with thousands of sessions?**
`list` sorts by modification time and parses only the rows it shows, and it caches what it parsed, keyed by file size and modification time.

**Is this an orchestrator?**
No. Tools like claude-squad, vibe-kanban or your own bot start and manage agents. agent-log only reports on them, so it works alongside any of them.

**Windows?**
Not tested. CI runs on Linux and macOS.

## Roadmap

Ideas, not promises. Comments and pull requests are welcome.

- An MCP server exposing `list` and `summary` as tools
- A `watch` command that waits for a session to finish (what `examples/wait-for-agent.sh` does today)
- PyPI and Homebrew packages
- More agents: Cursor CLI, Copilot CLI, Aider, Qwen Code, Amp

## Contributing

Bug reports, fixes for new agent versions and support for new agents are all welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the dev setup, the fixture privacy rules and how to add an agent.

```sh
python3 -m unittest discover -s tests
```

**Add your agent.** If your favorite coding agent writes session logs, a provider is a small class in `agent_log.py` plus one sanitized fixture. [Open a request](https://github.com/psylch/agent-log/issues/new?template=new_agent_support.yml) if you would rather describe the format and let someone else write it.

## Credits

The knowledge of the session formats comes from [SpecStory CLI](https://github.com/specstoryai/getspecstory) (Apache-2.0), which was used as a format reference. No code was copied: agent-log is an independent Python implementation.

## License

[MIT](LICENSE)
