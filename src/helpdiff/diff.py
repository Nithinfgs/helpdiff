"""Compare two snapshots and classify what changed for people who script the tool."""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Callable, Dict, List, Optional, Tuple

from .model import Command, Flag, Snapshot

BREAKING = "breaking"
WARNING = "warning"
INFO = "info"
SEVERITY_ORDER = {BREAKING: 0, WARNING: 1, INFO: 2}

PRIMITIVES = {"int", "integer", "number", "float", "duration", "bool", "boolean", "string", "str", "path", "file"}


@dataclass
class Change:
    severity: str
    kind: str
    command: str  # space-joined path, "" for root
    subject: str
    message: str
    hint: Optional[str] = None
    extra: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        out: Dict[str, object] = {
            "severity": self.severity,
            "kind": self.kind,
            "command": self.command,
            "subject": self.subject,
            "message": self.message,
        }
        if self.hint:
            out["hint"] = self.hint
        if self.extra:
            out["extra"] = self.extra
        return out


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _match_flags(old: List[Flag], new: List[Flag]) -> Tuple[List[Tuple[Flag, Flag]], List[Flag], List[Flag]]:
    """Pair flags that are the same option, tolerating added/removed aliases."""
    pairs: List[Tuple[Flag, Flag]] = []
    left_old = list(old)
    left_new = list(new)
    getters: List[Callable[[Flag], List[str]]] = [lambda f: f.long_names, lambda f: f.short_names]
    for getter in getters:
        for o in list(left_old):
            hit = None
            for n in left_new:
                if set(getter(o)) & set(getter(n)):
                    hit = n
                    break
            if hit is not None:
                shares_long = bool(set(o.long_names) & set(hit.long_names))
                if not shares_long and o.long_names and hit.long_names and _sim(o.description, hit.description) < 0.5:
                    continue  # same short letter reused for an unrelated flag
                pairs.append((o, hit))
                left_old.remove(o)
                left_new.remove(hit)
    return pairs, left_old, left_new


def _flag_changes(cmd_key: str, o: Flag, n: Flag, out: List[Change]) -> None:
    label = n.label
    removed = [x for x in o.names if x not in n.names]
    for r in removed:
        out.append(
            Change(
                BREAKING,
                "flag-alias-removed",
                cmd_key,
                r,
                "%s no longer accepted (still available as %s)" % (r, " / ".join(n.names)),
            )
        )
    added = [x for x in n.names if x not in o.names]
    if added:
        out.append(Change(INFO, "flag-alias-added", cmd_key, label, "new alias %s" % ", ".join(added)))
    if not o.takes_value and n.takes_value:
        out.append(
            Change(
                BREAKING,
                "flag-now-takes-value",
                cmd_key,
                label,
                "%s now takes a value (%s) and will swallow the next argument" % (label, n.value),
            )
        )
    elif o.takes_value and not n.takes_value:
        out.append(
            Change(
                BREAKING,
                "flag-no-longer-takes-value",
                cmd_key,
                label,
                "%s no longer takes a value; the old value will be read as a positional argument" % label,
            )
        )
    if o.choices and n.choices:
        gone = [c for c in o.choices if c not in n.choices]
        new = [c for c in n.choices if c not in o.choices]
        if gone:
            out.append(
                Change(
                    BREAKING, "choice-removed", cmd_key, label, "%s no longer accepts: %s" % (label, ", ".join(gone))
                )
            )
        if new:
            out.append(
                Change(INFO, "choice-added", cmd_key, label, "%s now also accepts: %s" % (label, ", ".join(new)))
            )
    elif not o.choices and n.choices and o.takes_value:
        out.append(
            Change(
                WARNING,
                "choices-restricted",
                cmd_key,
                label,
                "%s is now restricted to: %s" % (label, ", ".join(n.choices)),
            )
        )
    if o.default != n.default and (o.default is not None or n.default is not None):
        out.append(
            Change(
                WARNING,
                "default-changed",
                cmd_key,
                label,
                "default of %s changed: %s -> %s" % (label, o.default or "(none)", n.default or "(none)"),
            )
        )
    if o.takes_value and n.takes_value and o.value and n.value:
        ov, nv = o.value.lower(), n.value.lower()
        if ov != nv and ov in PRIMITIVES and nv in PRIMITIVES and {ov, nv} != {"string", "str"}:
            out.append(
                Change(
                    WARNING,
                    "value-type-changed",
                    cmd_key,
                    label,
                    "value type of %s changed: %s -> %s" % (label, o.value, n.value),
                )
            )
    if not o.required and n.required:
        out.append(Change(BREAKING, "flag-now-required", cmd_key, label, "%s is now required" % label))
    if not o.deprecated and n.deprecated:
        out.append(Change(WARNING, "flag-deprecated", cmd_key, label, "%s is now marked deprecated" % label))


