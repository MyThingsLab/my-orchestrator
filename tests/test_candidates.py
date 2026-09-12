from __future__ import annotations

from datetime import UTC, datetime

from myorchestrator.candidates import Candidate, leaders, rank, triage

NOW = datetime(2026, 9, 12, tzinfo=UTC)

_READY = ("lane:product", "prio:P2", "size:S")


def _c(number: int, created_at: str, labels: tuple[str, ...], repo: str = "repo") -> Candidate:
    return Candidate(
        id=f"{repo}#{number}",
        repo=repo,
        tool=repo,
        title="",
        kind="issue",
        created_at=created_at,
        number=number,
        labels=labels,
    )


def test_rank_puts_lane_before_priority_before_age() -> None:
    kernel_p2 = _c(1, "2026-09-01T00:00:00Z", ("lane:kernel", "prio:P2", "size:S"))
    core_p3 = _c(2, "2026-09-01T00:00:00Z", ("lane:core", "prio:P3", "size:S"))
    product_p0 = _c(3, "2026-01-01T00:00:00Z", ("lane:product", "prio:P0", "size:S"))

    ranked = rank([product_p0, kernel_p2, core_p3], now=NOW)

    # Lane wins outright: a core P3 outranks a kernel P2, and both outrank a
    # product P0 that is eight months older.
    assert [c.id for c in ranked] == ["repo#2", "repo#1", "repo#3"]


def test_rank_falls_through_to_oldest_first_within_a_lane_and_priority() -> None:
    newer = _c(1, "2026-09-01T00:00:00Z", _READY)
    older = _c(2, "2026-01-01T00:00:00Z", _READY)

    assert [c.id for c in rank([newer, older], now=NOW)] == ["repo#2", "repo#1"]


def test_a_critical_issue_outranks_everything() -> None:
    critical = _c(1, "2026-09-11T00:00:00Z", ("lane:product", "prio:P3", "size:S", "critical"))
    core_p0 = _c(2, "2026-01-01T00:00:00Z", ("lane:core", "prio:P0", "size:S"))

    assert [c.id for c in rank([core_p0, critical], now=NOW)] == ["repo#1", "repo#2"]


def test_a_mixed_backlog_totally_orders() -> None:
    items = [
        _c(1, "2026-01-01T00:00:00Z", ("lane:kernel", "prio:P1", "size:S")),
        _c(2, "2026-01-01T00:00:00Z", ("lane:kernel", "prio:P1", "size:S"), repo="other"),
        _c(3, "2026-01-01T00:00:00Z", ("lane:core", "prio:P0", "size:M")),
        _c(4, "2026-05-01T00:00:00Z", ("lane:product", "size:S")),
    ]
    ranked = rank(items, now=NOW)

    # No two candidates collide, and the only pair that needed the (repo,
    # number) tail to separate them is the kernel P1 pair.
    assert len({(c.repo, c.number) for c in ranked}) == len(items)
    assert [c.id for c in ranked] == ["repo#3", "other#2", "repo#1", "repo#4"]


def test_leaders_are_the_candidates_tied_on_everything_but_repo_and_number() -> None:
    a = _c(1, "2026-01-01T00:00:00Z", _READY)
    b = _c(2, "2026-01-01T00:00:00Z", _READY)
    younger = _c(3, "2026-02-01T00:00:00Z", _READY)
    lower_prio = _c(4, "2026-01-01T00:00:00Z", ("lane:product", "prio:P3", "size:S"))

    result = leaders(rank([a, b, younger, lower_prio], now=NOW), now=NOW)

    assert [c.id for c in result] == ["repo#1", "repo#2"]


def test_leaders_is_a_single_candidate_when_ages_differ() -> None:
    ranked = rank([_c(1, "2026-01-01T00:00:00Z", _READY), _c(2, "2026-02-01T00:00:00Z", _READY)])
    assert [c.id for c in leaders(ranked)] == ["repo#1"]


def test_leaders_of_an_empty_backlog() -> None:
    assert leaders([]) == []


def test_triage_excludes_blocked_oversized_and_unlabelled_issues() -> None:
    ok = _c(1, "2026-01-01T00:00:00Z", _READY)
    blocked = _c(2, "2026-01-01T00:00:00Z", ("lane:product", "size:S", "state:blocked"))
    oversized = _c(3, "2026-01-01T00:00:00Z", ("lane:product", "size:L"))
    unlabelled = _c(4, "2026-01-01T00:00:00Z", ("my-orchestrator",))

    keep, dropped = triage([ok, blocked, oversized, unlabelled])

    assert [c.id for c in keep] == ["repo#1"]
    # Every exclusion is reported with a reason, never silently skipped.
    assert [e.candidate.id for e in dropped] == ["repo#2", "repo#3", "repo#4"]
    assert dropped[0].reasons == ("state:blocked is not dispatchable",)
    assert "size:L" in dropped[1].reasons[0]
    assert dropped[2].reasons == ("missing lane", "missing size")


def test_triage_lets_a_scaffold_through_without_a_size_label() -> None:
    scaffold = Candidate(
        id="scaffold:my-tester",
        repo="my-tester",
        tool="MyTester",
        title="",
        kind="scaffold",
        created_at="2026-01-01",
        labels=("lane:product",),
    )
    keep, dropped = triage([scaffold])

    assert keep == [scaffold]  # no issue exists yet to carry one
    assert dropped == []


def test_summary_shows_the_lane_and_priority_the_ranking_used() -> None:
    summary = _c(1, "2026-01-01T00:00:00Z", ("lane:core", "prio:P0", "size:S")).summary()
    assert "lane:core" in summary
    assert "prio:P0" in summary
