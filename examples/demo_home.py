#!/usr/bin/env python3
"""Build a throwaway demo $HOME from the test fixtures, so you can try agent-log without any agent installed.

    python3 examples/demo_home.py /tmp/agent-log-demo
    export HOME=/tmp/agent-log-demo AGENT_LOG_PROC=/tmp/agent-log-demo/.proc AGENT_LOG_NO_CACHE=1
    agent-log                      # or: python3 agent_log.py

It copies tests/fixtures/home, builds the opencode SQLite databases, sets each session file's mtime to
its last event, and adds a fake process table with one live `claude` process, so one session reads as
running. Nothing outside the target directory is touched. The README demo output comes from this.
"""
import os, shutil, sqlite3, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures")
sys.path.insert(0, ROOT)


def build(home):
    if os.path.exists(home):
        if not os.path.isfile(os.path.join(home, ".agent-log-demo")):
            sys.exit(f"{home} exists and is not an agent-log demo home; pick a new directory")
        shutil.rmtree(home)
    shutil.copytree(os.path.join(FIX, "home"), home)
    open(os.path.join(home, ".agent-log-demo"), "w").close()
    oc = os.path.join(home, ".local", "share", "opencode")
    for sql, db in (("opencode_db.sql", "opencode.db"), ("opencode_v1_db.sql", "opencode-v1.db")):
        con = sqlite3.connect(os.path.join(oc, db))
        with open(os.path.join(FIX, sql), encoding="utf-8") as f: con.executescript(f.read())
        con.commit(); con.close()

    # one live Claude Code process (pid 4242) started in /fx/claude, the way `claude -p ...` looks in /proc
    proc = os.path.join(home, ".proc", "4242")
    os.makedirs(proc)
    with open(os.path.join(proc, "cmdline"), "wb") as f: f.write(b"claude\0-p\0/review now\0")
    os.symlink("/fx/claude", os.path.join(proc, "cwd"))

    os.environ["HOME"] = home
    for k in [k for k in os.environ if k.startswith("AGENT_LOG_HOME_")] + ["CODEX_HOME", "CLAUDE_CONFIG_DIR", "XDG_DATA_HOME",
                                                                           "PI_CODING_AGENT_DIR", "PI_CODING_AGENT_SESSION_DIR"]:
        os.environ.pop(k, None)
    import agent_log as al
    al.CACHE = al.Cache(None)
    stamps = {}
    for s in al.discover():
        if not s.ok: continue
        ts = [e["t"] for e in s.events() if e["t"]]
        if not ts: continue
        f = s.path.rpartition("#")[0] if ".db#" in s.path else s.path
        stamps[f] = max(stamps.get(f, 0), max(ts) + 3)
    # everything else (directories, opencode message dirs) gets the oldest time, so only the sessions set the order
    old = min(stamps.values()) - 86400
    for dp, ds, fs in os.walk(home):
        for fn in fs + ds:
            p = os.path.join(dp, fn)
            if not os.path.islink(p): os.utime(p, (old, old))
    for f, t in stamps.items(): os.utime(f, (t, t))


if __name__ == "__main__":
    if len(sys.argv) != 2: sys.exit(__doc__)
    home = os.path.abspath(sys.argv[1])
    build(home)
    print(f"demo home ready. Try:\n  export HOME={home} AGENT_LOG_PROC={home}/.proc AGENT_LOG_NO_CACHE=1\n  agent-log")
