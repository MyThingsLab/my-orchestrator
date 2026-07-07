from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path

from mythings.engine import Engine, EngineRequest, NoopEngine
from mythings.github import Runner, _gh
from mythings.isolation import in_github_actions
from mythings.ledger import Ledger
from mythings.policy import ALLOW, Action, Decision, Policy, PolicyResult

from myorchestrator.candidates import Candidate, leaders, rank
from myorchestrator.manifest import load_manifest
from myorchestrator.sources import (
    issue_candidates,
    list_repos,
    scaffold_candidates,
    scan_urgency,
)

_ENGINE_SYSTEM = (
    "Among these deterministically-tied candidates, choose which the single available "
    "worker should tackle next, and say why."
)


class _AllowPolicy:
    def evaluate(self, action: Action) -> PolicyResult:
        return ALLOW


@dataclass(frozen=True)
class Recommendation:
    chosen: Candidate | None
    reason: str
    candidates: list[Candidate] = field(default_factory=list)
    engine_used: bool = False


@dataclass(frozen=True)
class Tracking:
    repo: str
    issue: int


class Orchestrator:
    def __init__(
        self,
        *,
        org: str,
        manifest_path: str | Path,
        repo_root: str | Path,
        ledger: Ledger,
        runner: Runner = _gh,
        engine: Engine | None = None,
        policy: Policy | None = None,
        tracking: Tracking | None = None,
    ) -> None:
        self.org = org
        self.manifest_path = Path(manifest_path)
        self.repo_root = Path(repo_root)
        self.ledger = ledger
        self.runner = runner
        self.engine: Engine = engine or NoopEngine()
        self.policy: Policy = policy or _AllowPolicy()
        self.tracking = tracking

    def next(self) -> Recommendation:
        return self.next_n(1)[0]

    def next_n(self, count: int) -> list[Recommendation]:
        # count=1 keeps the original single-worker path (Engine tie-break and the
        # tracking-issue update) untouched. count>1 is for multiple concurrently
        # available workers: each gets a distinct ranked candidate, so ties among
        # the picks don't need breaking — every tied candidate is getting worked
        # on anyway, just by a different worker.
        repos = list_repos(self.runner, self.org)  # step 1
        built = set(repos)
        urgency = scan_urgency(self.repo_root, repos)  # step 4 signals
        candidates = [
            *issue_candidates(self.runner, self.org, repos, urgency),  # step 2 (live issues)
            *scaffold_candidates(load_manifest(self.manifest_path), built),  # step 2+3 (proposals)
        ]
        ranked = rank(candidates)  # step 4 ranking

        if not ranked:
            rec = Recommendation(chosen=None, reason="no ready candidates", candidates=ranked)
            self._record(rec)
            return [rec]

        if count == 1:
            top = leaders(ranked)
            if len(top) == 1:
                rec = Recommendation(
                    chosen=top[0],
                    reason="sole top candidate by oldest-first ranking",
                    candidates=ranked,
                    engine_used=False,
                )
            else:
                chosen, reason = self._break_tie(top)  # step 5
                rec = Recommendation(
                    chosen=chosen, reason=reason, candidates=ranked, engine_used=True
                )
            self._record(rec)
            if rec.chosen is not None and self.tracking is not None:
                self._update_tracking(rec.chosen)
            return [rec]

        picks = ranked[:count]
        recs = [
            Recommendation(
                chosen=c,
                reason=f"ranked pick {i + 1}/{len(picks)} for {count} available workers",
                candidates=ranked,
                engine_used=False,
            )
            for i, c in enumerate(picks)
        ]
        for rec in recs:
            self._record(rec)
        return recs

    def _break_tie(self, tied: list[Candidate]) -> tuple[Candidate, str]:
        result = self.engine.run(
            EngineRequest(
                prompt="\n".join(c.summary() for c in tied),
                system=_ENGINE_SYSTEM,
                context={"tie_count": len(tied)},
            )
        )
        by_id = {c.id: c for c in tied}
        chosen_id = result.data.get("chosen")
        if isinstance(chosen_id, str) and chosen_id in by_id:
            return by_id[chosen_id], str(result.data.get("reason", ""))
        # NoopEngine / unusable reply: fall back to strict oldest-first. `tied` is
        # already ranked, so tied[0] is the deterministic winner.
        return tied[0], "tie broken deterministically (oldest-first) — Engine gave no usable choice"

    def _record(self, rec: Recommendation) -> None:
        chosen_id = rec.chosen.id if rec.chosen else ""
        self.ledger.record(
            tool="myorchestrator",
            kind="orchestrate",
            outcome="success",
            detail=f"next: {chosen_id or 'none'}",
            candidates=[c.id for c in rec.candidates],
            chosen=chosen_id,
            reason=rec.reason,
        )

    def _update_tracking(self, chosen: Candidate) -> None:
        repo = self.tracking.repo
        number = self.tracking.issue
        body = f"**Next up:** {chosen.id}\n\n{chosen.title}"
        argv = ["issue", "edit", str(number), "--repo", repo, "--body", body]
        action = Action(kind="bash", payload={"command": "gh " + shlex.join(argv)})
        decision = self.policy.evaluate(action).under(unattended=in_github_actions())
        if decision is Decision.ALLOW:
            self.runner(argv)
