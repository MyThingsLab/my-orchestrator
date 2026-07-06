from __future__ import annotations

import json
from pathlib import Path

from mythings.engine import EngineRequest, EngineResult
from mythings.ledger import Ledger, LedgerEntry


class FakeRunner:
    # Mocks the `gh` process boundary: argv is everything after `gh`.
    def __init__(self, repos: list[str], issues: dict[str, list[dict]]) -> None:
        self.repos = repos
        self.issues = issues
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> str:
        self.calls.append(argv)
        if argv[:2] == ["repo", "list"]:
            return json.dumps([{"name": r} for r in self.repos])
        if argv[:2] == ["issue", "list"]:
            repo = argv[argv.index("--repo") + 1].split("/", 1)[1]
            return json.dumps(self.issues.get(repo, []))
        if argv[:2] == ["issue", "edit"]:
            return ""
        raise AssertionError(f"unexpected gh call: {argv}")


class SpyEngine:
    def __init__(self, result: EngineResult | None = None) -> None:
        self.calls: list[EngineRequest] = []
        self.result = result or EngineResult(text="", data={})

    def run(self, request: EngineRequest) -> EngineResult:
        self.calls.append(request)
        return self.result


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
