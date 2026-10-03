"""helpdiff command-line interface."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from typing import List, Optional, Sequence

from . import __version__
from .check import Finding, check_files, new_findings
from .crawl import CrawlError, crawl
from .diff import BREAKING, WARNING, Change, diff_snapshots
from .model import Snapshot
from .parser import parse_help
from .render import (
    render_findings,
    render_json,
    render_markdown,
    render_text,
    render_tree,
)

EXIT_OK, EXIT_FINDINGS, EXIT_ERROR = 0, 1, 2


class UsageError(Exception):
    pass


def _use_color(mode: str) -> bool:
    if mode == "always":
        return True
    if mode == "never" or os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _load(path: str) -> Snapshot:
    try:
        with open(path, encoding="utf-8") as fh:
            return Snapshot.from_json(fh.read())
    except OSError as exc:
        raise UsageError("cannot read %s: %s" % (path, exc.strerror or exc)) from None
    except (ValueError, KeyError) as exc:
        raise UsageError("%s: %s" % (path, exc)) from None


def _crawl_from_args(args: argparse.Namespace) -> Snapshot:
    argv = shlex.split(args.command)
    if not argv:
        raise UsageError("empty command")

    def progress(label: str) -> None:
        if not args.quiet and sys.stderr.isatty():
            sys.stderr.write("\r\x1b[K  crawling %s" % label[:70])
            sys.stderr.flush()

    try:
        snap = crawl(
            argv,
            name=args.name,
            help_flags=tuple(args.help_flag) or ("--help", "-h"),
            max_depth=args.max_depth,
            max_commands=args.max_commands,
            timeout=args.timeout,
            jobs=args.jobs,
            skip=tuple(args.skip),
            progress=progress,
        )
    except CrawlError as exc:
        raise UsageError(str(exc)) from None
    finally:
        if not args.quiet and sys.stderr.isatty():
            sys.stderr.write("\r\x1b[K")
    return snap


def _fail_code(changes: List[Change], impact: Optional[List[Finding]], fail_on: str) -> int:
    if fail_on == "never":
        return EXIT_OK
    levels = {BREAKING} if fail_on == "breaking" else {BREAKING, WARNING}
    if any(c.severity in levels for c in changes) or impact:
        return EXIT_FINDINGS
    return EXIT_OK


def _emit_diff(args: argparse.Namespace, old: Snapshot, new: Snapshot) -> int:
    changes = diff_snapshots(old, new)
    impact = new_findings(old, new, args.scripts) if args.scripts else None
    if args.format == "json":
        sys.stdout.write(render_json(changes, impact))
    elif args.format == "markdown":
        sys.stdout.write(render_markdown(old, new, changes, impact, args.all))
    else:
        sys.stdout.write(render_text(old, new, changes, impact, args.all, _use_color(args.color)))
    return _fail_code(changes, impact, args.fail_on)


def cmd_snap(args: argparse.Namespace) -> int:
    snap = _crawl_from_args(args)
    text = snap.to_json()
    if args.output and args.output != "-":
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
        if not args.quiet:
            n_flags = sum(len(c.flags) for c in snap.commands.values())
            sys.stderr.write("wrote %s: %d commands, %d flags\n" % (args.output, len(snap.commands), n_flags))
    else:
        sys.stdout.write(text)
    return EXIT_OK


def cmd_diff(args: argparse.Namespace) -> int:
    return _emit_diff(args, _load(args.old), _load(args.new))


def cmd_verify(args: argparse.Namespace) -> int:
    live = _crawl_from_args(args)
    if args.update:
        with open(args.snapshot, "w", encoding="utf-8") as fh:
            fh.write(live.to_json())
        sys.stderr.write("updated %s\n" % args.snapshot)
        return EXIT_OK
    if not os.path.exists(args.snapshot):
        raise UsageError("%s does not exist; create it with `helpdiff verify ... --update`" % args.snapshot)
    return _emit_diff(args, _load(args.snapshot), live)


def cmd_check(args: argparse.Namespace) -> int:
    snap = _load(args.snapshot)
    findings = check_files(snap, args.paths)
    if args.format == "json":
        sys.stdout.write(render_json([], findings))
    else:
        sys.stdout.write(render_findings(findings, _use_color(args.color)))
    return EXIT_FINDINGS if findings else EXIT_OK


def cmd_show(args: argparse.Namespace) -> int:
    sys.stdout.write(render_tree(_load(args.snapshot)))
    return EXIT_OK


def cmd_parse(args: argparse.Namespace) -> int:
    if args.file == "-":
        text = sys.stdin.read()
    else:
        with open(args.file, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    parsed = parse_help(text, args.known or [])
    payload = {
        "summary": parsed.summary,
        "usage": parsed.usage,
        "aliases": parsed.aliases,
        "subcommands": [{"name": s.name, "aliases": s.aliases, "summary": s.summary} for s in parsed.subcommands],
        "positionals": [p.to_dict() for p in parsed.positionals],
        "flags": [f.to_dict() for f in parsed.flags],
    }
    sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return EXIT_OK


def _add_crawl_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--name", help="name scripts use to invoke the tool (default: derived from the command)")
    p.add_argument(
        "--help-flag",
        action="append",
        default=[],
        metavar="FLAG",
        help="flag that prints help; repeatable (default: --help, then -h)",
    )
    p.add_argument("--max-depth", type=int, default=4, help="how deep to follow subcommands (default: 4)")
    p.add_argument("--max-commands", type=int, default=400, help="stop after this many commands (default: 400)")
    p.add_argument("--timeout", type=float, default=10.0, help="seconds to wait per help call (default: 10)")
    p.add_argument("--jobs", type=int, default=8, help="parallel help calls (default: 8)")
    p.add_argument(
        "--skip",
        action="append",
        default=[],
        metavar="PATH",
        help='do not crawl this subcommand path, e.g. --skip "extension"; repeatable',
    )
    p.add_argument("-q", "--quiet", action="store_true", help="no progress output")


def _add_diff_options(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--scripts",
        nargs="+",
        metavar="PATH",
        default=[],
        help="files/directories to scan for uses of anything that broke",
    )
    p.add_argument("--format", choices=("text", "markdown", "json"), default="text")
    p.add_argument(
        "--fail-on",
        choices=("breaking", "warning", "never"),
        default="breaking",
        help="exit 1 when a change at this level is found (default: breaking)",
    )
    p.add_argument("--all", action="store_true", help="also list additions")
    p.add_argument("--color", choices=("auto", "always", "never"), default="auto")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="helpdiff",
        description="Diff the --help surface of a command-line tool between versions, "
        "and find the scripts that would break.",
    )
    parser.add_argument("--version", action="version", version="helpdiff " + __version__)
    sub = parser.add_subparsers(dest="cmd", metavar="COMMAND")
    sub.required = True

    p = sub.add_parser(
        "snap",
        help="crawl a command's --help tree into a JSON snapshot",
        description="Run `<command> [subcommand ...] --help` recursively and save the parsed surface.",
    )
    p.add_argument("command", help='command to crawl, quoted if it has arguments, e.g. "gh" or "python3 -m mytool"')
    p.add_argument("-o", "--output", help="write snapshot here (default: stdout)")
    _add_crawl_options(p)
    p.set_defaults(func=cmd_snap)

    p = sub.add_parser(
        "diff", help="compare two snapshots", description="Compare two snapshots; exits 1 if anything breaking changed."
    )
    p.add_argument("old", help="older snapshot (.json)")
    p.add_argument("new", help="newer snapshot (.json)")
    _add_diff_options(p)
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser(
        "verify",
        help="crawl the live tool and diff it against a committed snapshot (CI)",
        description="Crawl the tool and compare with a snapshot file; use --update to refresh it.",
    )
    p.add_argument("command", help="command to crawl")
    p.add_argument("--snapshot", required=True, help="snapshot file to compare with")
    p.add_argument("--update", action="store_true", help="overwrite the snapshot with the live surface")
    _add_crawl_options(p)
    _add_diff_options(p)
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser(
        "check",
        help="find unknown flags/subcommands in scripts, against a snapshot",
        description="Scan shell scripts, Makefiles, CI YAML, Dockerfiles and Markdown code blocks.",
    )
    p.add_argument("snapshot", help="snapshot of the tool version you will run")
    p.add_argument("paths", nargs="+", metavar="PATH")
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("show", help="list the commands in a snapshot")
    p.add_argument("snapshot")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("parse", help="parse one saved help text and print the result (for debugging)")
    p.add_argument("file", help="file with help output, or - for stdin")
    p.add_argument("--known", action="append", help="leading usage words to ignore, e.g. the program name")
    p.set_defaults(func=cmd_parse)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        code: int = args.func(args)
        return code
    except UsageError as exc:
        sys.stderr.write("helpdiff: error: %s\n" % exc)
        return EXIT_ERROR
    except BrokenPipeError:
        return EXIT_OK
    except KeyboardInterrupt:
        return 130
