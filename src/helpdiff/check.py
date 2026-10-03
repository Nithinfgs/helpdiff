"""Find invocations of a tool in scripts and check them against a snapshot."""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from difflib import get_close_matches
from typing import Iterable, Iterator, List, Optional, Set, Tuple

from .model import Command, Flag, Snapshot

SCRIPT_EXTS = {".sh", ".bash", ".zsh", ".ksh", ".mk", ".yml", ".yaml", ".md", ".txt", ".cmd", ".ps1"}
SCRIPT_NAMES = {"makefile", "gnumakefile", "justfile", "dockerfile", "containerfile", "taskfile"}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", "dist", "build", "target", ".mypy_cache"}
MAX_FILE_BYTES = 2_000_000

WRAPPERS = {
    "sudo",
    "time",
    "exec",
    "command",
    "nohup",
    "xargs",
    "env",
    "then",
    "do",
    "else",
    "watch",
    "nice",
    "doas",
    "run",
    "!",
}
SEGMENT_SPLIT = re.compile(r"&&|\|\||[;|(){}`]|\$\(")
LEADING_NOISE = re.compile(r"^(?:[-@+]\s*|\$\s+|>\s+|run:\s*|RUN\s+|CMD\s+|ENTRYPOINT\s+|-\s+|\d+\.\s+)+")
ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
DYNAMIC = re.compile(r"[$`*?{}\\]|^\.\.\.$|^<.*>$")
IDENT = re.compile(r"^[A-Za-z][\w-]*$")


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    kind: str  # unknown-flag | unknown-subcommand
    token: str
    command: str
    text: str
    hint: Optional[str] = None

    @property
    def ident(self) -> Tuple[str, int, str, str, str]:
        return (self.file, self.line, self.kind, self.token, self.command)

    def message(self) -> str:
        where = "`%s`" % (self.command or "(root)")
        if self.kind == "unknown-flag":
            msg = "%s is not a flag of %s" % (self.token, where)
        elif self.kind == "unknown-subcommand":
            msg = "`%s` is not a subcommand of %s" % (self.token, where)
        elif self.kind == "invalid-value" or self.kind == "missing-value":
            return "%s: %s" % (where, self.hint)
        else:
            return "%s %s" % (where, self.hint)
        if self.hint:
            msg += " (%s)" % self.hint
        return msg


def iter_script_files(paths: Iterable[str]) -> Iterator[str]:
    for path in paths:
        if os.path.isfile(path):
            yield path
            continue
        for root, dirs, files in os.walk(path):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for name in sorted(files):
                lower = name.lower()
                ext = os.path.splitext(lower)[1]
                if ext in SCRIPT_EXTS or lower in SCRIPT_NAMES or lower.startswith("dockerfile"):
                    full = os.path.join(root, name)
                    try:
                        if os.path.getsize(full) <= MAX_FILE_BYTES:
                            yield full
                    except OSError:
                        continue


def _logical_lines(text: str, markdown: bool) -> Iterator[Tuple[int, str]]:
    """Yield (starting line number, text) with backslash continuations joined."""
    in_fence = not markdown
    buf: List[str] = []
    start = 0
    for i, raw in enumerate(text.splitlines(), 1):
        if markdown:
            if raw.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if not in_fence:
                continue
        line = raw.rstrip()
        if not buf:
            start = i
        if line.endswith("\\"):
            buf.append(line[:-1])
            continue
        buf.append(line)
        yield start, " ".join(s.strip() for s in buf)
        buf = []
    if buf:
        yield start, " ".join(s.strip() for s in buf)


def _commands_in(line: str) -> Iterator[List[str]]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return
    for seg in SEGMENT_SPLIT.split(stripped):
        seg = LEADING_NOISE.sub("", seg.strip())
        if not seg:
            continue
        try:
            lexer = shlex.shlex(seg, posix=True)
            lexer.whitespace_split = True
            lexer.commenters = "#"
            tokens = list(lexer)
        except ValueError:
            tokens = seg.split()
        yield tokens


def _strip_prefix(tokens: List[str]) -> List[str]:
    i = 0
    while i < len(tokens) and (ENV_ASSIGN.match(tokens[i]) or tokens[i] in WRAPPERS):
        i += 1
    return tokens[i:]


def _name_matches(token: str, name: str) -> bool:
    base = os.path.basename(token)
    return base == name or os.path.splitext(base)[0] == name


def _lookup(cmd: Command, token: str) -> Optional[Flag]:
    """Resolve a --long / -s token (without any =value) to a flag of cmd."""
    flag = cmd.find_flag(token)
    if flag is not None:
        return flag
    if token.startswith("--no-"):
        base = cmd.find_flag("--" + token[5:])
        if base is not None and not base.takes_value:
            return base
    if token.startswith("--") and len(token) > 3:
        # argparse accepts unambiguous prefixes
        matches = [f for f in cmd.flags if any(n.startswith(token) for n in f.long_names)]
        if len(matches) == 1:
            return matches[0]
    return None


