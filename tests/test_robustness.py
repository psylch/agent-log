"""Edge cases, timezone handling, running-detection fallbacks, the list cache and CLI behaviour."""
import contextlib, io, json, os, shutil, stat, subprocess, sys, tempfile, time, unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "agent_log.py")
sys.path.insert(0, ROOT)
import agent_log as al  # noqa: E402

LIST_KEYS = {"schema_version", "source", "session_id", "path", "cwd", "status", "status_reason", "started", "updated",
             "steps", "turns", "task", "files_changed"}
SUMMARY_KEYS = LIST_KEYS | {"error_count", "errors", "last_actions", "final_answer"}
EVENT_KEYS = {"ts", "kind", "tool", "text", "paths", "step", "turn"}
CLEAR_ENV = ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "XDG_DATA_HOME", "XDG_CACHE_HOME", "PI_CODING_AGENT_DIR",
             "PI_CODING_AGENT_SESSION_DIR", "TZ", "NO_COLOR", "TERM")
OLD = time.time() - 3 * 86400

def jl(*recs):
    return "".join((r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)) + "\n" for r in recs)

def uid(n): return f"{n:08x}-0000-4000-8000-000000000000"

def msg(role, content, ts="2026-10-01T02:00:00Z", **kw):
    d = {"type": "message", "message": {"role": role, "content": content}}
    if ts: d["timestamp"] = ts
    d.update(kw)
    return d