def _rename_hint(removed: Flag, candidates: List[Flag]) -> Optional[Flag]:
    best, best_score = None, 0.0
    for c in candidates:
        if removed.takes_value != c.takes_value:
            continue
        name_score = max(_sim(a.lstrip("-"), b.lstrip("-")) for a in removed.names for b in c.names)
        desc_score = _sim(removed.description, c.description) if removed.description and c.description else 0.0
        score = max(name_score, desc_score * 0.9)
        if score > best_score:
            best, best_score = c, score
    return best if best_score >= 0.6 else None


def _compare_command(key: str, o: Command, n: Command, out: List[Change]) -> None:
    old_flags = [f for f in o.flags if not f.inherited]
    new_flags = [f for f in n.flags if not f.inherited]
    pairs, gone, fresh = _match_flags(old_flags, new_flags)
    for of, nf in pairs:
        _flag_changes(key, of, nf, out)
    for f in gone:
        target = _rename_hint(f, fresh)
        hint = None
        if target is not None:
            hint = "looks renamed to %s" % target.label
            fresh = [x for x in fresh if x is not target]
        out.append(Change(BREAKING, "flag-removed", key, f.label, "%s was removed" % " / ".join(f.names), hint))
    for f in fresh:
        if f.required:
            out.append(Change(BREAKING, "flag-added-required", key, f.label, "new required flag %s" % f.label))
        else:
            out.append(Change(INFO, "flag-added", key, f.label, "new flag %s" % " / ".join(f.names)))

    # positionals, compared by position
    for i in range(max(len(o.positionals), len(n.positionals))):
        op = o.positionals[i] if i < len(o.positionals) else None
        np_ = n.positionals[i] if i < len(n.positionals) else None
        if op is None and np_ is not None:
            sev = BREAKING if np_.required else INFO
            out.append(
                Change(
                    sev,
                    "positional-added",
                    key,
                    np_.name,
                    "new %s argument <%s>" % ("required" if np_.required else "optional", np_.name),
                )
            )
        elif np_ is None and op is not None:
            out.append(Change(WARNING, "positional-removed", key, op.name, "argument <%s> was removed" % op.name))
        elif op is not None and np_ is not None:
            if not op.required and np_.required:
                out.append(
                    Change(
                        BREAKING, "positional-now-required", key, np_.name, "argument <%s> is now required" % np_.name
                    )
                )

    for alias in o.aliases:
        if alias not in n.aliases:
            out.append(
                Change(BREAKING, "command-alias-removed", key, alias, "alias `%s` of `%s` was removed" % (alias, key))
            )
    for alias in n.aliases:
        if alias not in o.aliases:
            out.append(Change(INFO, "command-alias-added", key, alias, "new alias `%s`" % alias))


def _command_hint(missing: Command, new: Snapshot) -> Optional[str]:
    """Find where a removed command may have gone: same leaf name elsewhere."""
    leaf = missing.path[-1]
    for key, cmd in sorted(new.commands.items()):
        if cmd.path and cmd.path[-1] == leaf and key != missing.key:
            return "a command named `%s` now exists at `%s`" % (leaf, key)
    best, best_score = None, 0.0
    for key, cmd in new.commands.items():
        if cmd.path and len(cmd.path) == len(missing.path) and cmd.path[:-1] == missing.path[:-1]:
            s = _sim(leaf, cmd.path[-1])
            if s > best_score:
                best, best_score = key, s
    if best and best_score >= 0.75:
        return "similar command now exists: `%s`" % best
    return None


def diff_snapshots(old: Snapshot, new: Snapshot) -> List[Change]:
    out: List[Change] = []
    for key in sorted(old.commands):
        o = old.commands[key]
        n = new.commands.get(key)
        if n is None:
            # not found by name; maybe it is now only reachable as an alias of a sibling
            parent_key = " ".join(o.path[:-1])
            parent = new.commands.get(parent_key)
            via_alias = None
            if parent is not None:
                via_alias = new.child(parent, o.path[-1])
            if via_alias is not None:
                out.append(
                    Change(
                        INFO,
                        "command-became-alias",
                        key,
                        o.path[-1],
                        "`%s` is now an alias of `%s`" % (key, via_alias.key),
                    )
                )
                continue
            # skip cascading reports for children of an already-removed command
            if any(c.kind == "command-removed" and key.startswith(c.command + " ") for c in out):
                continue
            out.append(
                Change(
                    BREAKING,
                    "command-removed",
                    key,
                    o.path[-1],
                    "command `%s` was removed" % key,
                    _command_hint(o, new),
                )
            )
            continue
        if not o.crawled or not n.crawled:
            continue
        _compare_command(key, o, n, out)
    for key in sorted(new.commands):
        if key not in old.commands:
            if any(c.kind == "command-added" and key.startswith(c.command + " ") for c in out):
                continue
            out.append(Change(INFO, "command-added", key, new.commands[key].path[-1], "new command `%s`" % key))
    out.sort(key=lambda c: (SEVERITY_ORDER[c.severity], c.command, c.subject, c.kind))
    return out


def worst_severity(changes: List[Change]) -> Optional[str]:
    if not changes:
        return None
    return min((c.severity for c in changes), key=lambda s: SEVERITY_ORDER[s])
