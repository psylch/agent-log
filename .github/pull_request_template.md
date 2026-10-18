## What and why

<!-- What does this change, and what problem does it solve? Link the issue: Fixes #123 -->

## Checklist

- [ ] `python3 -m unittest discover -s tests` passes
- [ ] `uvx ruff check .` passes
- [ ] New behavior or a new session format has a test (and a fixture under `tests/fixtures/home` if needed)
- [ ] Fixtures are sanitized: only `/fx/...` paths, no emails, usernames, hostnames, tokens or keys
- [ ] JSON output stays backward compatible (keys only added, never renamed or removed)
- [ ] User-visible changes are noted under `## [Unreleased]` in `CHANGELOG.md`
- [ ] Docs updated if needed (module docstring, README)
