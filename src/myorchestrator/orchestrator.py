from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from mythings.engine import Engine, EngineRequest, NoopEngine
from mythings.github import Runner, _gh
from mythings.isolation import in_github_actions
from mythings.ledger import Ledger
from mythings.policy import ALLOW, Action, Decision, Policy, PolicyResult

from myorchestrator.assess import AssessResult
from myorchestrator.assess import assess as _assess
from myorchestrator.candidates import Candidate, leaders, rank
from myorchestrator.manifest import load_manifest
from myorchestrator.plans import PolicyDenied, sync_plans
from myorchestrator.sources import (
    issue_candidates,
    list_repos,
    read_plan_signal,
    scaffold_candidates,
    scan_urgency,
)

_ENGINE_SYSTEM = (
    "Among these deterministically-tied candidates, choose which the single available "
    "worker should tackle next, and say why. Reply with only a JSON object: "
    '{"chosen": "<candidate id>", "reason": "<one sentence>"}, nothing else.'
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
        plan_ledger: str | Path | None = None,
    ) -> None:
        self.org = org
        self.manifest_path = Path(manifest_path)
        self.repo_root = Path(repo_root)
        self.ledger = ledger
        self.runner = runner
        self.engine: Engine = engine or NoopEngine()
        self.policy: Policy = policy or _AllowPolicy()
        self.tracking = tracking
        # MyPlanner writes kind=plan to its own runtime ledger, not a dev-ledger;
        # default to where it lands when MyPlanner runs in its repo.
        self.plan_ledger = Path(
            plan_ledger
            if plan_ledger is not None
            else self.repo_root / "my-planner" / ".mythings" / "ledger.jsonl"
        )

    def next(self) -> Recommendation:
        return self.next_n(1)[0]

    def assess(self, repo: str, *, max_new: int = 5) -> AssessResult:
        result = _assess(
            org=self.org,
            repo=repo,
            repo_root=self.repo_root,
            engine=self.engine,
            policy=self.policy,
            runner=self.runner,
            max_new=max_new,
        )
        self._record_assess(repo, result)
        return result

    def next_n(self, count: int) -> list[Recommendation]:
        # count=1 keeps the original single-worker path (Engine tie-break and the
        # tracking-issue update) untouched. count>1 is for multiple concurrently
        # available workers: each gets a distinct ranked candidate, so ties among
        # the picks don't need breaking — every tied candidate is getting worked
        # on anyway, just by a different worker.
        repos = list_repos(self.runner, self.org)  # step 1
        built = set(repos)
        manifest = load_manifest(self.manifest_path)
        urgency = scan_urgency(self.repo_root, repos)  # step 4 signals
        # A plan can name an unbuilt tool ("build my-tester next"), so the match
        # universe is live repos plus every manifest repo, not just what exists yet.
        universe = repos + [t.repo for t in manifest]
        signal = read_plan_signal(self.plan_ledger, universe)  # MyPlanner's pacing signal
        for repo, boost in signal.boosts.items():
            urgency[repo] = urgency.get(repo, 0) + boost
        blocked = self._sync_plans(repos)  # step 1.5: plans/*.md dependency gating
        candidates = [
            *issue_candidates(self.runner, self.org, repos, urgency, blocked),  # step 2 (issues)
            *scaffold_candidates(  # step 2+3 (proposals)
                manifest,
                built,
                urgency,
                penalty=signal.scaffold_penalty,
            ),
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

    def _sync_plans(self, repos: list[str]) -> dict[str, frozenset[int]]:
        # sync_plans() no-ops for a repo with no local plans/*.md (same
        # convention scan_urgency's dev-ledger read already relies on: a repo
        # this worker hasn't cloned, or that has none, is silently skipped).
        blocked: dict[str, frozenset[int]] = {}
        for repo in repos:
            try:
                result = sync_plans(
                    repo_root=self.repo_root,
                    org=self.org,
                    repo=repo,
                    runner=self.runner,
                    policy=self.policy,
                )
            except (PolicyDenied, RuntimeError):
                # A dirty local checkout or a denied git op must not take down
                # the whole ranking pass -- worst case, this repo's plan just
                # doesn't get a fresher read this cycle.
                continue
            if result.blocked_issues:
                blocked[repo] = result.blocked_issues
        return blocked

    def _break_tie(self, tied: list[Candidate]) -> tuple[Candidate, str]:
        result = self.engine.run(
            EngineRequest(
                prompt="\n".join(c.summary() for c in tied),
                system=_ENGINE_SYSTEM,
                context={"tie_count": len(tied)},
            )
        )
        by_id = {c.id: c for c in tied}
        try:
            # The model's own reply (EngineResult.text), not .data -- for
            # ClaudeCLIEngine, .data is the CLI's raw JSON envelope
            # (type/is_error/result/...), never the {"chosen": ...} shape the
            # prompt asks the model to reply with.
            obj = json.loads(result.text) if result.text else {}
        except json.JSONDecodeError:
            obj = {}
        chosen_id = obj.get("chosen")
        if isinstance(chosen_id, str) and chosen_id in by_id:
            return by_id[chosen_id], str(obj.get("reason", ""))
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

    def _record_assess(self, repo: str, result: AssessResult) -> None:
        self.ledger.record(
            tool="myorchestrator",
            kind="assess",
            outcome="success",
            detail=f"assess {repo}: {len(result.created)} filed, {len(result.skipped)} skipped",
            created=result.created,
            skipped=result.skipped,
            engine_used=result.engine_used,
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
