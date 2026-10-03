import os
import stat
import sys
import tempfile
import unittest

from helpdiff.crawl import CrawlError, crawl, derive_name


class CrawlTests(unittest.TestCase):
    def test_derive_name(self):
        self.assertEqual(derive_name(["/usr/bin/gh"]), "gh")
        self.assertEqual(derive_name(["python3", "-m", "pip"]), "pip")
        self.assertEqual(derive_name(["node", "bin/mytool.js"]), "mytool")

    def test_missing_command(self):
        with self.assertRaises(CrawlError):
            crawl(["definitely-not-a-real-command-xyz"])

    def test_version_line_drops_install_path(self):
        script = (
            "#!%s\nimport sys\n"
            "if '--version' in sys.argv:\n"
            "    print('mytool 3.1 from /opt/venv/lib/site-packages/mytool (python 3.12)')\n"
            "else:\n    print('usage: mytool [-h]\\n\\noptions:\\n  -h, --help  show help')\n" % sys.executable
        )
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "mytool")
            with open(path, "w") as fh:
                fh.write(script)
            os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
            snap = crawl([path])
        self.assertEqual(snap.tool_version, "mytool 3.1")
        self.assertEqual(snap.name, "mytool")

    def test_subcommand_that_echoes_parent_help_is_not_a_command(self):
        script = (
            "#!%s\nprint('usage: t [-h] {a,b}\\n\\npositional arguments:\\n  {a,b}\\n    a  first\\n    b  second\\n\\n"
            "options:\\n  -h, --help  show help')\n" % sys.executable
        )
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t")
            with open(path, "w") as fh:
                fh.write(script)
            os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
            snap = crawl([path])
        self.assertEqual(sorted(snap.commands), ["", "a", "b"])
        self.assertFalse(snap.commands["a"].crawled)


if __name__ == "__main__":
    unittest.main()
