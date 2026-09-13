from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from mythings.labels import Facets, QueueItem, parse, sort_key, validate

# Ranking is the CAD label schema's `sort_key`, not a score invented here:
# (not critical, lane_rank, prio_rank, -age_days, repo, number). Lexicographic
# and constant-free, so "why did this dispatch before that" is one line of a PR
# body rather than an argument about weights. The ledger/planner urgency signal
# still rides along on a Candidate -- it is reported and recorded -- but it no
# longer outranks a lane or priority a human actually set (my-orchestrator#25).


@dataclass(frozen=True)
class Candidate:
    # id is "repo#number" for a live issue, "scaffold:<tool>" for a not-yet-built tool.
    id: str
    repo: str
    tool: str
    title: str
    kind: str  # "issue" | "scaffold"
    created_at: str  # ISO-8601
    urgency: int = 0
    number: int = 0  # the issue number; 0 for a scaffold, which has no issue yet
    labels: tuple[str, ...] = ()

    def facets(self) -> Facets:
        return parse(self.labels)

    def queue_item(self, *, now: datetime | None = None) -> QueueItem:
        return QueueItem(
            repo=self.repo,
            number=self.number,
            labels=self.labels,
            age_days=age_days(self.created_at, now=now),
        )

    def summary(self) -> str:
        facets = self.facets()
        bits = [self.kind, self.repo, f"since {self.created_at}"]
        if facets.lane:
            bits.append(f"lane:{facets.lane}")
        if facets.prio:
            bits.append(f"prio:{facets.prio}")
        if self.urgency:
            bits.append(f"urgency={self.urgency}")
        return f"{self.id}  ({', '.join(bits)}): {self.title}"


@dataclass(frozen=True)
class Excluded:
    candidate: Candidate
    reasons: tuple[str, ...]


def age_days(created_at: str, *, now: datetime | None = None) -> float:
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    created = datetime.fromisoformat(created_at)
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return max((now - created).total_seconds() / 86400.0, 0.0)


def triage(candidates: list[Candidate]) -> tuple[list[Candidate], list[Excluded]]:
    # Hard filters, applied before ranking: an issue that isn't dispatchable
    # never competes for a slot. Every exclusion is reported rather than
    # silently dropped -- an unlabelled or oversized issue is work for
    # my-architect, and a backlog that quietly shrinks is how that work goes
    # unnoticed.
    keep: list[Candidate] = []
    dropped: list[Excluded] = []
    for c in candidates:
        # A scaffold has no GitHub issue and so no labels to validate; it
        # carries a synthetic lane from the fleet registry instead.
        if c.kind != "issue":
            keep.append(c)
            continue
        validation = validate(c.facets())
        if validation.dispatchable:
            keep.append(c)
        else:
            dropped.append(Excluded(candidate=c, reasons=validation.reasons))
    return keep, dropped


def rank(candidates: list[Candidate], *, now: datetime | None = None) -> list[Candidate]:
    now = now or datetime.now(UTC)
    return sorted(
        candidates,
        key=lambda c: (
            "critical" not in c.labels,
            -c.urgency if c.urgency >= 50 else 0,
            sort_key(c.queue_item(now=now)),
        ),
    )


# sort_key's last two components, (repo, number), exist only to guarantee a
# total order -- they are a coin toss, not a reason to prefer one candidate over
# another. A "genuine tie" for the Engine is therefore equality on everything
# before them.
_DECIDABLE = 4


def leaders(ranked: list[Candidate], *, now: datetime | None = None) -> list[Candidate]:
    if not ranked:
        return []
    now = now or datetime.now(UTC)
    top_cand = ranked[0]
    top = (
        "critical" in top_cand.labels,
        top_cand.urgency if top_cand.urgency >= 50 else 0,
        sort_key(top_cand.queue_item(now=now))[:_DECIDABLE],
    )
    return [
        c
        for c in ranked
        if (
            "critical" in c.labels,
            c.urgency if c.urgency >= 50 else 0,
            sort_key(c.queue_item(now=now))[:_DECIDABLE],
        )
        == top
    ]
