import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

from helpdiff.cli import main
from helpdiff.model import Snapshot
from tests.helpers import EXAMPLES


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as exc:  # argparse errors
            code = exc.code
    return code, out.getvalue(), err.getvalue()


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.v1 = os.path.join(cls.tmp.name, "v1.json")
        cls.v2 = os.path.join(cls.tmp.name, "v2.json")
        for path, script in ((cls.v1, "acme_v1.py"), (cls.v2, "acme_v2.py")):
            cmd = '"%s" "%s"' % (sys.executable, os.path.join(EXAMPLES, script))
            code, _out, err = run("snap", cmd, "--name", "acme", "-o", path, "-q")
            assert code == 0, err

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_snapshot_contents(self):
        snap = Snapshot.from_json(read(self.v2))
        self.assertEqual(
            sorted(snap.commands),
            ["", "config", "config get", "config list", "deploy", "service", "service logs", "status"],
        )
        self.assertEqual(snap.commands["deploy"].find_flag("--env").choices, ["dev", "prod"])
        self.assertEqual(snap.tool_version, "acme 2.0.0")

    def test_snapshot_is_deterministic(self):
        again = os.path.join(self.tmp.name, "again.json")
        cmd = '"%s" "%s"' % (sys.executable, os.path.join(EXAMPLES, "acme_v2.py"))
        run("snap", cmd, "--name", "acme", "-o", again, "-q")
        self.assertEqual(read(again), read(self.v2))

    def test_diff_finds_the_planned_breakages(self):
        code, out, _ = run("diff", self.v1, self.v2, "--format", "json")
        self.assertEqual(code, 1)
        data = json.loads(out)
        got = {(c["kind"], c["command"], c["subject"]) for c in data["changes"] if c["severity"] == "breaking"}
        self.assertEqual(
            got,
            {
                ("choice-removed", "deploy", "--env"),
                ("flag-removed", "deploy", "--force"),
                ("flag-now-takes-value", "deploy", "--verbose"),
                ("positional-added", "deploy", "region"),
                ("command-removed", "logs", "logs"),
                ("flag-removed", "status", "--json"),
                ("command-alias-removed", "status", "st"),
            },
        )
        self.assertEqual(data["summary"]["breaking"], 7)

    def test_diff_same_snapshot_exits_zero(self):
        code, out, _ = run("diff", self.v1, self.v1, "--color", "never")
        self.assertEqual(code, 0)
        self.assertIn("No differences", out)

    def test_fail_on_never(self):
        self.assertEqual(run("diff", self.v1, self.v2, "--fail-on", "never")[0], 0)

    def test_scripts_impact(self):
        code, out, _ = run("diff", self.v1, self.v2, "--scripts", os.path.join(EXAMPLES, "scripts"), "--format", "json")
        self.assertEqual(code, 1)
        hits = json.loads(out)["affected_scripts"]
        self.assertGreaterEqual(len(hits), 8)
        self.assertTrue(all(h["file"].endswith(("deploy.sh", "ci.yml")) for h in hits))
        # the v1 scripts are clean against v1: nothing is reported when nothing changed
        code, out, _ = run("diff", self.v1, self.v1, "--scripts", os.path.join(EXAMPLES, "scripts"))
        self.assertEqual(code, 0)

    def test_check_command(self):
        code, out, _ = run("check", self.v1, os.path.join(EXAMPLES, "scripts"), "--color", "never")
        self.assertEqual((code, "No unknown"), (0, out[:10]))
        code, out, _ = run("check", self.v2, os.path.join(EXAMPLES, "scripts"), "--color", "never")
        self.assertEqual(code, 1)
        self.assertIn("deploy.sh:5", out)

    def test_markdown_output(self):
        _code, out, _ = run("diff", self.v1, self.v2, "--format", "markdown")
        self.assertTrue(out.startswith("### CLI surface diff: `acme`"))
        self.assertIn("**7 breaking**", out)

    def test_verify_roundtrip(self):
        snap_file = os.path.join(self.tmp.name, "verify.json")
        cmd = '"%s" "%s"' % (sys.executable, os.path.join(EXAMPLES, "acme_v1.py"))
        self.assertEqual(run("verify", cmd, "--name", "acme", "--snapshot", snap_file, "--update", "-q")[0], 0)
        self.assertEqual(run("verify", cmd, "--name", "acme", "--snapshot", snap_file, "-q")[0], 0)
        cmd2 = '"%s" "%s"' % (sys.executable, os.path.join(EXAMPLES, "acme_v2.py"))
        self.assertEqual(run("verify", cmd2, "--name", "acme", "--snapshot", snap_file, "-q")[0], 1)

    def test_errors_exit_2(self):
        self.assertEqual(run("diff", "/nonexistent.json", self.v1)[0], 2)
        self.assertEqual(run("snap", "definitely-not-a-real-command-xyz", "-q")[0], 2)
        bad = os.path.join(self.tmp.name, "bad.json")
        write(bad, '{"nope": 1}')
        code, _out, err = run("diff", bad, self.v1)
        self.assertEqual(code, 2)
        self.assertIn("not a helpdiff snapshot", err)

    def test_parse_command(self):
        code, out, _ = run("parse", os.path.join(os.path.dirname(__file__), "fixtures", "clap.txt"))
        self.assertEqual(code, 0)
        self.assertEqual([s["name"] for s in json.loads(out)["subcommands"]], ["index", "help"])


if __name__ == "__main__":
    unittest.main()
