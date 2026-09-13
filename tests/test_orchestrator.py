from __future__ import annotations

from pathlib import Path

from mythings.engine import NoopEngine
from mythings.ledger import Ledger, LedgerEntry
from mythings.policy import Action, Decision, PolicyResult

from conftest import ScriptedEngine, fake_gh, issue, make_repo_root, mentry, write_manifest
from myorchestrator.orchestrator import Orchestrator, Tracking


def _drift(repo: str) -> LedgerEntry:
    return LedgerEntry(tool="mydriftwatcher", kind="drift", outcome="drift_found", detail=repo)


def test_happy_path_picks_the_top_lane_and_priority_without_an_engine_call(
    tmp_path: Path,
) -> None:
    # Three repos with one open issue each, plus one ready-to-scaffold tool. The
    # newest issue wins because it is the only kernel P0 -- lane and priority
    # outrank age, and the drift signal on my-searcher no longer moves the queue.
    repos = ["my-guard", "my-reporter", "my-searcher"]
    runner = fake_gh(
        repos=repos,
        issues={
            "my-guard": [issue(1, "guard bug", "2026-01-01T00:00:00Z")],
            "my-reporter": [issue(1, "reporter bug", "2026-03-01T00:00:00Z")],
            "my-searcher": [
                issue(
                    1,
                    "searcher bug",
                    "2026-05-01T00:00:00Z",
                    ("lane:kernel", "prio:P0", "size:S"),
                )
            ],
        },
    )
    repo_root = make_repo_root(tmp_path, repos, signals={"my-searcher": [_drift("my-searcher")]})
    manifest = write_manifest(
        tmp_path,
        [
            mentry("MyTester", "my-tester", "2026-06-01"),
            mentry("MyAdvisor", "my-advisor", "2026-02-01", ["tool:my-wiki"]),
        ],
    )
    engine = ScriptedEngine()
    ledger = Ledger(tmp_path / "ledger.jsonl")

    rec = Orchestrator(
        org="MyThingsLab",
        manifest_path=manifest,
        repo_root=repo_root,
        ledger=ledger,
        runner=runner,
        engine=engine,
    ).next()

    assert engine.calls == []  # no tie => Engine never fires
    assert rec.chosen is not None
    assert rec.chosen.id == "my-searcher#1"  # kernel P0 beats the two older product P2s
    assert rec.engine_used is False
    # not-ready MyAdvisor (needs my-wiki) is filtered out; ready MyTester survives
    ids = {c.id for c in rec.candidates}
    assert "scaffold:my-tester" in ids
    assert "scaffold:my-advisor" not in ids

    written = list(ledger)
    assert len(written) == 1
    entry = written[0]
    assert entry.kind == "orchestrate"
    assert entry.outcome == "success"
    assert entry.data["chosen"] == "my-searcher#1"
    assert entry.detail == "next: my-searcher#1"


def test_genuine_tie_calls_engine_once_and_uses_its_choice(tmp_path: Path) -> None:
    # Two scaffold candidates with identical age and no urgency => a real tie.
    runner = fake_gh(repos=[], issues={})
    repo_root = make_repo_root(tmp_path, [], signals={})
    manifest = write_manifest(
        tmp_path,
        [
            mentry("MyTester", "my-tester", "2026-07-05"),
            mentry("MyReporter", "my-reporter", "2026-07-05"),
        ],
    )
    # Deterministic order would pick scaffold:my-reporter (id-sorted first); the
    # Engine overrides it, proving its reply — not the fallback — is what's reported.
    # The reply lands in EngineResult.text (what a real ClaudeCLIEngine call would
    # put there), not .data (the CLI's own JSON envelope, never this shape).
    engine = ScriptedEngine('{"chosen": "scaffold:my-tester", "reason": "loop"}')
    ledger = Ledger(tmp_path / "ledger.jsonl")

    rec = Orchestrator(
        org="MyThingsLab",
        manifest_path=manifest,
        repo_root=repo_root,
        ledger=ledger,
        runner=runner,
        engine=engine,
    ).next()

    assert len(engine.calls) == 1
    assert engine.calls[0].context == {"tie_count": 2}
    assert rec.engine_used is True
    assert rec.chosen is not None and rec.chosen.id == "scaffold:my-tester"
    assert rec.reason == "loop"
    assert list(ledger)[0].data["chosen"] == "scaffold:my-tester"


