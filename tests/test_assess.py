from __future__ import annotations

import json
from pathlib import Path

from mythings.ledger import Ledger
from mythings.policy import Action, Decision, PolicyResult
from mythings.testing import FakeGh, ScriptedEngine

from myorchestrator.assess import assess
from myorchestrator.orchestrator import Orchestrator


class _AllowPolicy:
    def evaluate(self, action: Action) -> PolicyResult:
        return PolicyResult(Decision.ALLOW)


class _DenyPolicy:
    def evaluate(self, action: Action) -> PolicyResult:
        return PolicyResult(Decision.DENY, reason="no")


def _gh_for(existing: list[dict], created_number: int = 100) -> FakeGh:
    def issue_list(argv: list[str]) -> str:
        return json.dumps(existing)

    def issue_create(argv: list[str]) -> str:
        return f"https://github.com/MyThingsLab/my-target/issues/{created_number}\n"

    return FakeGh(
        {
            ("issue", "list"): issue_list,
            ("issue", "create"): issue_create,
            ("issue", "edit"): "",
        }
    )


def _write_assessment(repo_root: Path, repo: str, text: str = "known problem X") -> None:
    d = repo_root / repo
    d.mkdir(parents=True, exist_ok=True)
    (d / "ASSESSMENT.md").write_text(text, encoding="utf-8")


def test_no_assessment_doc_is_a_clean_noop(tmp_path: Path) -> None:
    repo_root = tmp_path / "workspace"
    (repo_root / "my-target").mkdir(parents=True)
    runner = _gh_for(existing=[])
    engine = ScriptedEngine()

    result = assess(
        org="MyThingsLab",
        repo="my-target",
        repo_root=repo_root,
        engine=engine,
        policy=_AllowPolicy(),
        runner=runner,
    )

    assert result.reason == "no ASSESSMENT.md"
    assert result.created == []
    assert result.skipped == []
    assert engine.calls == []
    assert runner.calls == []


def test_dedupes_against_already_open_titles(tmp_path: Path) -> None:
    repo_root = tmp_path / "workspace"
    _write_assessment(repo_root, "my-target")
    existing = [{"number": 1, "title": "already tracked", "body": "", "url": "u", "labels": []}]
    runner = _gh_for(existing=existing)
    reply = json.dumps(
        {
            "issues": [
                {"title": "already tracked", "body": "dup"},
                {"title": "fresh issue", "body": "new"},
            ]
        }
    )
    engine = ScriptedEngine(reply=reply)

    result = assess(
        org="MyThingsLab",
        repo="my-target",
        repo_root=repo_root,
        engine=engine,
        policy=_AllowPolicy(),
        runner=runner,
    )

    assert [c["title"] for c in result.created] == ["fresh issue"]
    assert {"title": "already tracked", "reason": "already open"} in result.skipped
    assert runner.saw("issue", "create")


def test_unusable_engine_reply_files_nothing(tmp_path: Path) -> None:
    repo_root = tmp_path / "workspace"
    _write_assessment(repo_root, "my-target")
    runner = _gh_for(existing=[])
    engine = ScriptedEngine(reply="not json")

    result = assess(
        org="MyThingsLab",
        repo="my-target",
        repo_root=repo_root,
        engine=engine,
        policy=_AllowPolicy(),
        runner=runner,
    )

    assert result.engine_used is False
    assert result.created == []
    assert not runner.saw("issue", "create")


def test_max_new_caps_how_many_are_filed(tmp_path: Path) -> None:
    repo_root = tmp_path / "workspace"
    _write_assessment(repo_root, "my-target")
    runner = _gh_for(existing=[])
    reply = json.dumps({"issues": [{"title": f"issue {i}", "body": "b"} for i in range(3)]})
    engine = ScriptedEngine(reply=reply)

    result = assess(
        org="MyThingsLab",
        repo="my-target",
        repo_root=repo_root,
        engine=engine,
        policy=_AllowPolicy(),
        runner=runner,
        max_new=1,
    )

    assert len(result.created) == 1
    assert any(s["reason"] == "max_new cap reached" for s in result.skipped)


def test_policy_deny_skips_without_filing(tmp_path: Path) -> None:
    repo_root = tmp_path / "workspace"
    _write_assessment(repo_root, "my-target")
    runner = _gh_for(existing=[])
    reply = json.dumps({"issues": [{"title": "blocked", "body": "b"}]})
    engine = ScriptedEngine(reply=reply)

    result = assess(
        org="MyThingsLab",
        repo="my-target",
        repo_root=repo_root,
        engine=engine,
        policy=_DenyPolicy(),
        runner=runner,
    )

    assert result.created == []
    assert result.skipped == [{"title": "blocked", "reason": "policy: deny"}]
    assert not runner.saw("issue", "create")


def test_orchestrator_assess_records_one_ledger_entry(tmp_path: Path) -> None:
    repo_root = tmp_path / "workspace"
    _write_assessment(repo_root, "my-target")
    runner = _gh_for(existing=[])
    reply = json.dumps({"issues": [{"title": "fresh issue", "body": "new"}]})
    engine = ScriptedEngine(reply=reply)
    ledger = Ledger(tmp_path / "ledger.jsonl")
    manifest = tmp_path / "manifest.json"
    manifest.write_text("[]", encoding="utf-8")

    orch = Orchestrator(
        org="MyThingsLab",
        manifest_path=manifest,
        repo_root=repo_root,
        ledger=ledger,
        runner=runner,
        engine=engine,
    )

    result = orch.assess("my-target")

    written = list(ledger)
    assert len(written) == 1
    assert written[0].kind == "assess"
    assert len(result.created) == 1
