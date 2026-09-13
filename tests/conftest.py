from __future__ import annotations

import json
from pathlib import Path

import pytest
from mythings.ledger import Ledger, LedgerEntry

# Shared fakes come from mythings.testing; the repo-list/issue-list gh double
# and the manifest/repo-root builders stay local.
from mythings.testing import FakeGh, ScriptedEngine
from mythings.testing import clean_git_env as _shared_clean_git_env  # noqa: F401

__all__ = ["ScriptedEngine"]


@pytest.fixture(autouse=True)
def _clean_git_env(request: pytest.FixtureRequest) -> None:
    # Real git worktrees in tests/test_plans.py; hook-launched pytest
    # (pre-commit) must not leak GIT_* into them.
    request.getfixturevalue("_shared_clean_git_env")


def fake_gh(repos: list[str], issues: dict[str, list[dict]]) -> FakeGh:
    def issue_list(argv: list[str]) -> str:
        repo_arg = argv[argv.index("--repo") + 1]
        repo = repo_arg.split("/", 1)[1] if "/" in repo_arg else repo_arg
        return json.dumps(issues.get(repo, []))

    def issue_view(argv: list[str]) -> str:
        repo_arg = argv[argv.index("--repo") + 1]
        repo = repo_arg.split("/", 1)[1] if "/" in repo_arg else repo_arg
        number = int(argv[argv.index("view") + 1])
        for obj in issues.get(repo, []):
            if obj.get("number") == number:
                state = obj.get("state", "OPEN")
                return json.dumps({"state": state})
        return json.dumps({"state": "CLOSED"})

    return FakeGh(
        {
            ("repo", "list"): json.dumps([{"name": r} for r in repos]),
            ("issue", "list"): issue_list,
            ("issue", "view"): issue_view,
            ("issue", "edit"): "",
        }
    )


# A plain, dispatchable CAD label set. Tests about ranking or the hard filters
# pass their own; the rest just need an issue that survives triage.
DEFAULT_LABELS = ("lane:product", "prio:P2", "size:S")


def issue(
    number: int, title: str, created_at: str, labels: tuple[str, ...] = DEFAULT_LABELS
) -> dict:
    return {
        "number": number,
        "title": title,
        "createdAt": created_at,
        "labels": [{"name": name} for name in labels],
    }


def make_repo_root(tmp_path: Path, repos: list[str], signals: dict[str, list[LedgerEntry]]) -> Path:
    root = tmp_path / "workspace"
    for repo in repos:
        dev = root / repo / "dev-ledger"
        dev.mkdir(parents=True)
        ledger = Ledger(dev / "session.jsonl")
        for entry in signals.get(repo, []):
            ledger.append(entry)
    return root


def mentry(tool: str, repo: str, added: str, depends_on: list[str] | None = None) -> dict:
    return {
        "tool": tool,
        "repo": repo,
        "title": "x",
        "added": added,
        "depends_on": depends_on or [],
    }


def write_manifest(tmp_path: Path, entries: list[dict]) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path
