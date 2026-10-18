#!/bin/sh
# Run a coding agent, then send its agent-log summary somewhere when it exits ("wake me when it's done").
#
#   examples/notify-on-done.sh claude -p "Fix the failing tests"
#   NOTIFY_URL=https://example.com/hook examples/notify-on-done.sh codex exec --full-auto "Bump deps"
#   NOTIFY_CMD='my-bot wake' examples/notify-on-done.sh droid exec "Add a CHANGELOG entry" &
#
# Environment:
#   NOTIFY_URL  POST the payload (JSON) to this URL with curl. Unset: print the payload on stdout.
#   NOTIFY_CMD  run this command with the payload on stdin instead (another agent, a bot CLI, ...).
#   NOTIFY_JQ   jq filter that shapes the payload (default: the summary plus "exit_code").
#               Slack/Discord-style text: NOTIFY_JQ='{text: "\(.source) \(.status): \(.final_answer // "no final answer")"}'
#
# Needs agent-log and jq on PATH. The agent's own output goes to stderr so stdout stays clean JSON.
set -u
[ $# -gt 0 ] || { echo "usage: $0 <agent command> [args...]" >&2; exit 2; }

start=$(date -u +%Y-%m-%dT%H:%M:%SZ)
"$@" 1>&2
rc=$?

case "$(basename "$1")" in
  claude|codex|gemini|opencode|droid|pi) src=$(basename "$1") ;;
  *) src= ;;
esac

# the newest session of that agent, in this directory, written since the command started
id=$(agent-log list ${src:+--source "$src"} --project "$(pwd -P)" --since "$start" -n 1 --json --no-cache |
     jq -r '.sessions[0].session_id // empty')

if [ -n "$id" ]; then
  # The agent has exited, so skip live-process detection: an empty process table keeps another
  # running instance of the same agent from making this just-written session look "running".
  noproc=$(mktemp -d)
  summary=$(AGENT_LOG_PROC=$noproc agent-log summary "$id" ${src:+--source "$src"} --json)
  rmdir "$noproc"
else
  summary='{"status": "unknown", "status_reason": "no session found for this run"}'
fi
payload=$(printf '%s' "$summary" | jq --argjson rc "$rc" ". + {exit_code: \$rc} | ${NOTIFY_JQ:-.}")

if [ -n "${NOTIFY_CMD:-}" ]; then
  printf '%s\n' "$payload" | sh -c "$NOTIFY_CMD"
elif [ -n "${NOTIFY_URL:-}" ]; then
  curl -fsS -X POST -H 'Content-Type: application/json' --data-binary "$payload" "$NOTIFY_URL" >/dev/null
else
  printf '%s\n' "$payload"
fi
exit "$rc"
