from __future__ import annotations

import json
from pathlib import Path

from mythings._devledger import read_all
from mythings.github import Runner
from mythings.ledger import LedgerEntry

from myorchestrator.candidates import Candidate
from myorchestrator.manifest import ProposedTool, is_ready

# All fleet reads go through the same `gh` boundary the core `github` contract uses
# (an injected Runner), so tests mock only that process. Core `github.GitHub` has no
# repo-listing or created-at exposure yet (both sit in the pending core-additions
# batch); rather than add to the shared contract mid-tool, we query the runner
# directly here in the same thin-wrapper style.


def list_repos(runner: Runner, org: str, *, limit: int = 1000) -> list[str]:
    raw = json.loads(runner(["repo", "list", org, "--json", "name", "--limit", str(limit)]))
    return [obj["name"] for obj in raw]


def _backlog_label(repo: str) -> str:
    # Convention: a tool's backlog label is its repo name (see my-guard's CLAUDE.md).
    return repo


def issue_candidates(
    runner: Runner,
    org: str,
    repos: list[str],
    urgency: dict[str, int],
) -> list[Candidate]:
    out: list[Candidate] = []
    for repo in repos:
        label = _backlog_label(repo)
        argv = [
            "issue",
            "list",
            "--repo",
            f"{org}/{repo}",
            "--state",
            "open",
            "--label",
            label,
            "--limit",
            "100",
            "--json",
            "number,title,createdAt",
        ]
        for obj in json.loads(runner(argv)):
            out.append(
                Candidate(
                    id=f"{repo}#{obj['number']}",
                    repo=repo,
                    tool=repo,
                    title=obj.get("title", ""),
                    kind="issue",
                    created_at=obj["createdAt"],
                    urgency=urgency.get(repo, 0),
                )
            )
    return out


def scaffold_candidates(manifest: list[ProposedTool], built_repos: set[str]) -> list[Candidate]:
    # A proposal with no repo yet becomes a "scaffold this tool" candidate, kept only
    # if every dependency in its manifest entry is already satisfied (readiness).
    out: list[Candidate] = []
    for tool in manifest:
        if tool.repo in built_repos:
            continue
        if not is_ready(tool, built_repos=built_repos):
            continue
        out.append(
            Candidate(
                id=f"scaffold:{tool.repo}",
                repo=tool.repo,
                tool=tool.tool,
                title=tool.title,
                kind="scaffold",
                created_at=tool.added,
                urgency=0,
            )
        )
    return out


_DRIFT_OPEN = ("drift", "drift_found")
_DRIFT_RESOLVED = "drift_resolved"
_ASK_OPEN = ("awaiting", "pending", "")
_ASK_RESOLVED = ("answered", "replied")


def urgency_from_entries(entries: list[LedgerEntry]) -> int:
    # entries arrive oldest-first; an open signal counts until a later entry resolves it.
    open_drift = 0
    open_ask = 0
    for e in entries:
        if e.kind == "drift":
            if e.outcome in _DRIFT_OPEN:
                open_drift += 1
            elif e.outcome == _DRIFT_RESOLVED:
                open_drift = max(0, open_drift - 1)
        elif e.kind == "ask":
            if e.outcome in _ASK_OPEN:
                open_ask += 1
            elif e.outcome in _ASK_RESOLVED:
                open_ask = max(0, open_ask - 1)
    return open_drift + open_ask


def scan_urgency(repo_root: str | Path, repos: list[str]) -> dict[str, int]:
    # Reads each repo's dev-ledger/ (the only per-repo ledger the convention defines
    # today; the runtime-ledger location is an unresolved upstream question). Drift/ask
    # signals there boost every candidate in that repo.
    root = Path(repo_root)
    out: dict[str, int] = {}
    for repo in repos:
        repo_path = root / repo
        if not (repo_path / "dev-ledger").is_dir():
            continue
        score = urgency_from_entries(read_all(root=repo_path))
        if score:
            out[repo] = score
    return out
