from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from mythings._devledger import read_all
from mythings.github import Runner
from mythings.ledger import Ledger, LedgerEntry

from myorchestrator.candidates import Candidate
from myorchestrator.manifest import ProposedTool, always_healthy, is_ready

# All fleet reads go through the same `gh` boundary the core `github` contract uses
# (an injected Runner), so tests mock only that process. Core `github.GitHub` has no
# repo-listing or created-at exposure yet (both sit in the pending core-additions
# batch); rather than add to the shared contract mid-tool, we query the runner
# directly here in the same thin-wrapper style.


def list_repos(runner: Runner, org: str, *, limit: int = 1000) -> list[str]:
    raw = json.loads(runner(["repo", "list", org, "--json", "name", "--limit", str(limit)]))
    return [obj["name"] for obj in raw]


def issue_candidates(
    runner: Runner,
    org: str,
    repos: list[str],
    urgency: dict[str, int],
    blocked: dict[str, frozenset[int]] | None = None,
    candidate_urgency: dict[str, int] | None = None,
) -> list[Candidate]:
    # Every open issue is a candidate; `triage()` decides which are dispatchable.
    # This used to pre-filter on a per-repo backlog label named after the repo,
    # which silently became a second, older gate once the CAD labels arrived --
    # and the two disagreed almost everywhere. Nothing re-applied the backlog
    # label, so at the time this changed it was carried by 73 of 222 open
    # issues, and by *none* of the triaged P0s: every `lane:`/`prio:`-labelled
    # issue in my-fleet, my-coder and my-things-core was invisible here, while
    # 60 untriaged rough ideas in my-idea were the bulk of what did get through.
    # One gate, in `mythings.labels.validate()`, which requires lane and size --
    # so an untriaged issue is still kept out, but as a reported exclusion
    # rather than by never being fetched.
    blocked = blocked or {}
    candidate_urgency = candidate_urgency or {}
    out: list[Candidate] = []
    for repo in repos:
        argv = [
            "issue",
            "list",
            "--repo",
            f"{org}/{repo}",
            "--state",
            "open",
            "--limit",
            "100",
            "--json",
            "number,title,createdAt,labels",
        ]
        repo_blocked = blocked.get(repo, frozenset())
        for obj in json.loads(runner(argv)):
            if obj["number"] in repo_blocked:
                # plans/*.md says this issue depends on an unfinished task --
                # not a real candidate no matter how old it is (my-orchestrator#19).
                continue
            cand_id = f"{repo}#{obj['number']}"
            extra = candidate_urgency.get(cand_id, 0) or candidate_urgency.get(
                f"{org}/{cand_id}", 0
            )
            out.append(
                Candidate(
                    id=cand_id,
                    repo=repo,
                    tool=repo,
                    title=obj.get("title", ""),
                    kind="issue",
                    created_at=obj["createdAt"],
                    urgency=urgency.get(repo, 0) + extra,
                    number=obj["number"],
                    labels=_label_names(obj.get("labels")),
                )
            )
    return out


def _label_names(raw: object) -> tuple[str, ...]:
    # `gh issue list --json labels` returns objects; ranking only needs names.
    if not isinstance(raw, list):
        return ()
    return tuple(str(o["name"]) for o in raw if isinstance(o, dict) and "name" in o)


