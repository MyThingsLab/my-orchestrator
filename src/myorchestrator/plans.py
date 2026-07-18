from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from mythings.github import GitHub, Runner
from mythings.isolation import Workspace, in_github_actions
from mythings.plan import read_plan as _read_plan
from mythings.plan import ready, reconcile, write_plan
from mythings.policy import Action, Decision, Policy

# my-orchestrator's own local checkout of every repo it considers lives under
# repo_root (the same convention scan_urgency's dev-ledger read already
# relies on) -- plans/*.md is read from there, not fetched over the gh API.
_PLAN_BRANCH = "myorchestrator/plan-sync"


class PolicyDenied(RuntimeError):
    pass


@dataclass(frozen=True)
class PlanSync:
    blocked_issues: frozenset[int] = field(default_factory=frozenset)
    pr: int | None = None


def sync_plans(
    *, repo_root: str | Path, org: str, repo: str, runner: Runner, policy: Policy
) -> PlanSync:
    local_repo = Path(repo_root) / repo
    if not (local_repo / "plans").is_dir():
        return PlanSync()

    blocked: set[int] = set()
    changed_relpaths: list[str] = []
    with Workspace(local_repo, "main") as tree:
        for plan_path in sorted((tree / "plans").glob("*.md")):
            tasks = _read_plan(plan_path)
            reconciled, changed = reconcile(tasks, repo=f"{org}/{repo}", runner=runner)
            ready_titles = {t.title for t in ready(reconciled)}
            for t in reconciled:
                if t.issue is not None and t.status != "done" and t.title not in ready_titles:
                    blocked.add(t.issue)
            if changed:
                write_plan(plan_path, reconciled)
                changed_relpaths.append(str(plan_path.relative_to(tree)))

        if not changed_relpaths:
            return PlanSync(blocked_issues=frozenset(blocked))

        pr = _open_sync_pr(tree, org, repo, runner, policy, changed_relpaths)
    return PlanSync(blocked_issues=frozenset(blocked), pr=pr)


def _guard(policy: Policy, command: str) -> None:
    action = Action(kind="bash", payload={"command": command})
    if policy.evaluate(action).under(unattended=in_github_actions()) is not Decision.ALLOW:
        raise PolicyDenied(f"policy blocked: {command}")


def _git(tree: Path, policy: Policy, argv: list[str]) -> None:
    _guard(policy, "git " + " ".join(argv))
    proc = subprocess.run(["git", "-C", str(tree), *argv], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(argv)} failed: {proc.stderr.strip()}")


def _existing_sync_pr(org: str, repo: str, runner: Runner) -> int | None:
    argv = [
        "pr",
        "list",
        "--repo",
        f"{org}/{repo}",
        "--head",
        _PLAN_BRANCH,
        "--state",
        "open",
        "--json",
        "number",
    ]
    rows = json.loads(runner(argv))
    return rows[0]["number"] if rows else None


def _open_sync_pr(
    tree: Path, org: str, repo: str, runner: Runner, policy: Policy, changed: list[str]
) -> int | None:
    existing = _existing_sync_pr(org, repo, runner)
    if existing is not None:
        return existing  # a prior sync PR is still open; don't pile up duplicates

    _git(tree, policy, ["checkout", "-b", _PLAN_BRANCH])
    _git(tree, policy, ["add", *changed])
    _git(tree, policy, ["commit", "-m", "docs: sync plan status"])
    _git(tree, policy, ["push", "-u", "origin", _PLAN_BRANCH])
    _guard(policy, f"gh pr create --repo {org}/{repo} --head {_PLAN_BRANCH} --base main")

    github = GitHub(f"{org}/{repo}", runner=runner)
    created = github.open_pr(
        title="docs: sync plan status",
        body="Reconciled plans/*.md against current issue/PR state (mythings.plan.reconcile).",
        base="main",
        head=_PLAN_BRANCH,
    )
    return created.number
