"""Release metadata stays in sync: the version lives only in agent_log.py and every release has a changelog entry."""
import os, re, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import agent_log as al  # noqa: E402


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f: return f.read()


class PackagingTests(unittest.TestCase):
    def test_version_is_single_sourced(self):
        py = read("pyproject.toml")
        self.assertRegex(py, r'(?m)^dynamic = \["version"\]$')
        self.assertRegex(py, r'(?m)^version = \{ attr = "agent_log.__version__" \}$')
        self.assertNotRegex(py, r'(?m)^version = "')

    def test_version_is_semver(self):
        self.assertRegex(al.__version__, r"^\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?$")

    def test_changelog_has_current_version(self):
        self.assertRegex(read("CHANGELOG.md"), r"(?m)^## \[" + re.escape(al.__version__) + r"\] - \d{4}-\d\d-\d\d$")

    def test_console_script_target(self):
        self.assertIn('agent-log = "agent_log:main"', read("pyproject.toml"))
        self.assertTrue(callable(al.main))


if __name__ == "__main__":
    unittest.main()