class Env(unittest.TestCase):
    """A fresh fake $HOME per test, with the environment isolated and the display timezone pinned."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agent-log-t-")
        self.home = os.path.join(self.tmp, "home"); os.makedirs(self.home)
        self.proc = os.path.join(self.tmp, "proc"); os.makedirs(self.proc)
        self.cache = os.path.join(self.tmp, "cache")
        self.saved_env = dict(os.environ)
        for k in list(os.environ):
            if k in CLEAR_ENV or k.startswith("AGENT_LOG_"): del os.environ[k]
        os.environ.update(HOME=self.home, AGENT_LOG_PROC=self.proc, NO_COLOR="1", AGENT_LOG_TZ="+08:00", XDG_CACHE_HOME=self.cache)
        al.set_tz("+08:00")
        self.saved_cache, al.CACHE = al.CACHE, al.Cache(None)

    def tearDown(self):
        al.CACHE = self.saved_cache
        os.environ.clear(); os.environ.update(self.saved_env)
        al.set_tz("+08:00")
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel, text, mtime=OLD):
        p = os.path.join(self.home, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f: f.write(text)
        if mtime is not None: os.utime(p, (mtime, mtime))
        return p

    def droid(self, n, recs, cwd="/fx/d", mtime=OLD):
        head = {"type": "session_start", "id": uid(n), "cwd": cwd}
        return self.write(f".factory/sessions/{cwd.replace('/', '-')}/{uid(n)}.jsonl", jl(head, *recs), mtime)

    def cli(self, *args, code=0, env=None, stdin=None):
        e = dict(os.environ); e.update(env or {})
        r = subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, env=e, timeout=60, encoding="utf-8")
        self.assertEqual(r.returncode, code, f"{args}: stdout={r.stdout[-500:]} stderr={r.stderr[-1500:]}")
        self.assertNotIn("Traceback", r.stderr)
        return r.stdout, r.stderr

    def main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = al.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def sess(self, prov):
        ss = al.PROVIDERS[prov].sessions()
        self.assertEqual(len(ss), 1, ss)
        return ss[0]


class ParserEdgeCases(Env):
    TRUNC = '{"type": "message", "timestamp": "2026-10-01T02:00:09Z", "message": {"role": "assi'

    def build_all(self, extra_line=None, empty=False):
        """One small session per file-based provider; extra_line is appended without a trailing newline."""
        tail = extra_line or ""
        files = {
            "droid": (f".factory/sessions/-fx-d/{uid(1)}.jsonl",
                      jl({"type": "session_start", "id": uid(1), "cwd": "/fx/d"},
                         msg("user", [{"type": "text", "text": "droid prompt"}]),
                         msg("assistant", [{"type": "text", "text": "droid answer"}], ts="2026-10-01T02:00:01Z"))),
            "claude": (f".claude/projects/-fx-c/{uid(2)}.jsonl",
                       jl({"type": "user", "cwd": "/fx/c", "timestamp": "2026-10-01T02:00:00Z", "message": {"role": "user", "content": "claude prompt"}},
                          {"type": "assistant", "timestamp": "2026-10-01T02:00:01Z", "message": {"id": "m1", "role": "assistant", "content": [{"type": "text", "text": "claude answer"}]}})),
            "codex": (f".codex/sessions/2026/10/01/rollout-2026-10-01T00-00-00-{uid(3)}.jsonl",
                      jl({"timestamp": "2026-10-01T02:00:00Z", "type": "session_meta", "payload": {"cwd": "/fx/x"}},
                         {"timestamp": "2026-10-01T02:00:00Z", "type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "codex prompt"}]}},
                         {"timestamp": "2026-10-01T02:00:01Z", "type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "codex answer"}]}})),
            "pi": (f".pi/agent/sessions/--fx-p--/2026-10-01T00-00-00-000Z_{uid(4)}.jsonl",
                   jl({"type": "session", "version": 3, "id": uid(4), "cwd": "/fx/p"},
                      {"type": "message", "id": "u1", "timestamp": "2026-10-01T02:00:00Z", "message": {"role": "user", "content": "pi prompt"}},
                      {"type": "message", "id": "a1", "parentId": "u1", "timestamp": "2026-10-01T02:00:01Z", "message": {"role": "assistant", "content": [{"type": "text", "text": "pi answer"}]}})),
            "gemini": (".gemini/tmp/h/chats/session-2026-10-01T02-00-55555555.jsonl",
                       jl({"sessionId": uid(5), "projectHash": "h"},
                          {"id": "u1", "type": "user", "timestamp": "2026-10-01T02:00:00Z", "content": [{"text": "gemini prompt"}]},
                          {"id": "g1", "type": "gemini", "timestamp": "2026-10-01T02:00:01Z", "content": "gemini answer"})),
        }
        for prov, (rel, text) in files.items():
            self.write(rel, "" if empty else text + tail)
        return files

    def test_truncated_last_line_is_ignored(self):
        self.build_all(extra_line=self.TRUNC)
        for prov in ("droid", "claude", "codex", "pi", "gemini"):
            with self.subTest(prov=prov):
                ev = al.load(self.sess(prov))
                self.assertEqual([e["k"] for e in ev], ["user", "say"])
                self.assertEqual(ev[0]["text"], f"{prov} prompt")
                self.assertEqual(al.idle_status(ev), ("done", "ended on assistant text"))
        d = json.loads(self.cli("list", "--json", "-n", "0")[0])
        self.assertEqual(len(d["sessions"]), 5)

    def test_empty_files(self):
        self.build_all(empty=True)
        self.write(".gemini/tmp/h/chats/session-2026-10-01T03-00-66666666.json", "")
        d = json.loads(self.cli("list", "--json", "-n", "0")[0])
        got = {i["source"]: i for i in d["sessions"]}
        # an empty .jsonl has no header, so it is not a gemini chat; an empty .json is a rewrite in progress
        self.assertEqual(set(got), {"droid", "claude", "codex", "pi", "gemini"})
        for src, i in got.items():
            with self.subTest(src=src):
                self.assertEqual(set(i), LIST_KEYS)
                self.assertEqual(i["status"], "unknown")
                self.assertEqual((i["steps"], i["task"], i["started"], i["files_changed"]), (0, None, None, []))
        self.assertEqual(got["gemini"]["session_id"], "66666666")
        self.assertEqual(got["droid"]["cwd"], "/fx/d")  # recovered from the directory name
        for src in ("droid", "gemini"):
            s = json.loads(self.cli("summary", "--source", src, "--json")[0])
            self.assertEqual(set(s), SUMMARY_KEYS)
            self.assertIsNone(s["final_answer"])

    def test_half_written_gemini_json_stays_listed(self):
        p = self.write(".gemini/tmp/h/chats/session-2026-10-01T03-00-77777777.json", '{"sessionId": "77777777-aaaa", "messages": [{"id": "u')
        s = self.sess("gemini")
        self.assertEqual(s.id, "77777777")
        ev = al.load(s)
        self.assertEqual(ev[0]["tool"], "agent-log")
        self.assertEqual(al.idle_status(ev)[0], "unknown")  # not "failed": this is not an agent error
        with open(p, "w") as f: json.dump({"sessionId": "x"}, f)  # parses, but is not a chat
        self.assertEqual(al.PROVIDERS["gemini"].sessions(), [])

    def test_malformed_records_are_skipped(self):
        self.droid(1, [
            "[1, 2, 3]", '"just a string"', "{not json",
            {"type": "message", "message": "oops"},
            {"type": "message", "message": {"role": "user", "content": 5}},
            {"type": "message", "timestamp": {"x": 1}, "message": {"role": "user", "content": [{"type": "text", "text": ["a", "list"]}]}},
            msg("assistant", [{"type": "text", "text": None}, {"type": "tool_use", "name": None, "input": "not json"}, "bare string"]),
            msg("user", [{"type": "text", "text": "real prompt"}], ts="2026-10-01T02:00:05Z"),
            msg("assistant", [{"type": "text", "text": "real answer"}], ts="2026-10-01T02:00:06Z"),
        ])
        self.write(f".claude/projects/-fx-c/{uid(2)}.jsonl", jl(
            {"type": "user", "message": "str"}, {"type": "assistant", "message": {"content": 5}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": 7}, {"type": "thinking", "thinking": None}]}},
            {"type": "user", "cwd": "/fx/c", "timestamp": "2026-10-01T02:00:00Z", "message": {"role": "user", "content": [{"type": "text", "text": None}, {"type": "text", "text": "ok"}]}}))
        self.write(f".codex/sessions/r-{uid(3)}.jsonl", jl(
            {"type": "session_meta", "payload": ["x"]}, {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "nope"}},
            {"type": "response_item", "payload": {"type": "function_call", "name": "shell", "arguments": 'cmd: "bad \\q escape"'}},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "fine"}]}}))
        self.write(f".pi/agent/sessions/--fx-p--/x_{uid(4)}.jsonl", jl(
            {"type": "session", "version": 3, "id": uid(4), "cwd": ["bad"]},
            {"type": "message", "id": ["x"], "parentId": None, "message": {"role": "assistant", "content": "str"}},
            {"type": "message", "id": "u1", "message": {"role": "user", "content": "pi ok"}}))
        droid = al.load(self.sess("droid"))
        self.assertEqual([e["text"] for e in droid if e["k"] == "user"][-1], "real prompt")
        self.assertEqual(droid[-1]["text"], "real answer")
        self.assertIn("? not json", [e["text"] for e in droid if e["k"] == "tool_call"])
        self.assertEqual([e["text"] for e in al.load(self.sess("claude")) if e["k"] == "user"], ["ok"])
        self.assertEqual([e["text"] for e in al.load(self.sess("codex")) if e["k"] == "user"], ["fine"])
        pi = self.sess("pi")
        self.assertEqual(pi.cwd, "/fx/p")
        self.assertEqual([e["text"] for e in al.load(pi) if e["k"] == "user"], ["pi ok"])
        d = json.loads(self.cli("list", "--json", "-n", "0")[0])
        self.assertEqual(len(d["sessions"]), 4)

    def test_unicode_line_separators_and_non_utf8_locale(self):
        text = "line\u2028sep\u2029para\x1cfs \u65e5\u672c\u8a9e \xfc"
        self.droid(1, [msg("user", [{"type": "text", "text": text}]),
                       msg("assistant", [{"type": "text", "text": "done \u2713"}], ts="2026-10-01T02:00:01Z")])
        self.write(f".claude/projects/-fx-c/{uid(2)}.jsonl",
                   jl({"type": "user", "cwd": "/fx/c", "timestamp": "2026-10-01T02:00:00Z", "message": {"role": "user", "content": text}}))
        with open(self.sess("droid").path, encoding="utf-8") as f: self.assertIn("\u2028", f.read())  # really raw
        for prov in ("droid", "claude"):
            self.assertEqual([e["text"] for e in al.load(self.sess(prov)) if e["k"] == "user"], [text])
        # ASCII locale for files, UTF-8 for stdout: file reading must not depend on the locale
        env = {"LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0", "PYTHONIOENCODING": "utf-8"}
        out, _ = self.cli("summary", "--source", "droid", "--json", env=env)
        self.assertEqual(json.loads(out)["task"], al.short(text, 600))
        self.assertIn("\u65e5\u672c\u8a9e", json.loads(out)["task"])

    def test_huge_tool_output(self):
        big = "x" * 3_000_000 + "\nEND"
        self.droid(1, [msg("user", [{"type": "text", "text": "go"}]),
                       msg("assistant", [{"type": "tool_use", "id": "t1", "name": "Execute", "input": {"command": "cat big"}}], ts="2026-10-01T02:00:01Z"),
                       msg("user", [{"type": "tool_result", "tool_use_id": "t1", "content": big}], ts="2026-10-01T02:00:02Z"),
                       msg("assistant", [{"type": "text", "text": "ok"}], ts="2026-10-01T02:00:03Z")])
        d = json.loads(self.cli("show", "--json", "--max-chars", "100")[0])
        res = next(e for e in d["events"] if e["kind"] == "tool_result")
        self.assertEqual(len(res["text"]), 400)
        out, _ = self.cli("show", "--with-output", "--max-chars", "100")
        self.assertLess(len(out), 5000)
        full = json.loads(self.cli("show", "--json", "--full")[0])
        self.assertEqual(next(e for e in full["events"] if e["kind"] == "tool_result")["text"], big)

    def test_short(self):
        t = time.perf_counter()
        self.assertEqual(al.short("a  b\n\nc" * 1_000_000, 10), "a b ca b …")
        self.assertLess(time.perf_counter() - t, 0.5)
        self.assertEqual(al.short(" " * 100_000 + "abc", 10), "abc")  # leading whitespace beyond the cap
        self.assertEqual(al.short("abc", 10), "abc")
        self.assertEqual(al.short("abcdefghijk", 10), "abcdefghi…")
        self.assertEqual(al.short("x" * 5000 + " " * 10, 4096 + 5000), "x" * 5000)
        self.assertEqual(al.short(None, 5), "")

    def test_missing_timestamps(self):
        self.droid(1, [msg("user", [{"type": "text", "text": "go"}], ts=None),
                       msg("assistant", [{"type": "text", "text": "one"}], ts=None),
                       msg("assistant", [{"type": "text", "text": "two"}], ts=None),
                       msg("assistant", [{"type": "text", "text": "three"}], ts="2026-10-01T02:00:00Z"),
                       msg("assistant", [{"type": "text", "text": "four"}], ts=None)])
        ev = al.load(self.sess("droid"))
        self.assertEqual([e["n"] for e in ev if e["k"] == "say"], [1, 2, 3, 4])  # untimed blocks are not merged
        d = json.loads(self.cli("summary", "--json")[0])
        self.assertEqual(d["steps"], 4)
        self.assertRegex(d["started"], r"\+08:00$")
        out, _ = self.cli("show")
        self.assertIn("--:--:-- #1   SAY   one", out)

    def test_no_timestamps_at_all(self):
        self.droid(1, [msg("assistant", [{"type": "text", "text": "a"}], ts=None)])
        d = json.loads(self.cli("summary", "--json")[0])
        self.assertIsNone(d["started"])
        self.assertIsNone(d["task"])
        self.assertIsNone(json.loads(self.cli("show", "--json")[0])["events"][0]["ts"])
        out, _ = self.cli("show")
        self.assertIn("-- → --", out)

    def test_no_user_prompt(self):
        self.write(f".codex/sessions/r-{uid(3)}.jsonl", jl(
            {"timestamp": "2026-10-01T02:00:00Z", "type": "session_meta", "payload": {"cwd": "/fx/x"}},
            {"timestamp": "2026-10-01T02:00:01Z", "type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hello"}]}}))
        d = json.loads(self.cli("summary", "--json")[0])
        self.assertEqual((d["task"], d["status"], d["final_answer"]), (None, "done", "hello"))
        out, _ = self.cli("summary")
        self.assertNotIn("TASK:", out)

    def test_codex_and_pi_failed_edits_not_counted(self):
        patch = "*** Begin Patch\n*** Update File: {}\n@@\n-a\n+b\n*** End Patch"
        self.write(f".codex/sessions/r-{uid(3)}.jsonl", jl(
            {"timestamp": "2026-10-01T02:00:00Z", "type": "session_meta", "payload": {"cwd": "/fx/x"}},
            {"timestamp": "2026-10-01T02:00:01Z", "type": "response_item", "payload": {"type": "custom_tool_call", "name": "apply_patch", "call_id": "c1", "input": patch.format("bad.py")}},
            {"timestamp": "2026-10-01T02:00:02Z", "type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "c1", "output": "Process exited with code 1"}},
            {"timestamp": "2026-10-01T02:00:03Z", "type": "response_item", "payload": {"type": "custom_tool_call", "name": "apply_patch", "call_id": "c2", "input": patch.format("good.py")}},
            {"timestamp": "2026-10-01T02:00:04Z", "type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "c2", "output": "Success"}}))
        self.write(f".pi/agent/sessions/--fx-p--/x_{uid(4)}.jsonl", jl(
            {"type": "session", "version": 3, "id": uid(4), "cwd": "/fx/p"},
            {"type": "message", "id": "a1", "timestamp": "2026-10-01T02:00:01Z", "message": {"role": "assistant", "content": [
                {"type": "toolCall", "id": "tc1", "name": "edit", "arguments": {"path": "/fx/p/x.py"}}]}},
            {"type": "message", "id": "r1", "timestamp": "2026-10-01T02:00:02Z", "message": {"role": "toolResult", "toolCallId": "tc1", "toolName": "edit", "isError": True, "content": "no match"}}))
        self.assertEqual(al.files_changed(al.load(self.sess("codex"))), ["good.py"])
        self.assertEqual(al.files_changed(al.load(self.sess("pi"))), [])

    def test_parse_ts(self):
        utc = lambda *a: datetime(*a, tzinfo=timezone.utc).timestamp()
        cases = {
            "2026-10-01T02:00:00Z": utc(2026, 10, 1, 2), "2026-10-01T02:00:00.5Z": utc(2026, 10, 1, 2) + 0.5,
            "2026-10-01T02:00:00.123456789Z": utc(2026, 10, 1, 2) + 0.123456, "2026-10-01T10:00:00+0800": utc(2026, 10, 1, 2),
            "2026-10-01T10:00:00+08:00": utc(2026, 10, 1, 2), "2026-10-01 02:00:00": utc(2026, 10, 1, 2),
            1790820000: 1790820000.0, 1790820000000: 1790820000.0, 1790820000000000: 1790820000.0,
            1790820000000000000: 1790820000.0, "1790820000000": 1790820000.0,
        }
        for v, want in cases.items():
            with self.subTest(v=v): self.assertAlmostEqual(al.parse_ts(v), want, places=3)
        for v in (None, "", "garbage", "2026-13-45T00:00:00Z", True, float("nan"), float("inf"), -5, 1e30, {"x": 1}):
            with self.subTest(v=v): self.assertIsNone(al.parse_ts(v))
        self.assertIsNone(al.iso(1e15))  # out of datetime range: null, not a crash
        self.assertEqual(al.fmt_t(1e15), "--:--:--")


class TimezoneTests(Env):
    def setUp(self):
        super().setUp()
        self.droid(1, [msg("user", [{"type": "text", "text": "go"}], ts="2026-10-01T02:00:00Z")])

    def test_parse_tz(self):
        self.assertIsNone(al.parse_tz("local")); self.assertIsNone(al.parse_tz(""))
        self.assertIs(al.parse_tz("UTC"), timezone.utc); self.assertIs(al.parse_tz("z"), timezone.utc)
        for spec, mins in (("+05:30", 330), ("-0800", -480), ("+8", 480), ("UTC+02:00", 120), ("-00:30", -30)):
            with self.subTest(spec=spec): self.assertEqual(al.parse_tz(spec).utcoffset(None), timedelta(minutes=mins))
        for bad in ("bogus/zone", "+25:00", "+5:3", "12"):
            with self.subTest(bad=bad), self.assertRaises(ValueError): al.parse_tz(bad)
        try: tz = al.parse_tz("Asia/Taipei")
        except ValueError: self.skipTest("no tz database")
        self.assertEqual(datetime(2026, 1, 1, tzinfo=tz).utcoffset(), timedelta(hours=8))

    def test_override_and_default(self):
        started = lambda *a, **kw: json.loads(self.cli("summary", "--json", *a, **kw)[0])["started"]
        self.assertEqual(started(), "2026-10-01T10:00:00+08:00")
        self.assertEqual(started("--tz", "UTC"), "2026-10-01T02:00:00+00:00")
        self.assertEqual(started(env={"AGENT_LOG_TZ": "-05:30"}), "2026-09-30T20:30:00-05:30")
        self.assertEqual(started("--tz", "+01:00", env={"AGENT_LOG_TZ": "-05:30"}), "2026-10-01T03:00:00+01:00")
        # no override: the local system timezone (POSIX TZ string "XYZ-05:30" = UTC+05:30)
        env = {"AGENT_LOG_TZ": "", "TZ": "XYZ-05:30"}
        self.assertEqual(started(env=env), "2026-10-01T07:30:00+05:30")
        self.assertTrue(self.cli("list", env=env)[0].startswith("last(+05:30)"))
        self.assertIn("(UTC)", self.cli("summary", "--tz", "UTC")[0])

    def test_bad_timezone_is_a_usage_error(self):
        _, err = self.cli("list", "--tz", "Mars/Olympus", code=2)
        self.assertIn("bad timezone --tz='Mars/Olympus'", err)
        _, err = self.cli("list", code=2, env={"AGENT_LOG_TZ": "+99"})
        self.assertIn("AGENT_LOG_TZ", err)

    def test_parse_when(self):
        utc = lambda *a: datetime(*a, tzinfo=timezone.utc).timestamp()
        self.assertEqual(al.parse_when("2026-10-01T10:00"), utc(2026, 10, 1, 2))  # naive = display tz (+08)
        self.assertEqual(al.parse_when("2026-10-01"), utc(2026, 9, 30, 16))
        self.assertEqual(al.parse_when("2026-10-01T02:00:00Z"), utc(2026, 10, 1, 2))
        self.assertAlmostEqual(al.parse_when("90m"), time.time() - 5400, delta=5)
        hm = datetime.fromtimestamp(al.parse_when("07:05"), al.TZ)
        self.assertEqual((hm.hour, hm.minute, hm.date()), (7, 5, datetime.now(al.TZ).date()))
        for bad in ("yesterday", "25:00", "2h30m", "2026-10-01T25:00"):
            with self.subTest(bad=bad), self.assertRaises(al.CliError) as cm: al.parse_when(bad)
            self.assertEqual(cm.exception.code, 2)


class RunningDetection(Env):
    def setUp(self):
        super().setUp()
        now = time.time()
        self.c1 = self.write(f".claude/projects/-fx-a/{uid(1)}.jsonl", jl({"type": "user", "cwd": "/fx/a", "message": {"role": "user", "content": "one"}}), OLD)
        self.c2 = self.write(f".claude/projects/-fx-a/{uid(2)}.jsonl", jl({"type": "user", "cwd": "/fx/a", "message": {"role": "user", "content": "two"}}), OLD + 10)
        self.d = self.droid(3, [msg("user", [{"type": "text", "text": "go"}])], mtime=now - 5)
        del os.environ["AGENT_LOG_PROC"]
        self.patch_root = mock.patch.object(al, "PROC_ROOT", os.path.join(self.tmp, "no-proc-here"))
        self.patch_root.start()

    def tearDown(self):
        self.patch_root.stop()
        super().tearDown()

    def status(self):
        ss = al.discover(); al.mark_running(ss)
        return {s.path: (s.running, s.why) for s in ss}

    def test_ps_fallback(self):
        rows = [("101", ["node", "/x/@anthropic-ai/claude-code/cli.js"]), ("102", ["claude", "--cwd", "/fx/a"]), ("103", ["vim"])]
        with mock.patch.object(al, "ps_rows", return_value=rows), mock.patch.object(al, "lsof_cwds", return_value={"101": "/fx/a"}) as lsof:
            st = self.status()
        lsof.assert_called_once_with(["101"])  # only processes without --cwd need lsof
        # two processes in one cwd claim the two newest sessions there, not the same one twice
        self.assertEqual(st[self.c2], (True, "proc:cwd")); self.assertEqual(st[self.c1], (True, "proc:cwd"))
        self.assertEqual(st[self.d], (False, ""))  # written just now, but no droid process is alive

    def test_no_process_table_uses_mtime_only(self):
        with mock.patch.object(al, "ps_rows", return_value=None):
            st = self.status()
        self.assertEqual(st[self.d], (True, "mtime<120s"))
        self.assertEqual(st[self.c2], (False, ""))
        with mock.patch.object(al, "ps_rows", return_value=None):
            d = json.loads(self.main("list", "--json")[1])
        self.assertEqual({i["session_id"]: i["status"] for i in d["sessions"]}[uid(3)], "running")

    def test_ps_and_lsof_failures_never_crash(self):
        with mock.patch.object(al.subprocess, "run", side_effect=OSError("no ps")):
            self.assertIsNone(al.ps_rows())
            self.assertEqual(al.lsof_cwds(["1"]), {})
            self.assertEqual(self.status()[self.d], (True, "mtime<120s"))
        failed = subprocess.CompletedProcess([], 1, "", "ps: illegal option")
        with mock.patch.object(al.subprocess, "run", return_value=failed): self.assertIsNone(al.ps_rows())

    def test_ps_and_lsof_parsing(self):
        ps = subprocess.CompletedProcess([], 0, "  1 /sbin/init\n 42 node /usr/lib/node_modules/@google/gemini-cli/dist/index.js -r abc\n\n", "")
        with mock.patch.object(al.subprocess, "run", return_value=ps):
            self.assertEqual(al.ps_rows()[1], ("42", ["node", "/usr/lib/node_modules/@google/gemini-cli/dist/index.js", "-r", "abc"]))
        lsof = subprocess.CompletedProcess([], 0, "p42\nfcwd\nn/work/x\np43\nfcwd\nn/work/y z\n", "")
        with mock.patch.object(al.subprocess, "run", return_value=lsof), mock.patch.object(al.shutil, "which", return_value="/usr/sbin/lsof"):
            self.assertEqual(al.lsof_cwds(["42", "43"]), {"42": "/work/x", "43": "/work/y z"})

    def test_unreadable_proc_override(self):
        os.environ["AGENT_LOG_PROC"] = os.path.join(self.tmp, "missing")
        self.assertEqual(al.live_procs(), ([], False))


class CacheTests(Env):
    def setUp(self):
        super().setUp()
        self.p = self.droid(1, [msg("user", [{"type": "text", "text": "go"}]), msg("assistant", [{"type": "text", "text": "a"}], ts="2026-10-01T02:00:01Z")])
        self.cfile = os.path.join(self.cache, "agent-log", "sessions.json")

    def cached(self):
        with open(self.cfile, encoding="utf-8") as f: return json.load(f)

    def list_json(self, *a, **kw): return json.loads(self.cli("list", "--json", "-n", "0", *a, **kw)[0])["sessions"]

    def test_hit_avoids_reading_the_file(self):
        al.CACHE = al.Cache(os.path.join(self.cache, "agent-log"))
        s = al.discover()[0]; b = s.brief(); al.CACHE.save()
        self.assertTrue(os.path.isfile(self.cfile))
        self.assertEqual(stat.S_IMODE(os.stat(self.cfile).st_mode), 0o600)  # it holds prompts
        al.CACHE = al.Cache(os.path.join(self.cache, "agent-log"))
        boom = mock.Mock(side_effect=AssertionError("file was parsed"))
        with mock.patch.object(al.Droid, "events", boom), mock.patch.object(al.Droid, "meta", boom):
            s2 = al.discover()[0]
            self.assertEqual((s2.id, s2.cwd, s2.brief()), (uid(1), "/fx/d", b))

    def test_never_stale(self):
        self.assertEqual(self.list_json()[0]["steps"], 1)
        with open(self.p, "a") as f: f.write(jl(msg("assistant", [{"type": "text", "text": "b"}], ts="2026-10-01T02:00:09Z")))
        os.utime(self.p, (OLD + 1, OLD + 1))
        self.assertEqual(self.list_json()[0]["steps"], 2)
        # same size, new mtime: still re-read
        with open(self.p, encoding="utf-8") as f: body = f.read()
        with open(self.p, "w", encoding="utf-8") as f: f.write(body.replace('"text": "go"', '"text": "GO"'))
        os.utime(self.p, (OLD + 2, OLD + 2))
        self.assertEqual(self.cached()["entries"][self.p]["brief"]["task"], "go")
        self.assertEqual(self.list_json()[0]["task"], "GO")

    def test_recently_written_files_bypass_the_cache(self):
        os.utime(self.p)
        s = al.discover()[0]
        al.CACHE = al.Cache(os.path.join(self.cache, "agent-log"))
        self.assertFalse(al.CACHE.usable(s))

    def test_unwritable_or_corrupt_cache_is_ignored(self):
        want = self.list_json(env={"AGENT_LOG_NO_CACHE": "1"})
        blocker = os.path.join(self.tmp, "a-file")
        open(blocker, "w").close()
        self.assertEqual(self.list_json(env={"AGENT_LOG_CACHE_DIR": os.path.join(blocker, "sub")}), want)
        os.makedirs(os.path.dirname(self.cfile))
        with open(self.cfile, "w") as f: f.write("{corrupt")
        self.assertEqual(self.list_json(), want)
        self.assertEqual(self.cached()["entries"][self.p]["brief"]["steps"], 1)  # rewritten

    def test_disable(self):
        self.list_json("--no-cache"); self.list_json(env={"AGENT_LOG_NO_CACHE": "1"})
        self.assertFalse(os.path.exists(self.cfile))
        self.list_json(env={"AGENT_LOG_NO_CACHE": "0"})
        self.assertTrue(os.path.exists(self.cfile))

    def test_deleted_sessions_are_pruned(self):
        q = self.droid(2, [msg("user", [{"type": "text", "text": "x"}])])
        self.list_json()
        self.assertIn(q, self.cached()["entries"])
        os.unlink(q)
        os.utime(self.p, (OLD + 5, OLD + 5))  # force a write
        self.list_json()
        self.assertNotIn(q, self.cached()["entries"])

    def test_list_only_parses_what_it_shows(self):
        for i in range(2, 30): self.droid(i, [msg("user", [{"type": "text", "text": f"p{i}"}])], mtime=OLD + i)
        calls = []
        orig = al.Droid.events
        with mock.patch.object(al.Droid, "events", lambda self_, path: calls.append(path) or orig(self_, path)):
            code, out, _ = self.main("list", "-n", "3", "--no-cache")
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(out.splitlines()), 5)  # header, 3 rows, legend


class CliTests(Env):
    def setUp(self):
        super().setUp()
        self.droid(0xaaaa1111, [msg("user", [{"type": "text", "text": "first"}]), msg("assistant", [{"type": "text", "text": "a"}], ts="2026-10-01T02:00:01Z")], cwd="/fx/alpha")
        self.droid(0xaaaa2222, [msg("user", [{"type": "text", "text": "second"}])], cwd="/fx/beta", mtime=OLD + 60)

    def test_version_and_main(self):
        self.assertEqual(al.__version__, "0.1.0")
        out, _ = self.cli("--version")
        self.assertEqual(out.strip(), "agent-log 0.1.0")
        self.assertEqual(self.main("--version")[0], 0)
        self.assertEqual(self.main("list")[0], 0)
        self.assertEqual(self.main()[0], 2)
        self.assertEqual(self.main("--help")[0], 0)
        self.assertEqual(self.main("bogus")[0], 2)

    def test_exit_codes_and_messages(self):
        _, err = self.cli("summary", "deadbeef", code=1)
        self.assertIn("agent-log: error: no session matches 'deadbeef'", err)
        _, err = self.cli("show", "--since", "yesterday", code=2)
        self.assertIn("bad time 'yesterday'", err)
        _, err = self.cli("list", "--since", "nope", code=2)
        self.cli("list", "-n", "-1", code=2)
        self.cli("tail", "--last", "0", code=2)
        self.cli("list", "--source", "nope", code=2)
        _, err = self.cli("show", "-f", "--json", code=2)
        self.assertIn("--follow is not supported with --json", err)
        _, err = self.cli("show", "/no/such/file.jsonl", code=1)

    def test_missing_source_dir(self):
        env = {"AGENT_LOG_HOME_GEMINI": os.path.join(self.tmp, "nope")}
        _, err = self.cli("summary", "--source", "gemini", code=1, env=env)
        self.assertIn("no sessions found (gemini: " + os.path.join(self.tmp, "nope", "tmp") + " (missing))", err)
        out, err = self.cli("list", "--source", "gemini", env=env)
        self.assertEqual(out.strip(), "(no sessions)")
        self.assertIn("note: not found: gemini:", err)
        self.assertEqual(json.loads(self.cli("list", "--source", "gemini", "--json", env=env)[0])["sessions"], [])

    def test_ambiguous_prefix_warns(self):
        out, err = self.cli("summary", "aaaa", "--json")
        self.assertEqual(json.loads(out)["session_id"], uid(0xaaaa2222))  # newest
        self.assertIn("'aaaa' matches 2 sessions", err)
        _, err = self.cli("summary", "aaaa1", "--json")
        self.assertEqual(err, "")

    def test_project_flag(self):
        self.assertEqual(json.loads(self.cli("summary", "--project", "alpha", "--json")[0])["task"], "first")
        self.assertEqual(json.loads(self.cli("show", "--project", "alpha", "--json")[0])["session"]["cwd"], "/fx/alpha")
        _, err = self.cli("summary", "--project", "gamma", code=1)
        self.assertIn("in projects matching 'gamma'", err)
        self.assertEqual(len(json.loads(self.cli("list", "--project", "BETA", "--json")[0])["sessions"]), 1)

    def test_limit_and_since(self):
        for i in range(3, 25): self.droid(i, [msg("user", [{"type": "text", "text": "x"}])])
        n = lambda *a: len(json.loads(self.cli("list", "--json", *a)[0])["sessions"])
        self.assertEqual(n(), 15)
        self.assertEqual(n("-n", "0"), 24)
        self.assertEqual(n("--limit", "2"), 2)
        self.assertEqual(n("-n", "0", "--since", "2000-01-01"), 24)
        self.assertEqual(n("--since", "1h"), 0)

    def test_color(self):
        class Tty(io.StringIO):
            def isatty(self): return True
        self.assertFalse(al.want_color(Tty()))  # NO_COLOR=1 from setUp
        del os.environ["NO_COLOR"]
        self.assertTrue(al.want_color(Tty()))
        self.assertFalse(al.want_color(io.StringIO()))
        os.environ["TERM"] = "dumb"
        self.assertFalse(al.want_color(Tty()))
        env = dict(os.environ); env.pop("NO_COLOR", None); env.pop("TERM", None)
        r = subprocess.run([sys.executable, SCRIPT, "show", "--all"], capture_output=True, text=True, env=env)
        self.assertNotIn("\033[", r.stdout)  # a pipe is not a tty

    def test_non_utf8_stdout(self):
        out, _ = self.cli("summary", env={"PYTHONIOENCODING": "ascii"})
        self.assertIn("? ", out)  # ▶ / → replaced instead of a UnicodeEncodeError

    def test_broken_pipe_is_quiet(self):
        self.droid(99, [msg("assistant", [{"type": "text", "text": f"line {i} " + "y" * 200}], ts=f"2026-10-01T02:{i // 60 % 60:02d}:{i % 60:02d}Z")
                        for i in range(3000)], mtime=time.time() - 3)
        for args in (["show", "--full", "--all"], ["show", "--json", "--all"]):
            with self.subTest(args=args):
                p = subprocess.Popen([sys.executable, SCRIPT, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=os.environ)
                p.stdout.readline(); p.stdout.close()
                err = p.stderr.read(); p.stderr.close()
                self.assertEqual(p.wait(timeout=60), 0)
                self.assertEqual(err, b"")

    def test_rel(self):
        self.assertEqual(al.rel("/a/b/c.py", "/a/b"), "c.py")
        self.assertEqual(al.rel("/a/b/c.py", "/a/b/"), "c.py")
        self.assertEqual(al.rel("/x/y.py", None), "/x/y.py")  # used to strip every "/"
        self.assertEqual(al.rel("/a/bc/d.py", "/a/b"), "/a/bc/d.py")


if __name__ == "__main__":
    unittest.main()
