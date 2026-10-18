import contextlib, importlib.util, io, json, os, re, shutil, sqlite3, subprocess, sys, tempfile, time, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "agent_log.py")
FIX = os.path.join(ROOT, "tests", "fixtures")

_spec = importlib.util.spec_from_file_location("agent_log", SCRIPT)
al = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(al)
al.USE_COLOR = False

NATIVE_ENV = ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "XDG_DATA_HOME", "PI_CODING_AGENT_DIR", "PI_CODING_AGENT_SESSION_DIR")
SUMMARY_KEYS = {"schema_version", "source", "session_id", "path", "cwd", "status", "status_reason", "started", "updated",
                "steps", "turns", "task", "files_changed", "errors", "last_actions", "final_answer"}
LIST_KEYS = SUMMARY_KEYS - {"errors", "last_actions", "final_answer"}
ISO_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$")
CODEX_ZST_JSONL = (json.dumps({"timestamp": "2026-10-02T03:00:00Z", "type": "session_meta", "payload": {"cwd": "/fx/codex-zst"}}) + "\n" +
                   json.dumps({"timestamp": "2026-10-02T03:00:01Z", "type": "response_item", "payload": {"type": "message", "role": "user",
                               "content": [{"type": "input_text", "text": "archived prompt"}]}}) + "\n").encode()

# (provider, cwd, id, steps, turns, files_changed, error_count, compact_count, status)
EXPECT = [
    ("droid", "/fx/droid", "11111111-1111-4111-8111-111111111111", 3, 1, ["/fx/droid/app.py"], 1, 1, "done"),
    ("codex", "/fx/codex", "22222222-2222-4222-8222-222222222222", 3, 1, ["README.md"], 1, 1, "done"),
    ("claude", "/fx/claude", "44444444-4444-4444-8444-444444444444", 3, 2, ["/fx/claude/CHANGELOG.md"], 1, 1, "done"),
    ("gemini", "/fx/gemini", "55555555-5555-4555-8555-555555555555", 2, 1, ["/fx/gemini/lib.py"], 1, 0, "done"),
    ("opencode", "/fx/opencode", "ses_legacy1", 2, 1, ["/fx/opencode/notes.txt"], 1, 0, "done"),
    ("opencode", "/fx/opencode-db", "ses_db1", 1, 1, ["a"], 1, 1, "failed"),
    ("pi", "/fx/pi", "66666666-6666-4666-8666-666666666666", 3, 2, ["/fx/pi/config.json"], 1, 1, "done"),
    # real sessions recorded on the box through the magpie gateway (paths sanitized to /fx/*-real)
    ("claude", "/fx/claude-real", "134298f6-a5a5-45ef-ad9e-e4b2524e1076", 6, 1, ["/fx/claude-real/calc.py"], 1, 0, "done"),
    ("claude", "/fx/claude-real", "7ede2b2f-bb02-44bf-b388-1f38d9a9f06b", 7, 1, [], 6, 0, "failed"),
    ("gemini", "/fx/gemini-real", "d9840123-71e8-4ec2-bc0e-d95e08e96de0", 4, 1, ["/fx/gemini-real/calc.py"], 1, 0, "done"),
    ("opencode", "/fx/opencode-real", "ses_eb24f4645ffe0DUv709DVDipk1", 5, 1, ["/fx/opencode-real/calc.py"], 1, 0, "done"),
    ("opencode", "/fx/opencode-real", "ses_eb2512259ffeq4dCOrlSF5d0FF", 0, 1, [], 1, 0, "failed"),
]

class AgentLogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="agent-log-test-")
        cls.home = os.path.join(cls.tmp, "home")
        cls.proc = os.path.join(cls.tmp, "proc")
        shutil.copytree(os.path.join(FIX, "home"), cls.home)
        os.makedirs(cls.proc)
        db = sqlite3.connect(os.path.join(cls.home, ".local/share/opencode/opencode.db"))
        with open(os.path.join(FIX, "opencode_db.sql")) as f: db.executescript(f.read())
        db.commit(); db.close()
        db = sqlite3.connect(os.path.join(cls.home, ".local/share/opencode/opencode-v1.db"))  # real OpenCode 1.19 schema
        with open(os.path.join(FIX, "opencode_v1_db.sql")) as f: db.executescript(f.read())
        db.commit(); db.close()
        old = time.time() - 7 * 86400  # well outside the "recently written" window
        for i, (dp, ds, fs) in enumerate(os.walk(cls.home)):
            for fn in fs + ds: os.utime(os.path.join(dp, fn), (old + i, old + i))
        cls.saved_env = dict(os.environ)
        for k in list(os.environ):
            if k in NATIVE_ENV or k.startswith("AGENT_LOG_"): del os.environ[k]
        os.environ.update(HOME=cls.home, AGENT_LOG_PROC=cls.proc, NO_COLOR="1")
        cls.env = dict(os.environ)

    @classmethod
    def tearDownClass(cls):
        os.environ.clear(); os.environ.update(cls.saved_env)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def cli(self, *args):
        r = subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def cli_json(self, *args): return json.loads(self.cli(*args, "--json"))

    def session(self, provider, sid):
        return next(s for s in al.PROVIDERS[provider].sessions() if s.id == sid)

    # ---- parsing ----
    def test_parse_each_provider(self):
        for prov, cwd, sid, steps, turns, files, nerr, ncomp, status in EXPECT:
            with self.subTest(prov=prov, sid=sid):
                s = self.session(prov, sid)
                n = al.PROVIDERS[prov].parse(s.path)
                self.assertEqual((n["id"], n["cwd"], n["source"]), (sid, cwd, prov))
                ev = n["events"]
                self.assertTrue(set(e["k"] for e in ev) <= set(al.KINDS))
                self.assertEqual(al.stats(ev)[:2], (steps, turns))
                self.assertEqual(al.files_changed(ev), files)
                self.assertEqual(sum(e["k"] == "error" for e in ev), nerr)
                self.assertEqual(sum(e["k"] == "compact" for e in ev), ncomp)
                self.assertEqual(al.status_of(s, ev)[0], status)
                self.assertIsNotNone(n["started"]); self.assertIsNotNone(n["updated"])

    def test_parse_details(self):
        texts = lambda prov, sid: [e["text"] for e in al.load(self.session(prov, sid))]
        droid = texts("droid", EXPECT[0][2])
        self.assertNotIn("<system-reminder>ignore me</system-reminder>", droid)
        codex = al.load(self.session("codex", EXPECT[1][2]))
        self.assertIn("shell $ cat README.md", [e["text"] for e in codex])
        self.assertEqual(codex[-1]["k"], "final")
        claude = al.load(self.session("claude", EXPECT[2][2]))
        self.assertEqual([e["text"] for e in claude if e["k"] == "user"], ["Write a CHANGELOG entry", "/review now"])
        self.assertNotIn("sidechain noise", [e["text"] for e in claude])
        self.assertEqual(next(e["tool"] for e in claude if e["k"] == "error"), "Bash")
        gem = al.load(self.session("gemini", EXPECT[3][2]))
        self.assertEqual(next(e["tool"] for e in gem if e["k"] == "error"), "run_shell_command")
        self.assertNotIn("info messages are ignored", [e["text"] for e in gem])
        oc = [e for e in al.load(self.session("opencode", "ses_legacy1")) if e["k"] == "user"]
        self.assertEqual([e["text"] for e in oc], ["Create notes.txt"])
        ocdb = al.load(self.session("opencode", "ses_db1"))
        self.assertEqual(ocdb[-1]["text"], "invalid model")
        pi = texts("pi", EXPECT[6][2])
        self.assertNotIn("abandoned branch answer", pi)
        self.assertIn("Actually also run tests", pi)

    def test_opencode_skips_child_sessions(self):
        ids = {s.id for s in al.PROVIDERS["opencode"].sessions()}
        self.assertEqual(ids, {"ses_legacy1", "ses_db1", "ses_eb24f4645ffe0DUv709DVDipk1", "ses_eb2512259ffeq4dCOrlSF5d0FF"})

    # ---- list ----
    def test_list_text(self):
        out = self.cli("list", "-n", "50")
        self.assertTrue(out.startswith("last(+08)"))
        rows = [l.split() for l in out.splitlines()[1:]]
        for prov, cwd, sid, *_ in EXPECT:
            self.assertIn([prov, al.short_id(sid), cwd], [[r[2], r[3], r[-1]] for r in rows if len(r) > 4])

    def test_list_json(self):
        d = self.cli_json("list", "-n", "50")
        self.assertEqual(d["schema_version"], 1)
        self.assertEqual(len(d["sessions"]), len(EXPECT))
        for item in d["sessions"]:
            self.assertTrue(LIST_KEYS <= set(item), LIST_KEYS - set(item))
            self.assertIn(item["status"], ("running", "done", "failed", "unknown"))
            self.assertRegex(item["updated"], ISO_RE); self.assertRegex(item["started"], ISO_RE)
        got = {(i["source"], i["session_id"]): i for i in d["sessions"]}
        for prov, cwd, sid, steps, turns, files, _, _, status in EXPECT:
            i = got[(prov, sid)]
            self.assertEqual((i["cwd"], i["steps"], i["turns"], i["files_changed"], i["status"]), (cwd, steps, turns, files, status))

    def test_source_filter(self):
        for prov in al.PROVIDERS:
            with self.subTest(prov=prov):
                items = self.cli_json("list", "--source", prov)["sessions"]
                self.assertTrue(items)
                self.assertEqual({i["source"] for i in items}, {prov})
                self.assertEqual(self.cli_json("summary", "--source", prov)["source"], prov)
                self.assertTrue(self.cli("list", "--source", prov).splitlines()[1].split()[2] == prov)
        self.assertNotIn("codex", {i["source"] for i in self.cli_json("list", "--no-codex", "-n", "50")["sessions"]})
        r = subprocess.run([sys.executable, SCRIPT, "list", "--source", "nope"], capture_output=True, text=True, env=self.env)
        self.assertNotEqual(r.returncode, 0)

    # ---- summary ----
    def test_summary_text(self):
        out = self.cli("summary", "--source", "droid")
        self.assertIn("[droid] /fx/droid  11111111  idle", out)
        self.assertIn("TASK: Add a hello function to app.py", out)
        self.assertIn("FILES CHANGED (1): app.py", out)
        self.assertIn("ERRORS (1), last 3:", out)
        self.assertIn("LAST ACTIONS:", out)
        self.assertIn("FINAL (last assistant text): Added hello(); one test still fails.", out)
        out = self.cli("summary", "--source", "codex")
        self.assertIn("FINAL: Fixed the typo in README.md.", out)
        self.assertIn("context compacted 1x", out)
        out = self.cli("summary", "ses_db1")
        self.assertIn("STATUS: idle, ended without a final text", out)

    def test_summary_json(self):
        for prov, cwd, sid, *_ in EXPECT:
            with self.subTest(prov=prov, sid=sid):
                d = self.cli_json("summary", sid[:12])
                self.assertTrue(SUMMARY_KEYS <= set(d), SUMMARY_KEYS - set(d))
                self.assertEqual((d["source"], d["session_id"], d["cwd"]), (prov, sid, cwd))
                self.assertIsInstance(d["task"], str)
                for e in d["errors"]: self.assertEqual(set(e) >= {"ts", "text"}, True)
                for e in d["last_actions"]: self.assertIn(e["kind"], ("tool_call", "say")); self.assertRegex(e["ts"], ISO_RE)
        self.assertEqual(self.cli_json("summary", "22222222")["final_answer"], "Fixed the typo in README.md.")
        self.assertIsNone(self.cli_json("summary", "ses_db1")["final_answer"])

    # ---- show / tail ----
    def test_show_text(self):
        out = self.cli("show", "11111111")
        self.assertIn("USER  (turn 1): Add a hello function to app.py", out)
        self.assertIn("▶ Edit /fx/droid/app.py", out)
        self.assertIn("✗ ERROR Execute: 1 failed", out)
        self.assertIn("[context compacted] earlier work summarized", out)
        self.assertNotIn("↳ ok", out)
        self.assertIn("↳ ok", self.cli("show", "11111111", "--with-output"))
        self.assertNotIn("think", self.cli("show", "11111111", "--no-thinking"))
        tail = self.cli("tail", "11111111", "--last", "1")
        self.assertIn("SAY   Added hello()", tail); self.assertNotIn("▶ Edit", tail)

    def test_show_json(self):
        for prov, cwd, sid, *_ in EXPECT:
            with self.subTest(prov=prov, sid=sid):
                d = self.cli_json("show", sid[:12])
                self.assertEqual(d["schema_version"], 1)
                self.assertTrue(LIST_KEYS <= set(d["session"]))
                self.assertTrue(d["events"])
                for e in d["events"]:
                    self.assertTrue({"ts", "kind", "text"} <= set(e))
                    self.assertIn(e["kind"], al.KINDS)
                    if e["kind"] == "tool_call": self.assertIn("tool", e)
        d = self.cli_json("tail", "66666666", "--last", "1")
        self.assertEqual([e["kind"] for e in d["events"]], ["say"])

    def test_show_by_path(self):
        for prov, cwd, sid, *_ in EXPECT:
            with self.subTest(prov=prov):
                path = self.session(prov, sid).path
                self.assertIn(f"[{prov}] {cwd}", self.cli("show", path))
                self.assertIs(al.sniff(path), al.PROVIDERS[prov])

    # ---- running detection ----
    def test_running_detection(self):
        def fake(pid, argv, cwd):
            d = os.path.join(self.proc, str(pid)); os.makedirs(d)
            with open(os.path.join(d, "cmdline"), "wb") as f: f.write(b"\0".join(a.encode() for a in argv) + b"\0")
            os.symlink(cwd, os.path.join(d, "cwd"))
        pi_path = self.session("pi", EXPECT[6][2]).path
        st = os.stat(pi_path)
        try:
            fake(101, ["node", "/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js"], "/fx/claude")
            fake(102, ["codex", "resume", "22222222"], "/elsewhere")
            fake(103, ["/usr/local/bin/gemini"], "/not/a/project")
            os.utime(pi_path)
            # recently written but no pi process alive anywhere: a finished run, not a running one
            got = {i["source"] + ":" + i["session_id"][:8]: i for i in self.cli_json("list", "-n", "50")["sessions"]}
            self.assertEqual(got["pi:66666666"]["status"], "done")
            fake(104, ["node", "/usr/lib/node_modules/@mariozechner/pi-coding-agent/dist/cli.js"], "/some/other/dir")
            got = {i["source"] + ":" + i["session_id"][:8]: i for i in self.cli_json("list", "-n", "50")["sessions"]}
            self.assertEqual((got["claude:44444444"]["status"], got["claude:44444444"]["status_reason"]), ("running", "proc:cwd"))
            self.assertEqual((got["codex:22222222"]["status"], got["codex:22222222"]["status_reason"]), ("running", "proc:session-id"))
            self.assertEqual(got["pi:66666666"]["status_reason"], "mtime<120s")
            self.assertEqual(got["gemini:55555555"]["status"], "done")
            self.assertEqual(got["droid:11111111"]["status"], "done")
            out = self.cli("list", "--active")
            self.assertIn("RUN proc:cwd", out); self.assertNotIn("droid", out)
            self.assertIn("RUNNING (proc:cwd)", self.cli("summary", "44444444"))
        finally:
            shutil.rmtree(self.proc); os.makedirs(self.proc)
            os.utime(pi_path, (st.st_atime, st.st_mtime))

    # ---- real-session regressions ----
    def test_gemini_jsonl_upserts(self):
        ev = al.load(self.session("gemini", "d9840123-71e8-4ec2-bc0e-d95e08e96de0"))
        calls = [e for e in ev if e["k"] == "tool_call"]
        self.assertEqual(len(calls), 5)  # each record is written twice (before/after toolCalls); count once
        users = [e for e in ev if e["k"] == "user"]
        self.assertEqual(len(users), 1)  # <session_context> and functionResponse records are not prompts
        self.assertTrue(users[0]["text"].startswith("Read notes.txt"))
        err = [e for e in ev if e["k"] == "error"]
        self.assertEqual(err[0]["tool"], "run_shell_command")  # status "success" but "Exit Code: 1"
        self.assertNotIn("untrusted_context", err[0]["text"]); self.assertNotIn("PGID", err[0]["text"])

    def test_opencode_v1_db(self):
        s = self.session("opencode", "ses_eb24f4645ffe0DUv709DVDipk1")
        self.assertTrue(s.path.endswith("opencode-v1.db#ses_eb24f4645ffe0DUv709DVDipk1"))
        ev = al.load(s)
        ts = [e["t"] for e in ev if e["k"] in ("user", "say", "tool_call")]
        self.assertEqual(ts, sorted(ts))  # tool calls use their own start time, not the message's
        self.assertEqual([e["text"] for e in ev if e["k"] == "tool_call"][-1], "bash $ cat missing_file.txt")
        self.assertEqual(al.short_id(s.id), "ses_eb24f4645f")
        out = self.cli("summary", "ses_eb2512259f")
        self.assertIn("Factory: Forbidden", out)

    def test_failed_edit_not_counted(self):
        ev = al.load(self.session("claude", "7ede2b2f-bb02-44bf-b388-1f38d9a9f06b"))
        self.assertTrue(any(e["k"] == "tool_call" and e["tool"] == "Edit" for e in ev))
        self.assertEqual(al.files_changed(ev), [])
        item = self.cli_json("summary", "7ede2b2f")
        self.assertEqual((item["status"], item["status_reason"]), ("failed", "ended on agent/API error"))
        self.assertIsNone(item["final_answer"])

    def test_proc_kind(self):
        self.assertEqual(al.proc_kind(["droid", "exec"]), "droid")
        self.assertEqual(al.proc_kind(["node", "/x/bin/codex.js"]), "codex")
        self.assertEqual(al.proc_kind(["node", "/x/@google/gemini-cli/dist/index.js"]), "gemini")
        self.assertEqual(al.proc_kind(["bun", "/x/pi-coding-agent/dist/cli.js"]), "pi")
        self.assertIsNone(al.proc_kind(["python3", "agent_log.py"]))

    # ---- codex .zst archives ----
    def test_zst_skipped_without_decompressor(self):
        orig = al.zst_decompressor
        try:
            al.zst_decompressor = lambda: None
            paths = al.PROVIDERS["codex"].discover()
            self.assertFalse([p for p in paths if p.endswith(".zst")])
            self.assertEqual([s.id for s in al.PROVIDERS["codex"].sessions()], [EXPECT[1][2]])
        finally: al.zst_decompressor = orig

    def test_zst_unreadable_is_skipped(self):
        orig = al.zst_decompressor
        try:
            def broken(p): raise RuntimeError("bad frame")
            al.zst_decompressor = lambda: broken
            self.assertEqual([s.id for s in al.PROVIDERS["codex"].sessions()], [EXPECT[1][2]])
        finally: al.zst_decompressor = orig

    def test_zst_read_with_decompressor(self):
        orig = al.zst_decompressor
        try:
            al.zst_decompressor = lambda: (lambda p: CODEX_ZST_JSONL)
            s = next(s for s in al.PROVIDERS["codex"].sessions() if s.path.endswith(".zst"))
            self.assertEqual((s.id, s.cwd), ("33333333-3333-4333-8333-333333333333", "/fx/codex-zst"))
            self.assertEqual([e["text"] for e in al.load(s) if e["k"] == "user"], ["archived prompt"])
        finally: al.zst_decompressor = orig

    # ---- env overrides ----
    def test_env_override(self):
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty, exist_ok=True)
        env = dict(self.env, AGENT_LOG_HOME_PI=empty, AGENT_LOG_HOME_CLAUDE=empty)
        r = subprocess.run([sys.executable, SCRIPT, "list", "--json", "-n", "50"], capture_output=True, text=True, env=env)
        srcs = {i["source"] for i in json.loads(r.stdout)["sessions"]}
        self.assertNotIn("pi", srcs); self.assertNotIn("claude", srcs); self.assertIn("droid", srcs)

if __name__ == "__main__":
    unittest.main()
