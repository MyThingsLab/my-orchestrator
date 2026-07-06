from __future__ import annotations

from pathlib import Path

from mythings.engine import EngineResult, NoopEngine
from mythings.ledger import Ledger, LedgerEntry
from mythings.policy import Action, Decision, PolicyResult

from conftest import FakeRunner, SpyEngine, issue, make_repo_root, mentry, write_manifest
from myorchestrator.orchestrator import Orchestrator, Tracking


def _drift(repo: str) -> LedgerEntry:
    return LedgerEntry(tool="mydriftwatcher", kind="drift", outcome="drift_found", detail=repo)


def test_happy_path_picks_most_urgent_without_engine_call(tmp_path: Path) -> None:
    # Three repos with one open issue each, plus one ready-to-scaffold tool. A drift
    # signal in my-searcher makes its (newest) issue jump the oldest-first queue.
    repos = ["my-guard", "my-reporter", "my-searcher"]
    runner = FakeRunner(
        repos=repos,
        issues={
            "my-guard": [issue(1, "guard bug", "2026-01-01T00:00:00Z")],
            "my-reporter": [issue(1, "reporter bug", "2026-03-01T00:00:00Z")],
            "my-searcher": [issue(1, "searcher bug", "2026-05-01T00:00:00Z")],
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
    engine = SpyEngine()
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
    assert rec.chosen.id == "my-searcher#1"  # urgency beats the older two
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
    runner = FakeRunner(repos=[], issues={})
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
    reply = EngineResult(text="", data={"chosen": "scaffold:my-tester", "reason": "loop"})
    engine = SpyEngine(reply)
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


def test_tie_falls_back_to_oldest_first_against_noop_engine(tmp_path: Path) -> None:
    runner = FakeRunner(repos=[], issues={})
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
    runner = FakeRunner(repos=[], issues={})
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


class _DenyEdits:
    def evaluate(self, action: Action) -> PolicyResult:
        return PolicyResult(Decision.DENY, reason="no", rule="deny_all")


def test_tracking_update_is_gated_by_policy(tmp_path: Path) -> None:
    runner = FakeRunner(repos=[], issues={})
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
        tracking=Tracking(repo="MyThingsLab/mythings-core", issue=1),
    ).next()

    # DENY => the issue-edit gh call never reaches the runner.
    assert not any(c[:2] == ["issue", "edit"] for c in runner.calls)
