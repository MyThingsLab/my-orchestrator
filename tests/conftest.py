from __future__ import annotations

import json
from pathlib import Path

from mythings.ledger import Ledger, LedgerEntry

# Shared fakes come from mythings.testing; the repo-list/issue-list gh double
# and the manifest/repo-root builders stay local.
from mythings.testing import FakeGh, ScriptedEngine

__all__ = ["ScriptedEngine"]


def fake_gh(repos: list[str], issues: dict[str, list[dict]]) -> FakeGh:
    def issue_list(argv: list[str]) -> str:
        repo = argv[argv.index("--repo") + 1].split("/", 1)[1]
        return json.dumps(issues.get(repo, []))

    return FakeGh(
        {
            ("repo", "list"): json.dumps([{"name": r} for r in repos]),
            ("issue", "list"): issue_list,
            ("issue", "edit"): "",
        }
    )


def issue(number: int, title: str, created_at: str) -> dict:
    return {"number": number, "title": title, "createdAt": created_at}


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
