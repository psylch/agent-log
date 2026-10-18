---
name: agent-log
description: Check on coding agents you launched in the background (Claude Code, Codex CLI, Gemini CLI, opencode, droid, pi). Use when you need to know whether a delegated agent is still running, what it changed, what failed, or what it answered, without reading its raw session logs.
---

# Checking on background coding agents with agent-log

`agent-log` reads the session logs that coding agents write on this machine and prints a short
summary or stable JSON. It is read-only and never changes the sessions.

## When to use it

- You started an agent with `claude -p`, `codex exec`, `gemini -p`, `opencode run`, `droid exec` or `pi`
  and need its status, the files it touched, its errors or its final answer.
- A user asks "what is the agent in ~/project doing?" or "did that run finish?".

Do not open the raw JSONL/JSON/SQLite session files yourself. They are large and change between
agent versions. Use `agent-log` instead.

## Commands

```sh
agent-log list --json -n 10                         # newest sessions of every agent
agent-log list --active --json                      # only sessions that are running now
agent-log summary <id-or-project> --json            # one session: status, task, files, errors, final answer
agent-log tail <id-or-project> --last 5             # the last 5 steps as text, for detail
agent-log summary latest --source codex --project "$PWD" --json
```

`<id-or-project>` can be an id prefix (8 characters is usually enough), a substring of the project
path, `latest`, or a session file path. Add `--source claude|codex|gemini|opencode|droid|pi` to narrow it.

## Reading the result

- `status`: `running` (still working, check again later), `done` (finished; read `final_answer`),
  `failed` (stopped on an agent or API error; read `errors`), `unknown` (idle without a final answer:
  probably killed, crashed or stopped on a tool call).
- `files_changed`: paths the agent edited or wrote. `error_count` / `errors`: failed tool calls and agent errors.
  A few failed tool calls are normal; the agent usually recovers.
- `last_actions`: the last tool calls and messages, to see what it is doing right now.
- `updated`: the last write. A `running` session with no writes for 10+ minutes may be stuck.
- Exit codes: 0 ok, 1 no session found, 2 bad arguments.

## Rules

- Poll at most every 30 to 60 seconds. Each call reads the session from disk.
- Report to the user in one or two lines: status, files changed, the final answer or the last error.
- Do not interrupt a `running` agent only because it has tool errors. Interrupt it when it is stuck or
  clearly working on the wrong thing, and say why.
