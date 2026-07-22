from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from mythings import github
from mythings.github import GitHubError, Runner

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
    # Optional, machine-checkable JSON-Schema fragments for the tool's CLI
    # args / ledger data payload -- null until authored for a given tool.
    input_schema: dict | None = None
    output_schema: dict | None = None


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
            input_schema=obj.get("input_schema"),
            output_schema=obj.get("output_schema"),
        )
        for obj in raw
    ]


def _core_has(attr: str) -> bool:
    return hasattr(github.GitHub, attr)


def always_healthy(_name: str) -> bool:
    return True


def _critical_issue_repos(runner: Runner, org: str) -> frozenset[str] | None:
    # None means the search itself failed -- the caller must fail closed, not
    # assume every dependency is healthy just because the signal was unreadable.
    try:
        raw = runner(
            [
                "search", "issues", "--owner", org, "--state", "open",
                "--label", "critical", "--json", "repository",
            ]
        )
    except GitHubError:
        return None
    try:
        return frozenset(
            obj["repository"]["nameWithOwner"].rsplit("/", 1)[-1] for obj in json.loads(raw)
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def critical_issue_health_check(runner: Runner, org: str) -> Callable[[str], bool]:
    # Reuses the fleet's one existing critical-halt signal (CONVENTIONS.md
    # "Filing bugs": an open `critical`-labelled issue anywhere halts new
    # dispatch org-wide) as a per-dependency readiness check, rather than
    # inventing a second kind of health probe. One org-wide search, made
    # lazily on first use and cached for the life of this closure (meant to
    # be constructed once per orchestrator run), so checking N dependencies
    # costs at most one `gh` call, not N.
    state: dict[str, frozenset[str] | None] = {}

    def check(name: str) -> bool:
        if "repos" not in state:
            state["repos"] = _critical_issue_repos(runner, org)
        repos = state["repos"]
        if repos is None:
            # The search itself failed -- fail closed (unready), matching the
            # fleet's general ASK/DENY-over-silent-proceed bias rather than
            # assuming every dependency is fine.
            return False
        return name not in repos

    return check


def is_ready(
    tool: ProposedTool,
    *,
    built_repos: set[str],
    core_has: Callable[[str], bool] = _core_has,
    dep_is_healthy: Callable[[str], bool] = always_healthy,
) -> bool:
    for dep in tool.depends_on:
        prefix, _, name = dep.partition(":")
        if prefix == "tool":
            if name not in built_repos or not dep_is_healthy(name):
                return False
        elif prefix == "core":
            if not core_has(name):
                return False
        else:
            raise ValueError(f"unknown dependency form {dep!r} (expected 'tool:...' or 'core:...')")
    return True