def _needs_value(flag: Flag) -> bool:
    return flag.takes_value and not (flag.value or "").startswith("[")


def _value_problem(flag: Flag, value: str) -> Optional[str]:
    if flag.choices and not DYNAMIC.search(value) and value not in flag.choices:
        return "%s is not a valid value for %s (choices: %s)" % (value, flag.label, ", ".join(flag.choices))
    return None


Problem = Tuple[str, str, str, Optional[str]]


def _check_tokens(snap: Snapshot, tokens: List[str]) -> List[Problem]:
    """Return (kind, token, command_key, hint) problems for one invocation."""
    problems: List[Problem] = []
    node = snap.root
    pending: Optional[Flag] = None  # a flag still waiting for its value token
    positionals_seen = 0
    complete = True  # False when the walk stopped early (dynamic token, `--`, unknown subcommand)

    def add(kind: str, token: str, hint: Optional[str] = None) -> None:
        problems.append((kind, token, node.key, hint))

    for tok in tokens[1:]:
        if pending is not None:
            flag, pending = pending, None
            msg = _value_problem(flag, tok)
            if msg:
                add("invalid-value", tok, msg)
            continue
        if tok == "--":
            complete = False
            break
        if DYNAMIC.search(tok):
            if tok.startswith("-"):
                continue
            complete = False
            break
        if tok.startswith("-") and len(tok) > 1 and not re.match(r"^-\d", tok):
            name, eq, value = tok.partition("=")
            if name.startswith("--"):
                found = _lookup(node, name)
                if found is None:
                    names = [n for f in node.flags for n in f.long_names]
                    close = get_close_matches(name, names, n=1, cutoff=0.85)
                    add("unknown-flag", name, "did you mean %s?" % close[0] if close else None)
                elif _needs_value(found):
                    if eq:
                        msg = _value_problem(found, value)
                        if msg:
                            add("invalid-value", value, msg)
                    else:
                        pending = found
            else:
                chars = name[1:]
                for j, ch in enumerate(chars):
                    short = _lookup(node, "-" + ch)
                    if short is None:
                        add("unknown-flag", "-" + ch)
                        break
                    if _needs_value(short):
                        attached = chars[j + 1 :] or (value if eq else "")
                        if attached:
                            msg = _value_problem(short, attached)
                            if msg:
                                add("invalid-value", attached, msg)
                        else:
                            pending = short
                        break
            continue
        child = snap.child(node, tok)
        if child is not None and positionals_seen == 0:
            node = child
            continue
        if node.subcommands and not node.positionals and IDENT.match(tok):
            close = get_close_matches(tok, node.subcommands, n=1, cutoff=0.6)
            add("unknown-subcommand", tok, "did you mean `%s`?" % close[0] if close else None)
            complete = False
            break
        positionals_seen += 1

    if pending is not None:
        add("missing-value", pending.label, "%s needs a value" % pending.label)
    elif complete and not problems:
        required = [pos for pos in node.positionals if pos.required]
        if node.crawled and not node.subcommands and positionals_seen < len(required):
            arg_list = " ".join("<%s>" % pos.name for pos in required)
            add(
                "missing-argument",
                required[positionals_seen].name,
                "expects %d argument(s): %s" % (len(required), arg_list),
            )
    return problems


def check_files(snap: Snapshot, paths: Iterable[str]) -> List[Finding]:
    findings: List[Finding] = []
    seen: Set[Tuple[str, int, str, str, str]] = set()
    for path in iter_script_files(paths):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        markdown = path.lower().endswith((".md", ".txt"))
        for lineno, line in _logical_lines(text, markdown):
            if snap.name not in line:
                continue
            for tokens in _commands_in(line):
                tokens = _strip_prefix(tokens)
                if not tokens or not _name_matches(tokens[0], snap.name):
                    continue
                for kind, token, cmd_key, hint in _check_tokens(snap, tokens):
                    f = Finding(
                        path,
                        lineno,
                        kind,
                        token,
                        " ".join([snap.name] + ([cmd_key] if cmd_key else [])),
                        line.strip(),
                        hint,
                    )
                    if f.ident not in seen:
                        seen.add(f.ident)
                        findings.append(f)
    return findings


def new_findings(old: Snapshot, new: Snapshot, paths: List[str]) -> List[Finding]:
    """Problems that the upgrade introduces: present against `new` but not against `old`.

    Subtracting the old baseline cancels out anything this heuristic checker
    cannot understand about the tool, so only real regressions are reported.
    """
    baseline = {(f.file, f.line, f.kind, f.token) for f in check_files(old, paths)}
    return [f for f in check_files(new, paths) if (f.file, f.line, f.kind, f.token) not in baseline]