def test_tie_falls_back_when_engine_text_is_not_the_expected_json(tmp_path: Path) -> None:
    # Regression: a real engine that ignores the "reply with only JSON"
    # instruction (prose, or malformed JSON) must degrade exactly like an
    # empty NoopEngine reply -- not silently ignore the tie-break contract.
    runner = fake_gh(repos=[], issues={})
    repo_root = make_repo_root(tmp_path, [], signals={})
    manifest = write_manifest(
        tmp_path,
        [
            mentry("MyTester", "my-tester", "2026-07-05"),
            mentry("MyReporter", "my-reporter", "2026-07-05"),
        ],
    )
    engine = ScriptedEngine("I'd go with MyTester since it's simplest.")

    rec = Orchestrator(
        org="MyThingsLab",
        manifest_path=manifest,
        repo_root=repo_root,
        ledger=Ledger(tmp_path / "ledger.jsonl"),
        runner=runner,
        engine=engine,
    ).next()

    assert rec.chosen is not None and rec.chosen.id == "scaffold:my-reporter"  # fell back
    assert "no usable choice" in rec.reason


def test_tie_falls_back_to_oldest_first_against_noop_engine(tmp_path: Path) -> None:
    runner = fake_gh(repos=[], issues={})
    repo_root = make_repo_root(tmp_path, [], signals={})
    manifest = write_manifest(
        tmp_path,
        [
            mentry("MyTester", "my-tester", "2026-07-05"),
            mentry("MyReporter", "my-reporter", "2026-07-05"),
        ],
    )
    rec = Orchestrator(
        org="MyThingsLab",
        manifest_path=manifest,
        repo_root=repo_root,
        ledger=Ledger(tmp_path / "ledger.jsonl"),
        runner=runner,
        engine=NoopEngine(),
    ).next()

    assert rec.engine_used is True  # the call still happened...
    assert rec.chosen is not None and rec.chosen.id == "scaffold:my-reporter"  # ...but fell back


def test_no_ready_candidates(tmp_path: Path) -> None:
    runner = fake_gh(repos=[], issues={})
    manifest = write_manifest(
        tmp_path,
        [mentry("MyAdvisor", "my-advisor", "2026-02-01", ["tool:my-wiki"])],
    )
    ledger = Ledger(tmp_path / "ledger.jsonl")
    rec = Orchestrator(
        org="MyThingsLab",
        manifest_path=manifest,
        repo_root=make_repo_root(tmp_path, [], signals={}),
        ledger=ledger,
        runner=runner,
    ).next()

    assert rec.chosen is None
    assert list(ledger)[0].detail == "next: none"


def test_next_n_gives_each_worker_a_distinct_ranked_candidate(tmp_path: Path) -> None:
    # Two available workers should not both get pointed at the same candidate,
    # and no Engine call is needed since ties don't need breaking across workers.
    repos = ["my-guard", "my-reporter"]
    runner = fake_gh(
        repos=repos,
        issues={
            "my-guard": [issue(1, "guard bug", "2026-01-01T00:00:00Z")],
            "my-reporter": [issue(1, "reporter bug", "2026-02-01T00:00:00Z")],
        },
    )
    repo_root = make_repo_root(tmp_path, repos, signals={})
    manifest = write_manifest(tmp_path, [])
    engine = ScriptedEngine()
    ledger = Ledger(tmp_path / "ledger.jsonl")

    recs = Orchestrator(
        org="MyThingsLab",
        manifest_path=manifest,
        repo_root=repo_root,
        ledger=ledger,
        runner=runner,
        engine=engine,
    ).next_n(2)

    assert engine.calls == []
    assert [r.chosen.id for r in recs if r.chosen] == ["my-guard#1", "my-reporter#1"]
    assert len(list(ledger)) == 2


def test_next_n_fewer_ready_candidates_than_workers(tmp_path: Path) -> None:
    runner = fake_gh(repos=[], issues={})
    manifest = write_manifest(tmp_path, [mentry("MyTester", "my-tester", "2026-07-05")])
    ledger = Ledger(tmp_path / "ledger.jsonl")

    recs = Orchestrator(
        org="MyThingsLab",
        manifest_path=manifest,
        repo_root=make_repo_root(tmp_path, [], signals={}),
        ledger=ledger,
        runner=runner,
    ).next_n(3)

    assert len(recs) == 1
    assert recs[0].chosen is not None and recs[0].chosen.id == "scaffold:my-tester"


class _DenyEdits:
    def evaluate(self, action: Action) -> PolicyResult:
        return PolicyResult(Decision.DENY, reason="no", rule="deny_all")


def test_tracking_update_is_gated_by_policy(tmp_path: Path) -> None:
    runner = fake_gh(repos=[], issues={})
    manifest = write_manifest(
        tmp_path,
        [mentry("MyTester", "my-tester", "2026-07-05")],
    )
    Orchestrator(
        org="MyThingsLab",
        manifest_path=manifest,
        repo_root=make_repo_root(tmp_path, [], signals={}),
        ledger=Ledger(tmp_path / "ledger.jsonl"),
        runner=runner,
        policy=_DenyEdits(),
        tracking=Tracking(repo="MyThingsLab/my-things-core", issue=1),
    ).next()

    # DENY => the issue-edit gh call never reaches the runner.
    assert not any(c[:2] == ["issue", "edit"] for c in runner.calls)


