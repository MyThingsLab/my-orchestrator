from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from mythings import github

# The machine-readable fleet registry. Canonical copy is my-things-core's
# tools_manifest.json, shipped as `mythings` package data (like harness.md) —
# reading it from the installed core means this tool can never fall behind the
# fleet the way its own vendored 16-entry copy did. Still stdlib json: the
# runtime stays dependency-free.


@dataclass(frozen=True)
class ProposedTool:
    tool: str  # design name, e.g. "MyTester"
    repo: str  # target repo / collision key, e.g. "my-tester"
    title: str
    added: str  # ISO date the proposal was recorded; the oldest-first key
    depends_on: list[str]  # "tool:<repo>" (built) or "core:<attr>" (landed on github.GitHub)
    status: str = "designed"  # "designed" | "building" | "shipped"


def default_manifest_path() -> Path:
    return Path(str(files("mythings").joinpath("tools_manifest.json")))


def load_manifest(path: str | Path) -> list[ProposedTool]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        ProposedTool(
            tool=obj["tool"],
            repo=obj["repo"],
            title=obj.get("title", ""),
            added=obj["added"],
            depends_on=list(obj.get("depends_on", [])),
            status=obj.get("status", "designed"),
        )
        for obj in raw
    ]


def _core_has(attr: str) -> bool:
    return hasattr(github.GitHub, attr)


def is_ready(
    tool: ProposedTool,
    *,
    built_repos: set[str],
    core_has: Callable[[str], bool] = _core_has,
) -> bool:
    for dep in tool.depends_on:
        prefix, _, name = dep.partition(":")
        if prefix == "tool":
            if name not in built_repos:
                return False
        elif prefix == "core":
            if not core_has(name):
                return False
        else:
            raise ValueError(f"unknown dependency form {dep!r} (expected 'tool:...' or 'core:...')")
    return True
