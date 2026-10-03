"""Heuristic parser for ``--help`` output.

There is no standard help format, so this recognises the conventions shared by
argparse, optparse, click, cobra (Go), clap (Rust), commander and docopt-style
tools: section headings, ``-s, --long VALUE  description`` option rows and
``name  description`` command rows. Anything it does not recognise is ignored
rather than guessed at.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Optional, Tuple

from .model import Flag, ParsedHelp, Positional, SubRef

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
GAP_RE = re.compile(r"\s{2,}")

_HEADING_COLON = re.compile(r"^([A-Za-z][A-Za-z0-9 /&'_-]{1,60}):\s*$")
_HEADING_UPPER = re.compile(r"^[A-Z][A-Z0-9 &/_-]{2,48}$")
_USAGE_RE = re.compile(r"^usage:?\s*(.*)$", re.IGNORECASE)

_GROUP = "\0group"
_NAME_RE = re.compile(r"^[A-Za-z0-9][\w.:+-]*$")
_FLAG_PART = re.compile(r"^(-{1,2}[A-Za-z0-9][\w.-]*)(?:\[=([^\]]*)\]|[= ]\s*(.*))?$")

_DEFAULT_PATTERNS = (
    re.compile(r"\(default:?\s+([^)]*)\)", re.IGNORECASE),
    re.compile(r"\[default:?\s+([^\]]*)\]", re.IGNORECASE),
    re.compile(r"\bdefault(?:s to|s)?:\s*([^\s,;)\]]+)", re.IGNORECASE),
)
_CHOICES_PATTERNS = (
    re.compile(r"\[possible values:\s*([^\]]*)\]", re.IGNORECASE),
    re.compile(r"\((?:choices|one of|possible values):\s*([^)]*)\)", re.IGNORECASE),
)
_REQUIRED_RE = re.compile(r"[\[(]required[\])]", re.IGNORECASE)
_NOISE_POSITIONALS = {
    "options",
    "option",
    "flags",
    "flag",
    "command",
    "commands",
    "subcommand",
    "subcommands",
    "args",
    "arguments",
    "[options]",
    "cmd",
}


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _classify_heading(title: str) -> Optional[str]:
    t = title.lower()
    if t in ("usage", "examples", "example", "learn more", "notes", "environment variables"):
        return None
    if "alias" in t and "command" not in t:
        return "aliases"
    if "command" in t:
        return "commands"
    if "option" in t or "flag" in t:
        return "inherited" if ("global" in t or "inherited" in t) else "options"
    if "argument" in t:
        return "positionals"
    return None


def _split_sections(lines: List[str]) -> List[Tuple[str, str, List[str]]]:
    """Return (kind, title, body_lines) for each recognised section."""
    sections: List[Tuple[str, str, List[str]]] = []
    current: Optional[Tuple[str, str, List[str]]] = None
    for line in lines:
        if line.strip() and _indent(line) == 0:
            m = _HEADING_COLON.match(line)
            title = m.group(1) if m else None
            if title is None and _HEADING_UPPER.match(line.strip()):
                title = line.strip()
            if title is not None:
                kind = _classify_heading(title)
                if current is not None:
                    sections.append(current)
                current = (kind or "other", title, [])
                continue
            if _USAGE_RE.match(line):
                if current is not None:
                    sections.append(current)
                current = ("other", "", [])
                continue
            if current is not None and current[0] != "other":
                # unindented prose ends an options/commands section
                sections.append(current)
                current = ("other", "", [])
                continue
        if current is not None:
            current[2].append(line)
    if current is not None:
        sections.append(current)
    return sections


# --------------------------------------------------------------------------- flags


def _expand_bracket_negation(part: str) -> List[str]:
    m = re.match(r"^(-{1,2})\[no-\](.+)$", part)
    if m:
        return [m.group(1) + m.group(2), m.group(1) + "no-" + m.group(2)]
    return [part]


def _clean_value(raw: str) -> Optional[str]:
    v = raw.strip()
    if not v:
        return None
    v = v.rstrip(".").strip()
    return v or None


def parse_flag_spec(spec: str) -> Optional[Flag]:
    """Parse e.g. ``-o, --output FILE`` or ``--format {json,yaml}``."""
    spec = spec.strip()
    if not spec.startswith("-"):
        return None
    parts = [p.strip() for p in re.split(r",\s+|,(?=-)|\s+/\s+|\s+\|\s+|\s+or\s+", spec) if p.strip()]
    names: List[str] = []
    value: Optional[str] = None
    optional_value = False
    for part in parts:
        neg = re.match(r"^(-{1,2})\[no-\]([\w.-]+)(.*)$", part)
        if neg:
            names.extend([neg.group(1) + neg.group(2), neg.group(1) + "no-" + neg.group(2)])
            continue
        m = _FLAG_PART.match(part)
        if not m:
            continue
        name_part = m.group(1)
        if m.group(2) is not None:
            optional_value = True
            value = value or _clean_value(m.group(2)) or "VALUE"
        elif m.group(3):
            v = _clean_value(m.group(3))
            if v and not v.startswith("-"):
                value = value or v
        names.extend(_expand_bracket_negation(name_part))
    if not names:
        return None
    seen = set()
    uniq = []
    for n in names:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    choices: List[str] = []
    if value:
        brace = re.match(r"^<?\{([^}]*)\}>?$", value)
        pipes = re.match(r"^\[([^\[\]=]+\|[^\[\]=]+)\]$", value)
        if brace:
            choices = [c.strip() for c in brace.group(1).split(",") if c.strip()]
        elif pipes:
            choices = [c.strip() for c in pipes.group(1).split("|") if c.strip()]
        elif "|" in value and re.match(r"^<?[\w.-]+(\|[\w.-]+)+>?$", value):
            choices = [c.strip() for c in value.strip("<>").split("|")]
        if choices:
            value = "{" + ",".join(choices) + "}"
        elif optional_value:
            value = "[" + value + "]"
    return Flag(names=uniq, value=value, choices=choices)


def _enrich_from_description(flag: Flag, desc: str) -> None:
    flag.description = " ".join(desc.split())
    for pat in _DEFAULT_PATTERNS:
        m = pat.search(desc)
        if m:
            d = m.group(1).strip().strip("\"'`").strip()
            if d and d.lower() not in ("none", "null", ""):
                flag.default = d
            break
    if not flag.choices:
        for pat in _CHOICES_PATTERNS:
            m = pat.search(desc)
            if m:
                raw = re.split(r",?\s*default\b", m.group(1), maxsplit=1)[0]
                flag.choices = [c.strip().strip("\"'`") for c in raw.split(",") if c.strip()]
                break
    if _REQUIRED_RE.search(desc):
        flag.required = True
    if re.search(r"\bdeprecated\b", desc, re.IGNORECASE):
        flag.deprecated = True


def _split_spec_desc(text: str) -> Tuple[str, str]:
    m = GAP_RE.search(text)
    if m:
        return text[: m.start()], text[m.end() :]
    return text, ""


def _entries(body: List[str]) -> Iterable[Tuple[int, str, List[str]]]:
    """Group a section body into (indent, first_line_text, continuation_lines)."""
    nonblank = [ln for ln in body if ln.strip()]
    if not nonblank:
        return
    base = min(_indent(ln) for ln in nonblank)
    cur: Optional[Tuple[int, str, List[str]]] = None
    for ln in body:
        if not ln.strip():
            continue
        ind = _indent(ln)
        if ind <= base + 1 or cur is None:
            if cur is not None:
                yield cur
            cur = (ind, ln.strip(), [])
        else:
            cur[2].append(ln.strip())
    if cur is not None:
        yield cur


def parse_flags(body: List[str], inherited: bool = False) -> List[Flag]:
    flags: List[Flag] = []
    nonblank = [ln for ln in body if ln.strip()]
    if not nonblank:
        return flags
    base = min(_indent(ln) for ln in nonblank)
    cur_spec: Optional[str] = None
    cur_desc: List[str] = []

    def flush() -> None:
        nonlocal cur_spec, cur_desc
        if cur_spec is not None:
            flag = parse_flag_spec(cur_spec)
            if flag is not None:
                flag.inherited = inherited
                _enrich_from_description(flag, " ".join(cur_desc))
                flags.append(flag)
        cur_spec, cur_desc = None, []

    for ln in body:
        if not ln.strip():
            continue
        ind = _indent(ln)
        text = ln.strip()
        if text.startswith("-") and ind <= base + 4 and re.match(r"^-{1,2}[A-Za-z0-9\[]", text):
            flush()
            spec, desc = _split_spec_desc(text)
            cur_spec, cur_desc = spec, [desc] if desc else []
        elif cur_spec is not None:
            cur_desc.append(text)
    flush()
    return flags


# ----------------------------------------------------------------------- commands


def _parse_command_name(raw: str) -> Optional[Tuple[str, List[str]]]:
    raw = raw.strip().rstrip(":").strip()
    if not raw or raw.startswith("-"):
        return None
    paren = re.match(r"^([A-Za-z0-9][\w.:+-]*)\s+\(([\w.,|\s-]+)\)$", raw)
    if paren:  # argparse: "status (st)"
        return paren.group(1), [a for a in re.split(r"[,|\s]+", paren.group(2)) if a]
    # "build, b"  |  "build|b"  |  "build [options] <dir>"
    first = re.split(r"\s+(?=[\[<(])", raw, maxsplit=1)[0]
    names = [n.strip() for n in re.split(r",\s*|\|", first) if n.strip()]
    if not names:
        return None
    first_tok_names = []
    for n in names:
        tok = n.split()[0].rstrip(":")
        first_tok_names.append(tok)
    if not all(_NAME_RE.match(t) for t in first_tok_names):
        return None
    if len(names[0].split()) > 1 and "," not in first and "|" not in first:
        # "build dir" is a name followed by a bare placeholder; keep just the name
        pass
    return first_tok_names[0], first_tok_names[1:]


def parse_commands(body: List[str]) -> List[SubRef]:
    refs: List[SubRef] = []
    for _ind, text, cont in _entries_with_groups(body):
        spec, desc = _split_spec_desc(text)
        brace = re.match(r"^\{([^}]*)\}", spec)
        if cont == [_GROUP] and not brace:
            continue
        if brace:
            for n in brace.group(1).split(","):
                n = n.strip()
                if n and _NAME_RE.match(n) and not any(r.name == n for r in refs):
                    refs.append(SubRef(n))
            continue
        parsed = _parse_command_name(spec)
        if parsed is None:
            continue
        name, aliases = parsed
        summary = " ".join([desc] + cont).strip()
        existing = next((r for r in refs if r.name == name), None)
        if existing:
            existing.summary = existing.summary or summary
            existing.aliases = existing.aliases or aliases
        else:
            refs.append(SubRef(name, aliases, summary))
    return refs


def _is_group_header(text: str, body: List[str], ln: str) -> bool:
    if text.startswith("{") and "}" in text:
        return True
    if GAP_RE.search(text) or not (text.startswith("<") or text.isupper()):
        return False
    after = False
    for other in body:
        if other is ln:
            after = True
            continue
        if after and other.strip():
            return _indent(other) > _indent(ln)
    return False


def _has_group(body: List[str]) -> bool:
    return any(_is_group_header(ln.strip(), body, ln) for ln in body if ln.strip())


def _entries_with_groups(body: List[str]) -> Iterable[Tuple[int, str, List[str]]]:
    """Like _entries, but argparse-style ``{a,b}`` groups nest their rows one level deeper."""
    nonblank = [ln for ln in body if ln.strip()]
    if not nonblank:
        return
    base = min(_indent(ln) for ln in nonblank)
    group_at: Optional[int] = None
    row_base = base
    awaiting_row = False
    cur: Optional[Tuple[int, str, List[str]]] = None
    for ln in body:
        if not ln.strip():
            continue
        ind, text = _indent(ln), ln.strip()
        if ind <= base + 1 and _is_group_header(text, body, ln):
            if cur is not None:
                yield cur
                cur = None
            group_at, awaiting_row = ind, True
            yield (ind, text, [_GROUP])
            continue
        if group_at is not None:
            if ind <= group_at:
                group_at, awaiting_row, row_base = None, False, base
            elif awaiting_row:
                row_base, awaiting_row = ind, False
        if ind <= row_base + 1 or cur is None:
            if cur is not None:
                yield cur
            cur = (ind, text, [])
        else:
            cur[2].append(text)
    if cur is not None:
        yield cur


# ------------------------------------------------------------------------- usage


def _extract_usage(lines: List[str]) -> str:
    for i, line in enumerate(lines):
        m = _USAGE_RE.match(line.strip()) if _indent(line) == 0 else None
        if not m:
            continue
        inline = m.group(1).strip()
        if inline:
            parts = [inline]
            for nxt in lines[i + 1 :]:
                if not nxt.strip():
                    break
                if _indent(nxt) == 0:
                    break
                parts.append(nxt.strip())
            return " ".join(parts)
        for nxt in lines[i + 1 :]:
            if nxt.strip():
                return nxt.strip()
        return ""
    return ""


def _scan_top_level(usage: str) -> List[Tuple[str, str]]:
    """Tokenise usage into ('word'|'angle'|'bracket'|'brace', text) at nesting depth 0."""
    out: List[Tuple[str, str]] = []
    i, n = 0, len(usage)
    while i < n:
        ch = usage[i]
        if ch.isspace():
            i += 1
        elif ch in "[({<":
            close = {"[": "]", "(": ")", "{": "}", "<": ">"}[ch]
            depth, j = 1, i + 1
            while j < n and depth:
                if usage[j] == ch:
                    depth += 1
                elif usage[j] == close:
                    depth -= 1
                j += 1
            inner = usage[i + 1 : j - 1]
            kind = {"[": "bracket", "(": "group", "{": "brace", "<": "angle"}[ch]
            out.append((kind, inner))
            i = j
        else:
            j = i
            while j < n and not usage[j].isspace() and usage[j] not in "[({<":
                j += 1
            out.append(("word", usage[i:j]))
            i = j
    return out


def parse_usage_positionals(usage: str, known_words: Iterable[str]) -> List[Positional]:
    known = {w.lower() for w in known_words}
    positionals: List[Positional] = []
    leading = True
    for kind, text in _scan_top_level(usage):
        if kind == "word":
            if text.startswith("-") or text in ("|", "--"):
                leading = leading and text.startswith("-") and not positionals
                continue
            if text.startswith("...") and positionals:
                positionals[-1].variadic = True
                continue
            if leading and (text.lower() in known or text.lower().rstrip(".py") in known):
                continue
            variadic = text.endswith("...")
            name = text.rstrip(".").strip()
            if not name or name.lower() in _NOISE_POSITIONALS:
                leading = False
                continue
            if name.upper() == name or name.islower() or re.match(r"^[\w-]+$", name):
                positionals.append(Positional(name, True, variadic))
            leading = False
        elif kind == "angle":
            leading = False
            variadic = text.endswith("...")
            name = text.rstrip(".").strip()
            if name and name.lower() not in _NOISE_POSITIONALS:
                positionals.append(Positional(name, True, variadic))
        elif kind == "bracket":
            leading = False
            inner = text.strip()
            if not inner or inner.startswith("-"):
                continue
            variadic = inner.endswith("...") or inner.endswith("... ")
            name = re.sub(r"\s*\.\.\.\s*$", "", inner).strip()
            first = name.split()[0] if name.split() else ""
            if first.lower() in _NOISE_POSITIONALS or not first:
                continue
            if "|" in name or "=" in name or name.startswith("-"):
                continue
            positionals.append(Positional(first.strip("<>"), False, variadic))
        else:
            leading = False
    return positionals


# ------------------------------------------------------------------------- main


def parse_help(text: str, known_words: Iterable[str] = ()) -> ParsedHelp:
    text = strip_ansi(text).replace("\r\n", "\n").replace("\r", "\n").expandtabs(8)
    lines = text.split("\n")
    result = ParsedHelp()
    result.usage = _extract_usage(lines)

    for line in lines:
        s_ = line.strip()
        if not s_ or _indent(line) > 0 or s_.startswith("-"):
            continue
        if _USAGE_RE.match(s_) or _HEADING_COLON.match(s_) or _HEADING_UPPER.match(s_):
            continue
        result.summary = s_
        break

    for kind, _title, body in _split_sections(lines):
        if kind in ("options", "inherited"):
            result.flags.extend(parse_flags(body, inherited=(kind == "inherited")))
        elif kind == "commands":
            for ref in parse_commands(body):
                if not any(r.name == ref.name for r in result.subcommands):
                    result.subcommands.append(ref)
        elif kind == "aliases":
            for ln in body:
                for chunk in ln.split(","):
                    words = chunk.split()
                    if words and _NAME_RE.match(words[-1]):
                        result.aliases.append(words[-1])
        elif kind == "positionals":
            refs = parse_commands(body) if _has_group(body) else []
            for ref in refs:
                if not any(r.name == ref.name for r in result.subcommands):
                    result.subcommands.append(ref)

    # argparse-style positional sections without {a,b} groups: real positionals
    sec_pos: List[Positional] = []
    for kind, _title, body in _split_sections(lines):
        if kind == "positionals" and not _has_group(body):
            for _ind, text_, _cont in _entries(body):
                spec, _desc = _split_spec_desc(text_)
                name = spec.strip().strip("<>[]").rstrip(".").strip()
                if name and re.match(r"^[\w.-]+$", name):
                    sec_pos.append(Positional(name, not spec.strip().startswith("["), spec.strip().endswith("...")))

    # fallback for subcommands listed only in the usage line: usage: prog {a,b,c} ...
    if not result.subcommands:
        for kind, inner in _scan_top_level(result.usage):
            if kind == "brace":
                for n in inner.split(","):
                    n = n.strip()
                    if n and _NAME_RE.match(n):
                        result.subcommands.append(SubRef(n))

    positionals = parse_usage_positionals(result.usage, known_words)
    sub_names = {r.name for r in result.subcommands}
    positionals = [p for p in positionals if p.name not in sub_names]
    result.positionals = positionals or sec_pos
    known = {w.lower() for w in known_words}
    result.aliases = [a for a in result.aliases if a.lower() not in known]

    # a flag never lists itself under another name
    result.flags = [f for f in result.flags if f.names]
    return result
