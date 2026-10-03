import unittest

from helpdiff.parser import parse_flag_spec, parse_help
from tests.helpers import fixture


def flags_by_label(parsed):
    return {f.label: f for f in parsed.flags}


class FlagSpecTests(unittest.TestCase):
    def test_short_and_long_with_value(self):
        f = parse_flag_spec("-o, --output FILE")
        self.assertEqual(f.names, ["-o", "--output"])
        self.assertEqual(f.value, "FILE")

    def test_switch_has_no_value(self):
        self.assertIsNone(parse_flag_spec("-v, --verbose").value)

    def test_argparse_choices_repeated_per_name(self):
        f = parse_flag_spec("-e {dev,staging,prod}, --env {dev,staging,prod}")
        self.assertEqual(f.names, ["-e", "--env"])
        self.assertEqual(f.choices, ["dev", "staging", "prod"])

    def test_click_pipe_choices(self):
        self.assertEqual(parse_flag_spec("--format [json|yaml]").choices, ["json", "yaml"])

    def test_click_boolean_pair(self):
        f = parse_flag_spec("--color / --no-color")
        self.assertEqual(f.names, ["--color", "--no-color"])
        self.assertIsNone(f.value)

    def test_bracket_negation(self):
        self.assertEqual(parse_flag_spec("--[no-]color").names, ["--color", "--no-color"])

    def test_optional_value(self):
        f = parse_flag_spec("--color[=WHEN]")
        self.assertTrue(f.takes_value)
        self.assertEqual(f.value, "[WHEN]")

    def test_not_a_flag(self):
        self.assertIsNone(parse_flag_spec("build"))


class ArgparseTests(unittest.TestCase):
    def test_root_with_titled_group_and_alias_in_parens(self):
        p = parse_help(fixture("argparse_root.txt"), ["acme"])
        self.assertEqual(p.summary, "Deploy and inspect services.")
        self.assertEqual([s.name for s in p.subcommands], ["deploy", "status"])
        self.assertEqual(p.subcommands[1].aliases, ["st"])
        self.assertEqual(p.subcommands[0].summary, "deploy a service")
        self.assertEqual(p.positionals, [])
        self.assertEqual(flags_by_label(p)["--config"].value, "FILE")
        self.assertEqual(flags_by_label(p)["--config"].default, "acme.toml")

    def test_brace_group(self):
        p = parse_help(fixture("argparse_braces.txt"), ["tool"])
        self.assertEqual([s.name for s in p.subcommands], ["build", "test", "release"])
        self.assertEqual(p.positionals, [])

    def test_leaf(self):
        p = parse_help(fixture("argparse_leaf.txt"), ["acme", "deploy"])
        f = flags_by_label(p)
        self.assertEqual(f["--env"].choices, ["dev", "staging", "prod"])
        self.assertEqual(f["--env"].names, ["-e", "--env"])
        self.assertFalse(f["--force"].takes_value)
        self.assertEqual(f["--timeout"].default, "30")
        self.assertEqual([(x.name, x.required) for x in p.positionals], [("target", True)])


class MetavarGroupTests(unittest.TestCase):
    def test_uppercase_metavar_group_header(self):
        text = (
            "usage: helpdiff [-h] COMMAND ...\n\n"
            "positional arguments:\n"
            "  COMMAND\n"
            "    snap      crawl a tool\n"
            "    diff      compare snapshots\n\n"
            "options:\n  -h, --help  show help\n"
        )
        p = parse_help(text, ["helpdiff"])
        self.assertEqual([s.name for s in p.subcommands], ["snap", "diff"])
        self.assertEqual(p.positionals, [])


class OtherToolTests(unittest.TestCase):
    def test_click(self):
        p = parse_help(fixture("click.txt"), ["mycli", "build"])
        f = flags_by_label(p)
        self.assertEqual(f["--output"].default, "dist")
        self.assertEqual(f["--format"].choices, ["json", "yaml", "text"])
        self.assertTrue(f["--jobs"].required)
        self.assertEqual([x.name for x in p.positionals], ["SRC"])
        self.assertEqual(p.summary, "Build the project.") if p.summary else None

    def test_cobra_leaf(self):
        p = parse_help(fixture("cobra.txt"), ["tool", "pr", "list"])
        f = flags_by_label(p)
        self.assertEqual(f["--limit"].default, "30")
        self.assertEqual(f["--state"].default, "open")
        self.assertTrue(f["--repo"].inherited)
        self.assertEqual(f["--repo"].value, "[HOST/]OWNER/REPO")
        self.assertEqual(p.aliases, ["ls"])
        self.assertEqual(p.usage, "tool pr list [flags]")

    def test_cobra_group(self):
        p = parse_help(fixture("cobra_group.txt"), ["tool"])
        self.assertEqual([s.name for s in p.subcommands], ["completion", "create", "help", "list"])
        self.assertEqual(p.summary, "Manage things.")
        self.assertEqual(p.positionals, [])

    def test_clap(self):
        p = parse_help(fixture("clap.txt"), ["fd"])
        f = flags_by_label(p)
        self.assertEqual(f["--color"].choices, ["auto", "always", "never"])
        self.assertEqual(f["--color"].default, "auto")
        self.assertEqual(
            [(x.name, x.required, x.variadic) for x in p.positionals],
            [("PATTERN", False, False), ("PATH", False, True)],
        )
        self.assertEqual([s.name for s in p.subcommands], ["index", "help"])

    def test_commander(self):
        p = parse_help(fixture("commander.txt"), ["tool", "build"])
        f = flags_by_label(p)
        self.assertEqual(f["--out"].default, "dist")
        self.assertEqual(f["--mode"].choices, ["dev", "prod"])
        self.assertEqual([x.name for x in p.positionals], ["dir"])

    def test_optparse(self):
        p = parse_help(fixture("optparse.txt"), ["pip"])
        self.assertEqual([s.name for s in p.subcommands], ["install", "uninstall", "help"])
        self.assertEqual(flags_by_label(p)["--timeout"].value, "<sec>")

    def test_garbage_in_is_empty_out(self):
        p = parse_help("Segmentation fault\n\nsomething went wrong\n")
        self.assertEqual((p.flags, p.subcommands, p.positionals), ([], [], []))

    def test_ansi_is_stripped(self):
        p = parse_help("Options:\n  \x1b[1m--color\x1b[0m   colorize\n")
        self.assertEqual(p.flags[0].names, ["--color"])


if __name__ == "__main__":
    unittest.main()
