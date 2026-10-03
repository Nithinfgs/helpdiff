"""Text, Markdown and JSON renderers for diff and check results."""

from __future__ import annotations

import json
from collections import OrderedDict
from typing import Dict, List, Optional, Set, Tuple

from .check import Finding
from .diff import BREAKING, INFO, WARNING, Change
from .model import Snapshot

_STYLE = {
    "red": "\x1b[31m",
    "yellow": "\x1b[33m",
    "green": "\x1b[32m",
    "dim": "\x1b[2m",
    "bold": "\x1b[1m",
    "cyan": "\x1b[36m",
    "reset": "\x1b[0m",
}
_MARK = {BREAKING: ("✖", "red"), WARNING: ("▲", "yellow"), INFO: ("+", "green")}
_LABEL = {BREAKING: "breaking", WARNING: "warning", INFO: "added/other"}


class Painter:
    def __init__(self, color: bool) -> None:
        self.color = color

    def __call__(self, text: str, *styles: str) -> str:
        if not self.color:
            return text
        return "".join(_STYLE[s] for s in styles) + text + _STYLE["reset"]


def sections(changes: List[Change], name: str, min_shared: int = 3) -> List[Tuple[str, List[Change]]]:
    """Group changes by command; identical changes repeated across many commands (e.g. a shared
    "General Options" block) are collapsed into one section instead of repeating everywhere."""
    by_key: Dict[Tuple[str, str, str, str], List[Change]] = {}
    for c in changes:
        by_key.setdefault((c.severity, c.kind, c.subject, c.message), []).append(c)
    shared_ids: Set[int] = set()
    shared: "OrderedDict[Tuple[str, ...], List[Change]]" = OrderedDict()
    for items in by_key.values():
        if len(items) >= min_shared and items[0].kind.startswith(("flag", "choice", "default", "value")):
            cmds = tuple(sorted({i.command for i in items}))
            shared.setdefault(cmds, []).append(items[0])
            shared_ids.update(id(i) for i in items)
    out: List[Tuple[str, List[Change]]] = []
    for cmds, items in shared.items():
        shown = [(name + " " + c).strip() for c in cmds[:3]]
        more = " +%d more" % (len(cmds) - 3) if len(cmds) > 3 else ""
        out.append(("in %d commands (%s%s)" % (len(cmds), ", ".join(shown), more), items))
    grouped: "OrderedDict[str, List[Change]]" = OrderedDict()
    for c in changes:
        if id(c) not in shared_ids:
            grouped.setdefault(c.command, []).append(c)
    for cmd, items in grouped.items():
        out.append(((name + " " + cmd).strip(), items))
    return out


def counts(changes: List[Change]) -> Dict[str, int]:
    out = {BREAKING: 0, WARNING: 0, INFO: 0}
    for c in changes:
        out[c.severity] += 1
    return out


def _header(old: Snapshot, new: Snapshot) -> str:
    name = new.name or old.name

    def desc(s: Snapshot) -> str:
        v = s.tool_version or "unknown version"
        return v[len(name) + 1 :] if v.startswith(name + " ") else v

    return "%s  %s  →  %s" % (name, desc(old), desc(new))