def _write_plan_ledger(tmp_path: Path, plan: list[dict], flags: list[str] | None = None) -> Path:
    path = tmp_path / "plan.jsonl"
    Ledger(path).append(
        LedgerEntry(
            tool="myplanner",
            kind="plan",
            outcome="success",
            data={"plan": plan, "flags": flags or []},
        )
    )
    return path


def test_planner_boost_is_reported_but_does_not_outrank_the_labels(tmp_path: Path) -> None:
    # Two ready scaffolds, indistinguishable by label, so age decides: my-reviewer
    # is older and wins. MyPlanner's "build my-tester next" still rides along as
    # urgency for the reader, but it no longer reorders the queue -- a soft signal
    # inferred from a plan does not get to outvote a label a human set.
    runner = fake_gh(repos=[], issues={})
    manifest = write_manifest(
        tmp_path,
        [
            mentry("MyReviewer", "my-reviewer", "2026-01-01"),
            mentry("MyTester", "my-tester", "2026-06-01"),
        ],
    )
    plan_ledger = _write_plan_ledger(
        tmp_path, [{"item": "build my-tester", "rationale": "unblocks coverage", "horizon": "next"}]
    )

    rec = Orchestrator(
        org="MyThingsLab",
        manifest_path=manifest,
        repo_root=make_repo_root(tmp_path, [], signals={}),
        ledger=Ledger(tmp_path / "ledger.jsonl"),
        runner=runner,
        plan_ledger=plan_ledger,
    ).next()

    assert rec.chosen is not None
    assert rec.chosen.id == "scaffold:my-reviewer"  # older, and the boost doesn't move it
    boosted = next(c for c in rec.candidates if c.id == "scaffold:my-tester")
    assert boosted.urgency == 3  # still visible in the report


def test_planner_pause_flag_removes_scaffolds_from_the_running(tmp_path: Path) -> None:
    # A live issue and a much older ready scaffold: age would give it to the
    # scaffold, but a "pause new tools" flag takes scaffolds out entirely. Under
    # a label ranking a penalty score has nowhere to go, and "pause" always meant
    # don't start one, not start one slightly later.
    repos = ["my-guard"]
    runner = fake_gh(
        repos=repos,
        issues={"my-guard": [issue(1, "guard bug", "2026-05-01T00:00:00Z")]},
    )
    manifest = write_manifest(tmp_path, [mentry("MyTester", "my-tester", "2026-01-01")])
    plan_ledger = _write_plan_ledger(
        tmp_path, [], flags=["pause new tools, close a safety gap first"]
    )

    rec = Orchestrator(
        org="MyThingsLab",
        manifest_path=manifest,
        repo_root=make_repo_root(tmp_path, repos, signals={}),
        ledger=Ledger(tmp_path / "ledger.jsonl"),
        runner=runner,
        plan_ledger=plan_ledger,
    ).next()

    assert rec.chosen is not None
    assert rec.chosen.id == "my-guard#1"
    assert [c.id for c in rec.candidates] == ["my-guard#1"]  # the scaffold is gone, not demoted


def test_undispatchable_issues_are_reported_not_silently_dropped(tmp_path: Path) -> None:
    repos = ["my-guard"]
    runner = fake_gh(
        repos=repos,
        issues={
            "my-guard": [
                issue(1, "ready", "2026-05-01T00:00:00Z"),
                issue(2, "too big", "2026-01-01T00:00:00Z", ("lane:kernel", "prio:P0", "size:L")),
                issue(3, "unlabelled", "2026-01-01T00:00:00Z", ("my-guard",)),
                issue(4, "blocked", "2026-01-01T00:00:00Z", ("lane:core", "state:blocked")),
            ]
        },
    )
    ledger = Ledger(tmp_path / "ledger.jsonl")

    rec = Orchestrator(
        org="MyThingsLab",
        manifest_path=write_manifest(tmp_path, []),
        repo_root=make_repo_root(tmp_path, repos, signals={}),
        ledger=ledger,
        runner=runner,
    ).next()

    # The size:L kernel P0 would have ranked first; it is held back for
    # decomposition instead, and every exclusion says why.
    assert rec.chosen is not None and rec.chosen.id == "my-guard#1"
    assert {e.candidate.id for e in rec.excluded} == {"my-guard#2", "my-guard#3", "my-guard#4"}
    recorded = {e["id"]: e["reasons"] for e in list(ledger)[0].data["excluded"]}
    assert "size:L is too large to dispatch as-is; split it first" in recorded["my-guard#2"]
    assert recorded["my-guard#3"] == ["missing lane", "missing size"]
