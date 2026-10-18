"""The README's sample output is real: rebuild the demo home and compare. Skipped where examples/ is not shipped (sdist)."""
import os, re, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.path.join(ROOT, "examples", "demo_home.py")
README = os.path.join(ROOT, "README.md")


@unittest.skipUnless(os.path.isfile(DEMO) and os.path.isfile(README), "examples/ or README.md not present")
class ReadmeDemoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="agent-log-readme-")
        cls.home = os.path.join(cls.tmp.name, "agent-log-demo")
        subprocess.run([sys.executable, DEMO, cls.home], check=True, capture_output=True)
        with open(README, encoding="utf-8") as f: cls.blocks = re.findall(r"```text\n(.*?)```", f.read(), re.S)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_cli(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_LOG_")}
        env.update(HOME=self.home, AGENT_LOG_PROC=os.path.join(self.home, ".proc"), AGENT_LOG_TZ="UTC",
                   AGENT_LOG_NO_CACHE="1", NO_COLOR="1")
        for k in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "XDG_DATA_HOME", "PI_CODING_AGENT_DIR", "PI_CODING_AGENT_SESSION_DIR"): env.pop(k, None)
        r = subprocess.run([sys.executable, os.path.join(ROOT, "agent_log.py"), *args], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_samples_match(self):
        for args in (["list"], ["summary", "134298f6"], ["tail", "134298f6", "--last", "3", "--with-output"]):
            with self.subTest(args=args):
                out = self.run_cli(*args)
                self.assertIn(out, self.blocks, "README sample is stale; paste the new output from examples/demo_home.py")


if __name__ == "__main__":
    unittest.main()
