"""Crawl a command's ``--help`` tree into a Snapshot.

helpdiff only ever runs ``<your command> [subcommands...] --help`` (and
``--version`` once at the root). Subcommand names come from the help text of
the tool being crawled, so only crawl tools you already trust to run.
"""

from __future__ import annotations

import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from .model import Command, Snapshot
from .parser import parse_help, strip_ansi

INTERPRETERS = {
    "python",
    "python3",
    "node",
    "nodejs",
    "ruby",
    "perl",
    "bash",
    "sh",
    "deno",
    "bun",
    "npx",
    "uvx",
    "java",
}
MAX_OUTPUT = 1_000_000
SKIP_CRAWL = {"help"}


class CrawlError(Exception):
    pass


def derive_name(argv: Sequence[str]) -> str:
    base = os.path.basename(argv[0])
    if base.split(".")[0] in INTERPRETERS or base in INTERPRETERS:
        for tok in argv[1:]:
            if not tok.startswith("-"):
                return os.path.splitext(os.path.basename(tok))[0]
    return base


def _env() -> Dict[str, str]:
    env = dict(os.environ)
    env.update(
        NO_COLOR="1",
        TERM="dumb",
        COLUMNS="200",
        CI="1",
        PAGER="cat",
        GIT_PAGER="cat",
        CLICOLOR="0",
        FORCE_COLOR="0",
        PYTHONIOENCODING="utf-8",
    )
    return env


def run_help(argv: Sequence[str], help_flags: Sequence[str], timeout: float) -> Optional[str]:
    """Return help text for argv (already including subcommand path), or None."""
    env = _env()
    for flag in help_flags:
        try:
            proc = subprocess.run(
                list(argv) + [flag],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=timeout,
                env=env,
            )
        except FileNotFoundError:
            raise CrawlError("command not found: %s" % argv[0]) from None
        except PermissionError:
            raise CrawlError("permission denied running: %s" % argv[0]) from None
        except subprocess.TimeoutExpired:
            continue
        out = proc.stdout[:MAX_OUTPUT].decode("utf-8", "replace")
        err = proc.stderr[:MAX_OUTPUT].decode("utf-8", "replace")
        text = strip_ansi(out if len(out.strip().splitlines()) >= 2 else (out + "\n" + err))
        if len([ln for ln in text.splitlines() if ln.strip()]) >= 2:
            return text
    return None


def _version(argv: Sequence[str], timeout: float) -> Optional[str]:
    try:
        proc = subprocess.run(
            list(argv) + ["--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            env=_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = strip_ansi(proc.stdout.decode("utf-8", "replace")).strip().splitlines()
    if proc.returncode == 0 and lines and len(lines[0]) < 200:
        # "pip 25.3 from /path/to/site-packages/pip (python 3.12)" -> "pip 25.3"
        return re.split(r"\s+from\s+(?=/|~|[A-Za-z]:\\)", lines[0].strip())[0]
    return None


def crawl(
    argv: Sequence[str],
    name: Optional[str] = None,
    help_flags: Sequence[str] = ("--help", "-h"),
    max_depth: int = 4,
    max_commands: int = 400,
    timeout: float = 10.0,
    jobs: int = 8,
    skip: Sequence[str] = (),
    progress: Optional[Callable[[str], None]] = None,
) -> Snapshot:
    argv = list(argv)
    if not argv:
        raise CrawlError("no command given")
    name = name or derive_name(argv)
    known_root = {os.path.basename(a).lower() for a in argv} | {name.lower()}

    root_text = run_help(argv, help_flags, timeout)
    if root_text is None:
        raise CrawlError("could not get help output from `%s %s`" % (" ".join(argv), help_flags[0]))

    snap = Snapshot(name=name, command=argv, tool_version=_version(argv, timeout))
    texts: Dict[str, str] = {"": root_text}

    def build(path: List[str], text: str) -> Tuple[Command, List[str]]:
        parsed = parse_help(text, known_root | {p.lower() for p in path})
        cmd = Command(
            path=path,
            summary=parsed.summary,
            usage=parsed.usage,
            aliases=[a for a in parsed.aliases if not path or a != path[-1]],
            flags=parsed.flags,
            positionals=parsed.positionals,
            subcommands=[r.name for r in parsed.subcommands],
        )
        return cmd, [r.name for r in parsed.subcommands]

    root_cmd, root_subs = build([], root_text)
    snap.commands[""] = root_cmd
    frontier: List[Tuple[List[str], List[str]]] = [([], root_subs)]

    depth = 0
    total = 1
    while frontier and depth < max_depth:
        depth += 1
        work: List[Tuple[List[str], str]] = []
        for parent_path, subs in frontier:
            for sub in subs:
                path = parent_path + [sub]
                if " ".join(path) in skip or sub in SKIP_CRAWL:
                    snap.commands[" ".join(path)] = Command(path=path, crawled=False)
                    continue
                if total + len(work) >= max_commands:
                    break
                work.append((parent_path, sub))

        def fetch(item: Tuple[List[str], str]) -> Tuple[List[str], List[str], Optional[str]]:
            parent_path, sub = item
            path = parent_path + [sub]
            if progress:
                progress(" ".join([name] + path))
            return parent_path, path, run_help(argv + path, help_flags, timeout)

        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            results = list(pool.map(fetch, work))

        next_frontier: List[Tuple[List[str], List[str]]] = []
        for parent_path, path, text in results:
            key = " ".join(path)
            parent_text = texts.get(" ".join(parent_path), "")
            if text is None or text.strip() == parent_text.strip():
                # the tool ignored the subcommand and printed its parent's help
                snap.commands[key] = Command(path=path, crawled=False)
                continue
            texts[key] = text
            cmd, subs = build(path, text)
            snap.commands[key] = cmd
            total += 1
            if subs:
                next_frontier.append((path, subs))
        frontier = next_frontier

    _attach_parent_info(snap, texts, known_root)
    return snap


def _attach_parent_info(snap: Snapshot, texts: Dict[str, str], known: Set[str]) -> None:
    """Copy summaries/aliases that a parent lists for each child onto the child."""
    for key, text in texts.items():
        parent = snap.commands.get(key)
        if parent is None:
            continue
        for ref in parse_help(text, known).subcommands:
            child = snap.commands.get(" ".join(parent.path + [ref.name]))
            if child is None:
                continue
            if ref.summary and not child.summary:
                child.summary = ref.summary
            for a in ref.aliases:
                if a not in child.aliases:
                    child.aliases.append(a)