def scaffold_candidates(
    manifest: list[ProposedTool],
    built_repos: set[str],
    urgency: dict[str, int] | None = None,
    *,
    paused: bool = False,
    dep_is_healthy: Callable[[str], bool] = always_healthy,
) -> list[Candidate]:
    # A proposal with no repo yet becomes a "scaffold this tool" candidate, kept only
    # if every dependency in its manifest entry is already satisfied (readiness) AND
    # healthy -- don't recommend building on top of a dependency that's currently
    # broken (dep_is_healthy defaults to a no-op; the orchestrator wires in a real
    # check via manifest.critical_issue_health_check).
    # `urgency` still carries MyPlanner's "build X next" boost for reporting, but
    # ranking is the label sort_key now, so a "pause new tools" flag can no longer
    # be expressed as a large negative score -- it is a filter: `paused` drops
    # every scaffold outright, which is what the flag always meant.
    if paused:
        return []
    urgency = urgency or {}
    out: list[Candidate] = []
    for tool in manifest:
        # The registry lists the whole fleet; only a still-unbuilt design is a
        # scaffold candidate. The status check also covers a partial repo
        # listing, where a shipped repo could be missing from built_repos.
        if tool.status == "shipped" or tool.repo in built_repos:
            continue
        if not is_ready(tool, built_repos=built_repos, dep_is_healthy=dep_is_healthy):
            continue
        out.append(
            Candidate(
                id=f"scaffold:{tool.repo}",
                repo=tool.repo,
                tool=tool.tool,
                title=tool.title,
                kind="scaffold",
                created_at=tool.added,
                urgency=urgency.get(tool.repo, 0),
                # A proposed tool has no issue to label. `lane:product` is what
                # it would be labelled as the moment it becomes one, and no
                # `prio:*` means it ranks after every prioritized product issue
                # -- an unbuilt design should not preempt live work.
                labels=("lane:product",),
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


_BLOCKER_REF_RE = re.compile(r"(?:([A-Za-z0-9_.-]+)/)?([A-Za-z0-9_.-]+)#(\d+)")


def extract_blocker_ref(entry: LedgerEntry) -> tuple[str | None, str, int] | None:
    # Check structured blocker field first (recorded by my-fleet and my-coder).
    blocker = entry.data.get("blocker")
    if isinstance(blocker, str) and blocker.strip():
        m = _BLOCKER_REF_RE.search(blocker.strip())
        if m:
            return m.group(1), m.group(2), int(m.group(3))

    # Fall back to matching explicit blocker signals in detail or final_message
    for text in (
        entry.detail,
        str(entry.data.get("detail", "")),
        str(entry.data.get("final_message", "")),
    ):
        if not text:
            continue
        m = _BLOCKER_REF_RE.search(text)
        if m:
            return m.group(1), m.group(2), int(m.group(3))
    return None


def is_issue_open(runner: Runner, org: str, repo: str, number: int) -> bool:
    try:
        raw = runner(
            [
                "issue",
                "view",
                str(number),
                "--repo",
                f"{org}/{repo}",
                "--json",
                "state",
            ]
        )
        data = json.loads(raw)
        return str(data.get("state", "")).upper() == "OPEN"
    except Exception:
        return False


def scan_blocker_urgency(
    repo_root: str | Path,
    repos: list[str],
    runner: Runner,
    org: str,
    ledger: Ledger | None = None,
) -> dict[str, int]:
    # Scans dev-ledgers and the runtime ledger for open `blocked` outcomes.
    # When an issue is explicitly blocked on <org>/<repo>#<n>, verify if that
    # blocking issue is currently OPEN via gh. If open, boost the blocking
    # candidate by +50 so CAD unblocks dependent tasks immediately (my-orchestrator#32).
    root = Path(repo_root)
    entries: list[LedgerEntry] = []
    if ledger is not None:
        entries.extend([e for e in ledger if e.outcome == "blocked"])

    fleet_ledger = root / ".my-fleet" / "ledger.jsonl"
    if fleet_ledger.exists() and (ledger is None or getattr(ledger, "path", None) != fleet_ledger):
        try:
            entries.extend([e for e in Ledger(fleet_ledger) if e.outcome == "blocked"])
        except Exception:
            pass

    for repo in repos:
        repo_path = root / repo
        if (repo_path / "dev-ledger").is_dir():
            try:
                entries.extend([e for e in read_all(root=repo_path) if e.outcome == "blocked"])
            except Exception:
                pass

    boosts: dict[str, int] = {}
    open_cache: dict[tuple[str, str, int], bool] = {}
    seen_pairs: set[tuple[str, str]] = set()

    for e in entries:
        ref = extract_blocker_ref(e)
        if ref is None:
            continue
        ref_org, repo, num = ref
        target_org = ref_org or org
        key = (target_org, repo, num)
        if key not in open_cache:
            open_cache[key] = is_issue_open(runner, target_org, repo, num)

        if open_cache[key]:
            cand_id = f"{repo}#{num}"
            blocked_id = str(e.data.get("candidate") or e.data.get("issue") or e.detail or cand_id)
            if (blocked_id, cand_id) not in seen_pairs:
                seen_pairs.add((blocked_id, cand_id))
                boosts[cand_id] = boosts.get(cand_id, 0) + 50

    return boosts


# MyPlanner feeds its plan back as a reported signal (a "next" item raises its
# repo's urgency, "soon" nudges it) plus one hard gate: a "pause new tools" flag
# stops scaffolds entirely.
_HORIZON_BOOST = {"next": 3, "soon": 1, "later": 0}
_PAUSE_MARKERS = ("pause new tool", "freeze new tool", "no new tool", "hold new tool")


@dataclass(frozen=True)
class PlanSignal:
    boosts: dict[str, int] = field(default_factory=dict)  # repo -> urgency boost
    scaffold_paused: bool = False  # drop every scaffold candidate


def plan_signal_from_entry(entry: LedgerEntry, repos: list[str]) -> PlanSignal:
    boosts: dict[str, int] = {}
    for item in entry.data.get("plan") or []:
        repo = _match_repo(str(item.get("item", "")), repos)
        if repo is None:
            continue
        boosts[repo] = boosts.get(repo, 0) + _HORIZON_BOOST.get(item.get("horizon", ""), 0)
    paused = _has_pause_flag(entry.data.get("flags") or [])
    return PlanSignal(boosts={r: b for r, b in boosts.items() if b}, scaffold_paused=paused)


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
    return any(isinstance(f, str) and any(m in f.lower() for m in _PAUSE_MARKERS) for f in flags)
