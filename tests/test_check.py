import os
import tempfile
import unittest

from helpdiff.check import check_files, new_findings
from helpdiff.model import Command, Flag, Positional, Snapshot


def make_snapshot():
    s = Snapshot(name="acme", command=["acme"])
    s.commands[""] = Command(path=[], subcommands=["deploy", "status"], flags=[Flag(["--config"], value="FILE")])
    s.commands["deploy"] = Command(
        path=["deploy"],
        flags=[
            Flag(["-e", "--env"], value="{dev,prod}", choices=["dev", "prod"]),
            Flag(["-y", "--yes"]),
            Flag(["-v", "--verbose"]),
            Flag(["--dry-run"]),
        ],
        positionals=[Positional("target")],
    )
    s.commands["status"] = Command(path=["status"], aliases=["st"], flags=[Flag(["--output"], value="FMT")])
    return s


class CheckTests(unittest.TestCase):
    def scan(self, text, name="run.sh", snap=None):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, name)
            with open(path, "w") as fh:
                fh.write(text)
            return check_files(snap or make_snapshot(), [path])

    def kinds(self, text, **kw):
        return [(f.kind, f.token) for f in self.scan(text, **kw)]

    def test_clean_script(self):
        self.assertEqual(self.kinds("acme deploy web -e prod --yes -v\nacme st\n"), [])

    def test_unknown_flag_and_subcommand(self):
        self.assertEqual(self.kinds("acme deploy web --force\n"), [("unknown-flag", "--force")])
        self.assertEqual(self.kinds("acme logs web\n"), [("unknown-subcommand", "logs")])

    def test_invalid_choice_both_forms(self):
        self.assertEqual(self.kinds("acme deploy w --env staging\n"), [("invalid-value", "staging")])
        self.assertEqual(self.kinds("acme deploy w --env=staging\n"), [("invalid-value", "staging")])
        self.assertEqual(self.kinds("acme deploy w -e staging\n"), [("invalid-value", "staging")])

    def test_missing_argument_and_value(self):
        self.assertEqual(self.kinds("acme deploy\n"), [("missing-argument", "target")])
        self.assertEqual(self.kinds("acme deploy w --env\n"), [("missing-value", "--env")])

    def test_value_is_not_mistaken_for_subcommand_or_positional(self):
        self.assertEqual(self.kinds("acme --config x.toml deploy web\n"), [])

    def test_short_flag_clusters(self):
        self.assertEqual(self.kinds("acme deploy web -yv\n"), [])
        self.assertEqual(self.kinds("acme deploy web -yz\n"), [("unknown-flag", "-z")])

    def test_dynamic_tokens_are_skipped(self):
        self.assertEqual(self.kinds('acme deploy "$SVC" --env "$E"\n'), [])
        self.assertEqual(self.kinds("acme $CMD --whatever\n"), [])

    def test_wrappers_pipes_and_env(self):
        text = "FOO=1 sudo acme deploy web --nope | tee log\n"
        self.assertEqual(self.kinds(text), [("unknown-flag", "--nope")])

    def test_line_continuation_and_yaml_run(self):
        text = "steps:\n  - run: |\n      acme deploy web \\\n        --nope\n"
        found = self.scan(text, name="ci.yml")
        self.assertEqual([(f.kind, f.token, f.line) for f in found], [("unknown-flag", "--nope", 3)])

    def test_markdown_only_looks_inside_fences(self):
        text = "acme deploy --nope\n\n```sh\nacme deploy w --nope2\n```\n"
        self.assertEqual(self.kinds(text, name="README.md"), [("unknown-flag", "--nope2")])

    def test_comments_and_other_tools_ignored(self):
        self.assertEqual(self.kinds("# acme deploy --nope\nnotacme deploy --nope\n"), [])

    def test_unique_prefix_abbreviation_accepted(self):
        self.assertEqual(self.kinds("acme deploy w --dry\n"), [])

    def test_only_new_findings_after_upgrade(self):
        old = make_snapshot()
        new = make_snapshot()
        new.commands["deploy"].flags = [f for f in new.commands["deploy"].flags if f.names != ["-v", "--verbose"]]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a.sh")
            with open(path, "w") as fh:
                fh.write("acme deploy w --verbose\nacme deploy w --always-unknown\n")
            # --always-unknown is also unknown against `old`, so it is not an upgrade regression
            got = new_findings(old, new, [path])
        self.assertEqual([(f.kind, f.token) for f in got], [("unknown-flag", "--verbose")])


if __name__ == "__main__":
    unittest.main()
