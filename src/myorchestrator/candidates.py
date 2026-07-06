from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Candidate:
    # id is "repo#number" for a live issue, "scaffold:<tool>" for a not-yet-built tool.
    id: str
    repo: str
    tool: str
    title: str
    kind: str  # "issue" | "scaffold"
    created_at: str  # ISO-8601; the oldest-first sort key
    urgency: int = 0

    def summary(self) -> str:
        flags = f" urgency={self.urgency}" if self.urgency else ""
        meta = f"{self.kind}, {self.repo}, since {self.created_at}{flags}"
        return f"{self.id}  ({meta}): {self.title}"


def rank(candidates: list[Candidate]) -> list[Candidate]:
    # Urgency boost first (higher wins), then strict oldest-first, then id for a
    # total, deterministic order.
    return sorted(candidates, key=lambda c: (-c.urgency, c.created_at, c.id))


def leaders(ranked: list[Candidate]) -> list[Candidate]:
    # The top candidates that are indistinguishable by the deterministic rules:
    # same urgency and same age. More than one => a genuine tie for the Engine.
    if not ranked:
        return []
    top = ranked[0]
    return [c for c in ranked if c.urgency == top.urgency and c.created_at == top.created_at]
