import unittest

from helpdiff.diff import BREAKING, INFO, WARNING, diff_snapshots
from helpdiff.model import Command, Flag, Positional, Snapshot


def snap(*commands):
    s = Snapshot(name="t", command=["t"])
    for c in commands:
        s.commands[c.key] = c
    return s


def root(flags=(), subs=(), pos=(), aliases=()):
    return Command(path=[], flags=list(flags), subcommands=list(subs), positionals=list(pos))


def kinds(changes):
    return {(c.severity, c.kind, c.subject) for c in changes}


class DiffTests(unittest.TestCase):
    def test_identical_is_empty(self):
        a = snap(root([Flag(["--x"])]))
        self.assertEqual(diff_snapshots(a, a), [])

    def test_flag_removed_with_rename_hint(self):
        old = snap(root([Flag(["-f", "--force"], description="skip the confirmation prompt")]))
        new = snap(root([Flag(["-y", "--yes"], description="skip the confirmation prompt")]))
        ch = diff_snapshots(old, new)
        removed = [c for c in ch if c.kind == "flag-removed"][0]
        self.assertEqual(removed.severity, BREAKING)
        self.assertIn("--yes", removed.hint)
        self.assertNotIn(("info", "flag-added", "--yes"), kinds(ch))

    def test_alias_removed_is_breaking_alias_added_is_not(self):
        old = snap(root([Flag(["-v", "--verbose"])]))
        new = snap(root([Flag(["--verbose", "--debug"])]))
        k = kinds(diff_snapshots(old, new))
        self.assertIn((BREAKING, "flag-alias-removed", "-v"), k)
        self.assertIn((INFO, "flag-alias-added", "--verbose"), k)

    def test_switch_gains_value(self):
        old = snap(root([Flag(["--verbose"])]))
        new = snap(root([Flag(["--verbose"], value="LEVEL")]))
        self.assertIn((BREAKING, "flag-now-takes-value", "--verbose"), kinds(diff_snapshots(old, new)))

    def test_choices(self):
        old = snap(root([Flag(["--env"], value="{a,b}", choices=["a", "b"])]))
        new = snap(root([Flag(["--env"], value="{b,c}", choices=["b", "c"])]))
        k = kinds(diff_snapshots(old, new))
        self.assertIn((BREAKING, "choice-removed", "--env"), k)
        self.assertIn((INFO, "choice-added", "--env"), k)

    def test_default_change_is_warning(self):
        old = snap(root([Flag(["--n"], value="int", default="30")]))
        new = snap(root([Flag(["--n"], value="int", default="10")]))
        self.assertIn((WARNING, "default-changed", "--n"), kinds(diff_snapshots(old, new)))

    def test_new_required_flag_and_positional(self):
        old = snap(root())
        new = snap(root([Flag(["--token"], value="T", required=True)], pos=[Positional("dir")]))
        k = kinds(diff_snapshots(old, new))
        self.assertIn((BREAKING, "flag-added-required", "--token"), k)
        self.assertIn((BREAKING, "positional-added", "dir"), k)

    def test_optional_positional_is_info(self):
        new = snap(root(pos=[Positional("dir", required=False)]))
        self.assertIn((INFO, "positional-added", "dir"), kinds(diff_snapshots(snap(root()), new)))

    def test_removed_command_reports_once_with_location_hint(self):
        logs = Command(path=["logs"], flags=[Flag(["--x"])])
        deep = Command(path=["logs", "tail"])
        old = snap(root(subs=["logs"]), logs, deep)
        moved = Command(path=["service", "logs"])
        new = snap(root(subs=["service"]), Command(path=["service"], subcommands=["logs"]), moved)
        ch = diff_snapshots(old, new)
        removed = [c for c in ch if c.kind == "command-removed"]
        self.assertEqual([c.command for c in removed], ["logs"])
        self.assertIn("service logs", removed[0].hint)

    def test_command_becoming_alias_is_not_breaking(self):
        old = snap(root(subs=["st"]), Command(path=["st"]), Command(path=["status"]))
        new = snap(root(subs=["status"]), Command(path=["status"], aliases=["st"]))
        ch = diff_snapshots(old, new)
        self.assertEqual([c.kind for c in ch if c.severity == BREAKING], [])

    def test_inherited_flags_are_ignored_on_children(self):
        child_old = Command(path=["a"], flags=[Flag(["--repo"], inherited=True)])
        child_new = Command(path=["a"], flags=[])
        old = snap(root(subs=["a"]), child_old)
        new = snap(root(subs=["a"]), child_new)
        self.assertEqual(diff_snapshots(old, new), [])

    def test_uncrawled_commands_are_not_compared(self):
        old = snap(root(subs=["a"]), Command(path=["a"], flags=[Flag(["--x"])]))
        new = snap(root(subs=["a"]), Command(path=["a"], crawled=False))
        self.assertEqual(diff_snapshots(old, new), [])

    def test_breaking_sorted_first(self):
        old = snap(root([Flag(["--a"])]))
        new = snap(root([Flag(["--b"]), Flag(["--c"])]))
        sev = [c.severity for c in diff_snapshots(old, new)]
        self.assertEqual(sev, sorted(sev, key=["breaking", "warning", "info"].index))


if __name__ == "__main__":
    unittest.main()


class RenderAndNormaliseTests(unittest.TestCase):
    def test_unit_words_in_defaults_are_not_a_change(self):
        old = snap(root([Flag(["--retries"], value="int", default="5 times")]))
        new = snap(root([Flag(["--retries"], value="int", default="5")]))
        self.assertEqual(diff_snapshots(old, new), [])

    def test_shared_changes_collapse_into_one_section(self):
        from helpdiff.render import sections

        subs = ["a", "b", "c", "d"]
        old = snap(
            root(subs=subs),
            *[Command(path=[s], flags=[Flag(["--shared"]), Flag(["--own-" + s])]) for s in subs],
        )
        new = snap(root(subs=subs), *[Command(path=[s]) for s in subs])
        secs = sections(diff_snapshots(old, new), "t")
        titles = [t for t, _ in secs]
        self.assertTrue(titles[0].startswith("in 4 commands"))
        self.assertEqual([c.subject for c in secs[0][1]], ["--shared"])
        self.assertEqual(titles[1:], ["t a", "t b", "t c", "t d"])
        self.assertTrue(all(len(items) == 1 for _, items in secs[1:]))
