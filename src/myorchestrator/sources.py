from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from mythings._devledger import read_all
from mythings.github import Runner
from mythings.ledger import Ledger, LedgerEntry

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


def scaffold_candidates(
    manifest: list[ProposedTool],
    built_repos: set[str],
    urgency: dict[str, int] | None = None,
    *,
    penalty: int = 0,
) -> list[Candidate]:
    # A proposal with no repo yet becomes a "scaffold this tool" candidate, kept only
    # if every dependency in its manifest entry is already satisfied (readiness).
    # `urgency` lets a MyPlanner "build X next" boost surface a specific scaffold;
    # `penalty` lets a "pause new tools" flag push every scaffold down at once.
    urgency = urgency or {}
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
                urgency=urgency.get(tool.repo, 0) - penalty,
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


# MyPlanner feeds its plan back as one more ranking signal, the same role the
# drift/ask urgency boosts play: a "next" item raises its repo, "soon" nudges it,
# and a "pause new tools" flag penalizes every scaffold at once.
_HORIZON_BOOST = {"next": 3, "soon": 1, "later": 0}
_PAUSE_MARKERS = ("pause new tool", "freeze new tool", "no new tool", "hold new tool")
_SCAFFOLD_PENALTY = 100  # enough to drop any paused scaffold below every live issue


@dataclass(frozen=True)
class PlanSignal:
    boosts: dict[str, int] = field(default_factory=dict)  # repo -> ranking boost
    scaffold_penalty: int = 0  # subtracted from every scaffold candidate


def plan_signal_from_entry(entry: LedgerEntry, repos: list[str]) -> PlanSignal:
    boosts: dict[str, int] = {}
    for item in entry.data.get("plan") or []:
        repo = _match_repo(str(item.get("item", "")), repos)
        if repo is None:
            continue
        boosts[repo] = boosts.get(repo, 0) + _HORIZON_BOOST.get(item.get("horizon", ""), 0)
    penalty = _SCAFFOLD_PENALTY if _has_pause_flag(entry.data.get("flags") or []) else 0
    return PlanSignal(boosts={r: b for r, b in boosts.items() if b}, scaffold_penalty=penalty)


def read_plan_signal(plan_ledger: str | Path, repos: list[str]) -> PlanSignal:
    # Reads MyPlanner's own runtime ledger (its kind=plan entries live there, not in
    # any repo's dev-ledger). Missing ledger => no signal, so this stays a soft,
    # optional input: MyOrchestrator works exactly as before when MyPlanner hasn't run.
    path = Path(plan_ledger)
    if not path.exists():
        return PlanSignal()
    entries = [e for e in Ledger(path) if e.kind == "plan"]
    if not entries:
        return PlanSignal()
    return plan_signal_from_entry(entries[-1], repos)


def _match_repo(text: str, repos: list[str]) -> str | None:
    low = text.lower()
    # Longest repo name first, so "my-drift-watcher" wins over a substring "my".
    for repo in sorted(repos, key=len, reverse=True):
        if repo.lower() in low:
            return repo
    return None


def _has_pause_flag(flags: list) -> bool:
    return any(
        isinstance(f, str) and any(m in f.lower() for m in _PAUSE_MARKERS) for f in flags
    )
