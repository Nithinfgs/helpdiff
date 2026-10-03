"""Data model for a CLI's command/flag surface, plus snapshot (de)serialisation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

SNAPSHOT_FORMAT = 1


@dataclass
class Flag:
    names: List[str]
    value: Optional[str] = None  # metavar / type word; None for a plain switch
    choices: List[str] = field(default_factory=list)
    default: Optional[str] = None
    required: bool = False
    deprecated: bool = False
    inherited: bool = False
    description: str = ""

    @property
    def long_names(self) -> List[str]:
        return [n for n in self.names if n.startswith("--")]

    @property
    def short_names(self) -> List[str]:
        return [n for n in self.names if not n.startswith("--")]

    @property
    def takes_value(self) -> bool:
        return self.value is not None

    @property
    def label(self) -> str:
        return self.long_names[0] if self.long_names else self.names[0]

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"names": self.names}
        if self.value is not None:
            out["value"] = self.value
        if self.choices:
            out["choices"] = self.choices
        if self.default is not None:
            out["default"] = self.default
        if self.required:
            out["required"] = True
        if self.deprecated:
            out["deprecated"] = True
        if self.inherited:
            out["inherited"] = True
        if self.description:
            out["description"] = self.description
        return out

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Flag":
        return cls(
            names=list(d["names"]),
            value=d.get("value"),
            choices=list(d.get("choices", [])),
            default=d.get("default"),
            required=bool(d.get("required", False)),
            deprecated=bool(d.get("deprecated", False)),
            inherited=bool(d.get("inherited", False)),
            description=d.get("description", ""),
        )


@dataclass
class Positional:
    name: str
    required: bool = True
    variadic: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "required": self.required, "variadic": self.variadic}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Positional":
        return cls(d["name"], bool(d.get("required", True)), bool(d.get("variadic", False)))


@dataclass
class SubRef:
    """A subcommand as listed by its parent's help."""

    name: str
    aliases: List[str] = field(default_factory=list)
    summary: str = ""


@dataclass
class ParsedHelp:
    summary: str = ""
    usage: str = ""
    aliases: List[str] = field(default_factory=list)
    flags: List[Flag] = field(default_factory=list)
    positionals: List[Positional] = field(default_factory=list)
    subcommands: List[SubRef] = field(default_factory=list)


@dataclass
class Command:
    path: List[str]
    summary: str = ""
    usage: str = ""
    aliases: List[str] = field(default_factory=list)
    flags: List[Flag] = field(default_factory=list)
    positionals: List[Positional] = field(default_factory=list)
    subcommands: List[str] = field(default_factory=list)
    crawled: bool = True

    @property
    def key(self) -> str:
        return " ".join(self.path)

    def find_flag(self, name: str) -> Optional[Flag]:
        for f in self.flags:
            if name in f.names:
                return f
        return None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if self.summary:
            out["summary"] = self.summary
        if self.usage:
            out["usage"] = self.usage
        if self.aliases:
            out["aliases"] = self.aliases
        if self.subcommands:
            out["subcommands"] = self.subcommands
        if self.positionals:
            out["positionals"] = [p.to_dict() for p in self.positionals]
        if self.flags:
            out["flags"] = [f.to_dict() for f in self.flags]
        if not self.crawled:
            out["crawled"] = False
        return out

    @classmethod
    def from_dict(cls, key: str, d: Dict[str, Any]) -> "Command":
        return cls(
            path=key.split(" ") if key else [],
            summary=d.get("summary", ""),
            usage=d.get("usage", ""),
            aliases=list(d.get("aliases", [])),
            flags=[Flag.from_dict(f) for f in d.get("flags", [])],
            positionals=[Positional.from_dict(p) for p in d.get("positionals", [])],
            subcommands=list(d.get("subcommands", [])),
            crawled=bool(d.get("crawled", True)),
        )


@dataclass
class Snapshot:
    name: str
    command: List[str]
    tool_version: Optional[str] = None
    commands: Dict[str, Command] = field(default_factory=dict)

    @property
    def root(self) -> Command:
        return self.commands[""]

    def child(self, parent: Command, word: str) -> Optional[Command]:
        for sub in parent.subcommands:
            key = " ".join(parent.path + [sub])
            cmd = self.commands.get(key)
            if sub == word or (cmd is not None and word in cmd.aliases):
                return cmd
        return None

    def to_json(self) -> str:
        from . import __version__

        payload = {
            "format": SNAPSHOT_FORMAT,
            "helpdiff": __version__,
            "name": self.name,
            "command": self.command,
            "tool_version": self.tool_version,
            "commands": {k: self.commands[k].to_dict() for k in sorted(self.commands)},
        }
        return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "Snapshot":
        data = json.loads(text)
        if not isinstance(data, dict) or "commands" not in data:
            raise ValueError("not a helpdiff snapshot (missing 'commands')")
        if data.get("format") != SNAPSHOT_FORMAT:
            raise ValueError(
                "unsupported snapshot format %r (this helpdiff reads format %d)" % (data.get("format"), SNAPSHOT_FORMAT)
            )
        snap = cls(
            name=data.get("name", ""),
            command=list(data.get("command", [])),
            tool_version=data.get("tool_version"),
        )
        for key, d in data["commands"].items():
            snap.commands[key] = Command.from_dict(key, d)
        if "" not in snap.commands:
            raise ValueError("snapshot has no root command")
        return snap
