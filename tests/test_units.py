from __future__ import annotations

import json

from mythings.github import GitHubError
from mythings.ledger import LedgerEntry

from conftest import fake_gh, issue
from myorchestrator.manifest import (
    ProposedTool,
    critical_issue_health_check,
    is_ready,
)
from myorchestrator.sources import (
    extract_blocker_ref,
    is_issue_open,
    issue_candidates,
    plan_signal_from_entry,
    scaffold_candidates,
    scan_blocker_urgency,
    urgency_from_entries,
)

# Ranking and the label filters live in tests/test_candidates.py.


def _tool(depends_on: list[str]) -> ProposedTool:
    return ProposedTool(tool="T", repo="my-t", title="", added="2026-07-05", depends_on=depends_on)


def test_is_ready_tool_dependency() -> None:
    assert is_ready(_tool(["tool:my-wiki"]), built_repos={"my-wiki"}, core_has=lambda _: False)
    assert not is_ready(_tool(["tool:my-wiki"]), built_repos=set(), core_has=lambda _: False)


def test_is_ready_core_dependency() -> None:
    assert is_ready(_tool(["core:diff"]), built_repos=set(), core_has=lambda a: a == "diff")
    assert not is_ready(_tool(["core:diff"]), built_repos=set(), core_has=lambda _: False)


def test_is_ready_defaults_to_healthy_when_no_check_given() -> None:
    # Purely additive: a caller that never passes dep_is_healthy sees the same
    # behavior as before this existed.
    assert is_ready(_tool(["tool:my-wiki"]), built_repos={"my-wiki"})


def test_is_ready_unhealthy_dependency_is_not_ready() -> None:
    assert not is_ready(
        _tool(["tool:my-wiki"]), built_repos={"my-wiki"}, dep_is_healthy=lambda _: False
    )


def _critical_search_result(*, org: str, broken_repos: list[str]):
    def runner(argv: list[str]) -> str:
        assert argv[:2] == ["search", "issues"]
        assert argv[argv.index("--owner") + 1] == org
        return json.dumps([{"repository": {"nameWithOwner": f"{org}/{r}"}} for r in broken_repos])

    return runner


def test_critical_issue_health_check_flags_repo_with_open_critical_issue() -> None:
    check = critical_issue_health_check(
        _critical_search_result(org="o", broken_repos=["my-guard"]), "o"
    )
    assert check("my-guard") is False
    assert check("my-wiki") is True


def test_critical_issue_health_check_fails_closed_on_gh_error() -> None:
    def raising_runner(argv: list[str]) -> str:
        raise GitHubError("gh search issues failed (1): rate limited")

    check = critical_issue_health_check(raising_runner, "o")
    assert check("my-wiki") is False


def test_critical_issue_health_check_fails_closed_on_malformed_json() -> None:
    check = critical_issue_health_check(lambda argv: "not json", "o")
    assert check("my-wiki") is False


def test_critical_issue_health_check_caches_across_calls() -> None:
    calls = []

    def runner(argv: list[str]) -> str:
        calls.append(argv)
        return json.dumps([])

    check = critical_issue_health_check(runner, "o")
    check("my-wiki")
    check("my-guard")
    check("my-tester")

    assert len(calls) == 1  # one org-wide search, not one per dependency checked


def test_urgency_open_and_resolved_signals() -> None:
    entries = [
        LedgerEntry(tool="d", kind="drift", outcome="drift_found"),
        LedgerEntry(tool="t", kind="ask", outcome="awaiting"),
    ]
    assert urgency_from_entries(entries) == 2
    entries.append(LedgerEntry(tool="d", kind="drift", outcome="drift_resolved"))
    assert urgency_from_entries(entries) == 1
    entries.append(LedgerEntry(tool="t", kind="ask", outcome="answered"))
    assert urgency_from_entries(entries) == 0


def _plan_entry(plan: list[dict], flags: list[str] | None = None) -> LedgerEntry:
    return LedgerEntry(
        tool="myplanner",
        kind="plan",
        outcome="success",
        data={"plan": plan, "flags": flags or []},
    )


def test_plan_signal_boosts_repo_by_horizon() -> None:
    repos = ["my-tester", "my-reviewer", "my-drift-watcher"]
    entry = _plan_entry(
        [
            {"item": "build my-tester", "horizon": "next"},
            {"item": "then my-reviewer", "horizon": "soon"},
            {"item": "my-drift-watcher later", "horizon": "later"},
        ]
    )
    signal = plan_signal_from_entry(entry, repos)
    assert signal.boosts == {"my-tester": 3, "my-reviewer": 1}  # 'later' == 0 dropped
    assert signal.scaffold_paused is False


def test_plan_signal_pause_flag_pauses_scaffolds() -> None:
    entry = _plan_entry([], flags=["pause new tools, close a safety gap first"])
    signal = plan_signal_from_entry(entry, ["my-tester"])
    assert signal.scaffold_paused is True


def test_plan_signal_unmatched_item_is_ignored() -> None:
    entry = _plan_entry([{"item": "something with no repo name", "horizon": "next"}])
    assert plan_signal_from_entry(entry, ["my-tester"]).boosts == {}


def test_scaffold_candidates_carry_the_planner_boost_and_a_product_lane() -> None:
    manifest = [
        ProposedTool("MyTester", "my-tester", "t", "2026-01-01", []),
        ProposedTool("MyReviewer", "my-reviewer", "r", "2026-01-02", []),
    ]
    cands = scaffold_candidates(manifest, built_repos=set(), urgency={"my-tester": 3})

    assert {c.repo: c.urgency for c in cands} == {"my-tester": 3, "my-reviewer": 0}
    # No prio label: an unbuilt design ranks after every prioritized live issue.
    assert all(c.labels == ("lane:product",) for c in cands)


