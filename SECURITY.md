# Security policy

## What agent-log touches

agent-log only reads files on your own machine. It opens agent session logs read-only (SQLite
databases in read-only mode), lists local processes to detect running agents, and writes nothing except
an optional cache in `$XDG_CACHE_HOME/agent-log` (created with `0700`/`0600` permissions, since it holds
prompt snippets). It makes no network requests and has no runtime dependencies.

Session logs can contain prompts, code and secrets. Treat agent-log output the same way you treat the
logs themselves, especially when you forward it to a bot or chat.

## Supported versions

Security fixes go into the latest release.

## Reporting a vulnerability

Please do not open a public issue for security problems. Report them privately through
[GitHub security advisories](https://github.com/psylch/agent-log/security/advisories/new).
Include the version (`agent-log --version`), your OS, and steps to reproduce.
You should get a reply within a week.
