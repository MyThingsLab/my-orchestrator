from __future__ import annotations

import json
from pathlib import Path

from mythings.policy import ALLOW, Action, Decision, PolicyResult
from mythings.testing import FakeGh, make_git_repo

from myorchestrator.plans import PolicyDenied, sync_plans

_PLAN = """| Task | Owner | Depends on | Issue | Status |
|---|---|---|---|---|
| foundation | core | | #1 | todo |
| feature | core | foundation | #2 | todo |
"""


class _AllowPolicy:
    def evaluate(self, action: Action) -> PolicyResult:
        return ALLOW


class _DenyPolicy:
    def evaluate(self, action: Action) -> PolicyResult:
        return PolicyResult(Decision.DENY)


def _repo_with_plan(tmp_path: Path, plan: str = _PLAN) -> Path:
    return make_git_repo(tmp_path, files={"README.md": "# x\n", "plans/roadmap.md": plan}).path


def _fake(*, issue_states: dict[int, str], open_prs: list[int] | None = None) -> FakeGh:
    def issue_view(argv: list[str]) -> str:
        number = int(argv[2])
        return issue_states.get(number, "OPEN")

    return FakeGh(
        {
            ("issue", "view"): issue_view,
            ("pr", "list"): json.dumps(
                [{"number": n} for n in (open_prs or [])] if open_prs else []
            ),
            ("pr", "create"): "https://github.com/o/r/pull/9\n",
        }
    )


def test_no_plans_dir_is_a_noop(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path, files={"README.md": "# x\n"}).path
    root = tmp_path / "root"
    root.mkdir()
    (root / "my-repo").symlink_to(repo)  # cheap way to give it the expected name

    result = sync_plans(
        repo_root=root,
        org="o",
        repo="my-repo",
        runner=_fake(issue_states={}),
        policy=_AllowPolicy(),
    )

    assert result.blocked_issues == frozenset()
    assert result.pr is None


def test_task_with_unmet_dependency_is_blocked(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "my-repo").symlink_to(_repo_with_plan(tmp_path / "src"))
    fake = _fake(issue_states={1: "OPEN", 2: "OPEN"})

    result = sync_plans(repo_root=root, org="o", repo="my-repo", runner=fake, policy=_AllowPolicy())

    assert result.blocked_issues == frozenset({2})  # foundation (#1) isn't done yet


def test_reconciled_done_task_unblocks_its_dependent(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "my-repo").symlink_to(_repo_with_plan(tmp_path / "src"))
    fake = _fake(issue_states={1: "CLOSED", 2: "OPEN"})

    result = sync_plans(repo_root=root, org="o", repo="my-repo", runner=fake, policy=_AllowPolicy())

    assert result.blocked_issues == frozenset()  # foundation closed -> feature is ready
    assert result.pr is not None  # status changed, so a sync PR is opened
    assert any(c[:2] == ["pr", "create"] for c in fake.calls)


def test_no_status_change_opens_no_pr(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "my-repo").symlink_to(_repo_with_plan(tmp_path / "src"))
    fake = _fake(issue_states={1: "OPEN", 2: "OPEN"})

    result = sync_plans(repo_root=root, org="o", repo="my-repo", runner=fake, policy=_AllowPolicy())

    assert result.pr is None
    assert not any(c[:2] == ["pr", "create"] for c in fake.calls)


def test_existing_open_sync_pr_is_reused_not_duplicated(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "my-repo").symlink_to(_repo_with_plan(tmp_path / "src"))
    fake = _fake(issue_states={1: "CLOSED", 2: "OPEN"}, open_prs=[42])

    result = sync_plans(repo_root=root, org="o", repo="my-repo", runner=fake, policy=_AllowPolicy())

    assert result.pr == 42
    assert not any(c[:2] == ["pr", "create"] for c in fake.calls)


def test_policy_deny_raises_instead_of_silently_pushing(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "my-repo").symlink_to(_repo_with_plan(tmp_path / "src"))
    fake = _fake(issue_states={1: "CLOSED", 2: "OPEN"})

    try:
        sync_plans(repo_root=root, org="o", repo="my-repo", runner=fake, policy=_DenyPolicy())
        raise AssertionError("expected PolicyDenied")
    except PolicyDenied:
        pass