def test_scaffold_candidates_are_dropped_entirely_when_paused() -> None:
    manifest = [ProposedTool("MyTester", "my-tester", "t", "2026-01-01", [])]
    assert scaffold_candidates(manifest, built_repos=set(), paused=True) == []


def test_issue_candidates_excludes_blocked_issue_numbers() -> None:
    runner = fake_gh(
        repos=["my-t"],
        issues={
            "my-t": [
                issue(1, "foundation", "2026-01-01T00:00:00Z"),
                issue(2, "feature", "2026-01-02T00:00:00Z"),
            ]
        },
    )
    cands = issue_candidates(runner, "o", ["my-t"], {}, blocked={"my-t": frozenset({2})})
    assert [c.id for c in cands] == ["my-t#1"]  # #2 is blocked on an unfinished dependency


def test_issue_candidates_carry_the_labels_and_number_ranking_needs() -> None:
    runner = fake_gh(
        repos=["my-t"],
        issues={
            "my-t": [issue(7, "x", "2026-01-01T00:00:00Z", ("lane:core", "prio:P0", "size:M"))]
        },
    )
    (cand,) = issue_candidates(runner, "o", ["my-t"], {})

    assert cand.number == 7
    assert cand.labels == ("lane:core", "prio:P0", "size:M")
    assert cand.facets().lane == "core"


def test_scaffold_candidates_skip_shipped_entries() -> None:
    manifest = [
        ProposedTool("MyTester", "my-tester", "t", "2026-01-01", [], status="shipped"),
        ProposedTool("MyReviewer", "my-reviewer", "r", "2026-01-02", []),
    ]
    # my-tester is absent from built_repos (e.g. a partial listing) but the
    # registry already knows it shipped — it must not resurface as a scaffold.
    cands = scaffold_candidates(manifest, built_repos=set())
    assert [c.repo for c in cands] == ["my-reviewer"]


def test_scaffold_candidates_excludes_unhealthy_dependency() -> None:
    manifest = [
        ProposedTool("MyReviewer", "my-reviewer", "r", "2026-01-02", ["tool:my-guard"]),
        ProposedTool("MyTester", "my-tester", "t", "2026-01-01", ["tool:my-wiki"]),
    ]
    cands = scaffold_candidates(
        manifest,
        built_repos={"my-guard", "my-wiki"},
        dep_is_healthy=lambda name: name != "my-guard",
    )
    # my-reviewer depends on the unhealthy my-guard -- not recommended for
    # scaffolding until that's fixed; my-tester's dependency is unaffected.
    assert [c.repo for c in cands] == ["my-tester"]


def test_default_manifest_is_the_core_fleet_registry() -> None:
    from myorchestrator.manifest import default_manifest_path, load_manifest

    tools = load_manifest(default_manifest_path())
    assert len(tools) >= 30
    assert {t.status for t in tools} <= {"designed", "building", "shipped"}
    assert any(t.status == "designed" for t in tools)


def test_extract_blocker_ref() -> None:
    # 1. Structured data
    e1 = LedgerEntry(
        tool="mycoder",
        kind="build",
        outcome="blocked",
        data={"blocker": "MyThingsLab/my-guard#7"},
    )
    assert extract_blocker_ref(e1) == ("MyThingsLab", "my-guard", 7)

    # 2. Detail string
    e2 = LedgerEntry(
        tool="fleet_dispatch",
        kind="dispatch",
        outcome="blocked",
        detail="paused on cross-repo blocker my-core#42",
    )
    assert extract_blocker_ref(e2) == (None, "my-core", 42)

    # 3. Final message sentinel
    e3 = LedgerEntry(
        tool="fleet_dispatch",
        kind="dispatch",
        outcome="blocked",
        data={"final_message": "FLEET-DISPATCH-BLOCKED: MyThingsLab/my-core#99\nsomething else"},
    )
    assert extract_blocker_ref(e3) == ("MyThingsLab", "my-core", 99)

    # 4. Non-blocked or missing ref
    e4 = LedgerEntry(tool="mycoder", kind="build", outcome="success")
    assert extract_blocker_ref(e4) is None


def test_is_issue_open() -> None:
    runner = fake_gh(
        repos=["my-repo"],
        issues={"my-repo": [{"number": 1, "state": "OPEN"}, {"number": 2, "state": "CLOSED"}]},
    )
    assert is_issue_open(runner, "MyThingsLab", "my-repo", 1) is True
    assert is_issue_open(runner, "MyThingsLab", "my-repo", 2) is False
    assert is_issue_open(runner, "MyThingsLab", "my-repo", 3) is False


def test_scan_blocker_urgency_open_and_closed(tmp_path) -> None:
    from mythings.ledger import Ledger

    root = tmp_path / "workspace"
    dev = root / "my-worker" / "dev-ledger"
    dev.mkdir(parents=True)
    dev_ledger = Ledger(dev / "session.jsonl")
    dev_ledger.record(
        tool="mycoder",
        kind="build",
        outcome="blocked",
        candidate="my-worker#1",
        blocker="MyThingsLab/my-blocker#10",
    )
    dev_ledger.record(
        tool="mycoder",
        kind="build",
        outcome="blocked",
        candidate="my-worker#2",
        blocker="MyThingsLab/my-closed#20",
    )

    repos = ["my-worker", "my-blocker", "my-closed"]
    runner = fake_gh(
        repos=repos,
        issues={
            "my-blocker": [{"number": 10, "state": "OPEN"}],
            "my-closed": [{"number": 20, "state": "CLOSED"}],
        },
    )

    boosts = scan_blocker_urgency(root, repos, runner=runner, org="MyThingsLab")
    assert boosts == {"my-blocker#10": 50}
