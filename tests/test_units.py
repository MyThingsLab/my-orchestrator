from __future__ import annotations

from mythings.ledger import LedgerEntry

from myorchestrator.candidates import Candidate, leaders, rank
from myorchestrator.manifest import ProposedTool, is_ready
from myorchestrator.sources import (
    plan_signal_from_entry,
    scaffold_candidates,
    urgency_from_entries,
)


def _c(id_: str, created_at: str, urgency: int = 0) -> Candidate:
    return Candidate(
        id=id_, repo="r", tool="r", title="", kind="issue", created_at=created_at, urgency=urgency
    )


def test_rank_is_urgency_then_oldest_then_id() -> None:
    a = _c("a", "2026-05-01")
    b = _c("b", "2026-01-01")
    c = _c("c", "2026-09-01", urgency=1)
    ranked = rank([a, b, c])
    assert [x.id for x in ranked] == ["c", "b", "a"]


def test_leaders_groups_same_age_and_urgency() -> None:
    a = _c("a", "2026-01-01")
    b = _c("b", "2026-01-01")
    c = _c("c", "2026-02-01")
    assert {x.id for x in leaders(rank([a, b, c]))} == {"a", "b"}


def test_leaders_single_when_ages_differ() -> None:
    assert [x.id for x in leaders(rank([_c("a", "2026-01-01"), _c("b", "2026-02-01")]))] == ["a"]


def _tool(depends_on: list[str]) -> ProposedTool:
    return ProposedTool(tool="T", repo="my-t", title="", added="2026-07-05", depends_on=depends_on)


def test_is_ready_tool_dependency() -> None:
    assert is_ready(_tool(["tool:my-wiki"]), built_repos={"my-wiki"}, core_has=lambda _: False)
    assert not is_ready(_tool(["tool:my-wiki"]), built_repos=set(), core_has=lambda _: False)


def test_is_ready_core_dependency() -> None:
    assert is_ready(_tool(["core:diff"]), built_repos=set(), core_has=lambda a: a == "diff")
    assert not is_ready(_tool(["core:diff"]), built_repos=set(), core_has=lambda _: False)


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
    assert signal.scaffold_penalty == 0


def test_plan_signal_pause_flag_penalizes_scaffolds() -> None:
    entry = _plan_entry([], flags=["pause new tools, close a safety gap first"])
    signal = plan_signal_from_entry(entry, ["my-tester"])
    assert signal.scaffold_penalty == 100


def test_plan_signal_unmatched_item_is_ignored() -> None:
    entry = _plan_entry([{"item": "something with no repo name", "horizon": "next"}])
    assert plan_signal_from_entry(entry, ["my-tester"]).boosts == {}


def test_scaffold_candidates_apply_boost_and_penalty() -> None:
    manifest = [
        ProposedTool("MyTester", "my-tester", "t", "2026-01-01", []),
        ProposedTool("MyReviewer", "my-reviewer", "r", "2026-01-02", []),
    ]
    cands = scaffold_candidates(
        manifest, built_repos=set(), urgency={"my-tester": 3}, penalty=100
    )
    by_repo = {c.repo: c.urgency for c in cands}
    assert by_repo["my-tester"] == 3 - 100  # boosted then penalized
    assert by_repo["my-reviewer"] == -100  # only penalized
