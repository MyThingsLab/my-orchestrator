from myorchestrator.candidates import Candidate, leaders


def test_leaders_returns_only_ties_with_top_urgency_and_age():
    c1 = Candidate(
        id="repo#1",
        repo="repo",
        tool="tool",
        title="First",
        kind="issue",
        created_at="2026-01-01T00:00:00Z",
        urgency=5,
    )
    c2 = Candidate(
        id="repo#2",
        repo="repo",
        tool="tool",
        title="Second, tied with first",
        kind="issue",
        created_at="2026-01-01T00:00:00Z",
        urgency=5,
    )
    c3 = Candidate(
        id="repo#3",
        repo="repo",
        tool="tool",
        title="Same age, lower urgency",
        kind="issue",
        created_at="2026-01-01T00:00:00Z",
        urgency=1,
    )
    c4 = Candidate(
        id="repo#4",
        repo="repo",
        tool="tool",
        title="Same urgency, different age",
        kind="issue",
        created_at="2026-02-01T00:00:00Z",
        urgency=5,
    )

    ranked = [c1, c2, c3, c4]

    result = leaders(ranked)

    assert result == [c1, c2]
