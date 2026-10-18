# Contributing to agent-log

Thanks for helping. Bug reports, format fixes for new agent versions and support for new agents are
all very welcome. Small focused pull requests get merged fastest.

## Dev setup

agent-log is one file, `agent_log.py`, with no runtime dependencies. Python 3.9 or newer is enough.

```sh
git clone https://github.com/psylch/agent-log
cd agent-log
python3 agent_log.py --help          # run it straight from the checkout
pip install -e .                     # optional: puts `agent-log` on your PATH
```

## Running the tests

```sh
python3 -m unittest discover -s tests
uvx ruff check .                     # optional lint, same config as CI
```

The tests never touch your real sessions. They copy `tests/fixtures/home` to a temp directory and set
`HOME`, `AGENT_LOG_PROC` and `AGENT_LOG_TZ` so results do not depend on your machine.
`tests/bench.py` is a synthetic benchmark for `list` and is not part of the suite.

CI runs the suite on Linux and macOS for Python 3.9 to 3.13. Please keep the code 3.9-compatible
(no `match`, no `X | Y` type unions at runtime, no `zip(strict=...)`).

## Adding a new agent

Everything lives in `agent_log.py`:

1. **Provider class.** Under `# ---- sessions & providers ----`, subclass `Provider`. `Droid` is the
   shortest example. Set `name` and `procs` (process names for running detection) and implement:
   - `roots()`: the directories you read, using `base_dir("<name>", "<NATIVE_ENV_VAR>", ".<agent>")` so
     `AGENT_LOG_HOME_<NAME>` works.
   - `discover()`: the session file paths.
   - `meta(path)`: `(session_id, cwd)` from the head of the file. Raise if the file is not a session.
   - `events(path)`: a list of `E(ts, kind, text, ...)` events. `kind` is one of `KINDS`: `user`, `say`,
     `think`, `tool_call` (build it with `tool_ev`), `tool_result` (pass `err=True` for failures),
     `error`, `compact` or `final`. Wrap per-record parsing in `each()` so one bad record cannot sink
     the session.
2. **Register it** in `PROVIDERS`, add a path marker to `sniff()`, and add a `SCRIPT_HINTS` entry if
   the agent runs as a `node`/`bun` script.
3. **Document it** in the module docstring (the "Sources" list) and the "Supported agents" table of
   `README.md` and `README.zh-CN.md`.

If you change the text output, refresh the README samples (`tests/test_readme.py` compares them with the
output on the demo home from `examples/demo_home.py`) and regenerate the demo image with
`python3 docs/assets/make_demo_svg.py`.

## Fixtures

Add a session under `tests/fixtures/home/` at the same relative path the agent uses (for example
`tests/fixtures/home/.newagent/sessions/<id>.jsonl`), then add a row to `EXPECT` in
`tests/test_agent_log.py` with the counts you expect. A short real session is better than a hand-written
one, but it must be sanitized first.

**Fixture privacy rules.** Fixtures are public forever once merged, so before you commit:

- Every path is a fake `/fx/...` path (`/fx/myagent`, `/fx/myagent/app.py`). No `/home/<you>`,
  `/Users/<you>`, `/root`, `/workspace` or temp directories, including inside prompts and tool output.
- No emails, usernames (watch `ls -l` and `whoami` output), hostnames, IP addresses, ports, git
  remotes, organization names, or the names of private proxies and gateways.
- No API keys, tokens, cookies, auth headers or request ids that could identify an account.
- Keep prompts boring (`Fix calc.py so add returns a+b`). Drop records you do not need for the test.
- Check with something like `rg --hidden -n -i '/home/|/Users/|/root|@|token|key' tests/fixtures`.
  `--hidden` matters: fixture homes are dot-directories (`.claude`, `.codex`), which `rg` skips by default.

## Commits and pull requests

- One topic per pull request, with a test for every behavior change or new format.
- `python3 -m unittest discover -s tests` and `uvx ruff check .` pass.
- Keep the JSON schema backward compatible: adding keys is fine, renaming or removing keys needs
  a `schema_version` bump and a discussion first.
- Add a line under `## [Unreleased]` in `CHANGELOG.md` for user-visible changes.
- Commit messages: a short imperative summary line (`Add Aider support`, `Fix Codex archive parsing`),
  then details in the body if needed.

By contributing you agree that your work is released under the [MIT License](LICENSE), and to follow the
[Code of Conduct](CODE_OF_CONDUCT.md).
