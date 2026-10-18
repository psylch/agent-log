#!/bin/sh
# Poll one agent session until it stops running, then print its summary JSON.
# An orchestrator can run this (or copy the loop) instead of reading raw session logs.
#
#   examples/wait-for-agent.sh 134298f6            # id prefix, "latest", a project substring or a file path
#   POLL=10 STALL=300 examples/wait-for-agent.sh latest --source codex
#
# Environment: POLL seconds between checks (default 30), STALL seconds without a write before a
# "running" session counts as stuck (default 600, 0 = never).
# Exit codes: 0 done, 1 failed or unknown (crashed, killed, ended on a tool call), 2 usage or not found,
# 3 stalled (still marked running but silent for STALL seconds; decide whether to interrupt it).
#
# Needs agent-log and jq on PATH.
set -u
# an empty SESSION (say, from a failed lookup) would silently mean "latest"
[ $# -gt 0 ] && [ -n "$1" ] || { echo "usage: $0 SESSION [agent-log options such as --source NAME]" >&2; exit 2; }
POLL=${POLL:-30}; STALL=${STALL:-600}
export AGENT_LOG_TZ=UTC  # "+00:00" offsets, which jq's fromdateiso8601 can read once turned into "Z"

while :; do
  s=$(agent-log summary "$@" --json) || exit 2
  status=$(printf '%s' "$s" | jq -r .status)
  if [ "$status" != running ]; then
    printf '%s\n' "$s"
    [ "$status" = done ] && exit 0 || exit 1
  fi
  idle=$(printf '%s' "$s" | jq -r '(now - (.updated | sub("\\+00:00$"; "Z") | fromdateiso8601)) | floor')
  if [ "$STALL" -gt 0 ] && [ "$idle" -ge "$STALL" ]; then
    printf '%s\n' "$s"
    echo "wait-for-agent: still running but no writes for ${idle}s" >&2
    exit 3
  fi
  sleep "$POLL"
done
