from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from mythings import github

# The machine-readable dependency graph among not-yet-built tools. JSON, not YAML,
# because the runtime must stay dependency-free (stdlib json vs. a third-party YAML
# parser). Canonically this belongs beside the design docs in
# my-things-core/docs/tools/manifest.json; a copy ships here as package data so the
# tool is usable before that shared-repo addition lands.


@dataclass(frozen=True)
class ProposedTool:
    tool: str  # design name, e.g. "MyTester"
    repo: str  # target repo / collision key, e.g. "my-tester"
    title: str
    added: str  # ISO date the proposal was recorded; the oldest-first key
    depends_on: list[str]  # "tool:<repo>" (built) or "core:<attr>" (landed on github.GitHub)


def default_manifest_path() -> Path:
    return Path(str(files("myorchestrator").joinpath("manifest.json")))


def load_manifest(path: str | Path) -> list[ProposedTool]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        ProposedTool(
            tool=obj["tool"],
            repo=obj["repo"],
            title=obj.get("title", ""),
            added=obj["added"],
            depends_on=list(obj.get("depends_on", [])),
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