def render_text(
    old: Snapshot,
    new: Snapshot,
    changes: List[Change],
    impact: Optional[List[Finding]] = None,
    show_info: bool = False,
    color: bool = False,
) -> str:
    p = Painter(color)
    lines: List[str] = [p(_header(old, new), "bold"), ""]
    shown = [c for c in changes if show_info or c.severity != INFO]
    for title, items in sections(shown, new.name or old.name):
        lines.append(p(title, "cyan", "bold"))
        for c in items:
            mark, col = _MARK[c.severity]
            row = "  %s %s" % (p(mark, col), c.message)
            if c.hint:
                row += p("  → " + c.hint, "dim")
            lines.append(row)
        lines.append("")
    n = counts(changes)
    hidden = n[INFO] if not show_info else 0
    summary = "%s, %s" % (
        p("%d breaking" % n[BREAKING], "red" if n[BREAKING] else "dim"),
        p("%d warnings" % n[WARNING], "yellow" if n[WARNING] else "dim"),
    )
    if hidden:
        summary += p(", %d additions hidden (--all to show)" % hidden, "dim")
    elif n[INFO]:
        summary += ", %d additions" % n[INFO]
    if not changes:
        lines.append(p("No differences in the command-line surface.", "green"))
    else:
        lines.append(summary)
    if impact is not None:
        lines.append("")
        if impact:
            lines.append(p("Your scripts would break at:", "bold", "red"))
            for f in impact:
                lines.append("  %s:%d  %s" % (f.file, f.line, p(f.message(), "red")))
                lines.append(p("      " + f.text[:160], "dim"))
        else:
            lines.append(p("None of the scanned scripts use anything that changed incompatibly.", "green"))
    return "\n".join(lines).rstrip() + "\n"


def render_markdown(
    old: Snapshot,
    new: Snapshot,
    changes: List[Change],
    impact: Optional[List[Finding]] = None,
    show_info: bool = False,
) -> str:
    n = counts(changes)
    name = new.name or old.name
    out = ["### CLI surface diff: `%s` (%s → %s)" % (name, old.tool_version or "?", new.tool_version or "?"), ""]
    if not changes:
        out.append("No differences in the command-line surface.")
    else:
        out.append("**%d breaking**, %d warnings, %d additions" % (n[BREAKING], n[WARNING], n[INFO]))
        out.append("")
        for sev, title in ((BREAKING, "Breaking"), (WARNING, "Warnings"), (INFO, "Additions")):
            items = [c for c in changes if c.severity == sev]
            if not items or (sev == INFO and not show_info):
                continue
            out.append("#### %s" % title)
            for title, group in sections(items, name):
                for c in group:
                    row = "- `%s`: %s" % (title, c.message)
                    if c.hint:
                        row += " _(%s)_" % c.hint
                    out.append(row)
            out.append("")
    if impact is not None:
        out.append("#### Affected scripts")
        if impact:
            for f in impact:
                out.append("- `%s:%d`: %s" % (f.file, f.line, f.message()))
        else:
            out.append("None of the scanned scripts use anything that changed incompatibly.")
    return "\n".join(out).rstrip() + "\n"


def render_json(changes: List[Change], impact: Optional[List[Finding]] = None) -> str:
    payload: Dict[str, object] = {
        "summary": counts(changes),
        "changes": [c.to_dict() for c in changes],
    }
    if impact is not None:
        payload["affected_scripts"] = [
            {
                "file": f.file,
                "line": f.line,
                "kind": f.kind,
                "token": f.token,
                "command": f.command,
                "message": f.message(),
                "text": f.text,
            }
            for f in impact
        ]
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def render_findings(findings: List[Finding], color: bool = False) -> str:
    p = Painter(color)
    if not findings:
        return p("No unknown flags or subcommands found in the scanned scripts.", "green") + "\n"
    lines = []
    for f in findings:
        lines.append("%s:%d  %s" % (f.file, f.line, p(f.message(), "red")))
        lines.append(p("    " + f.text[:160], "dim"))
    lines.append("")
    lines.append(p("%d problem(s) found" % len(findings), "bold"))
    return "\n".join(lines) + "\n"


def render_tree(snap: Snapshot) -> str:
    lines = ["%s %s" % (snap.name, snap.tool_version or ""), ""]
    for key in sorted(snap.commands):
        cmd = snap.commands[key]
        label = key or "(root)"
        lines.append(
            "%s  [%d flags%s]" % (label, len(cmd.flags), ", %d args" % len(cmd.positionals) if cmd.positionals else "")
        )
    return "\n".join(lines) + "\n"
