from __future__ import annotations

import json

import pytest
from mythings.engine import ClaudeCLIEngine

from myorchestrator import cli
from myorchestrator.candidates import Candidate
from myorchestrator.orchestrator import Recommendation


def _cand(id_: str = "repo#1", title: str = "Fix thing") -> Candidate:
    return Candidate(
        id=id_, repo="repo", tool="tool", title=title, kind="issue",
        created_at="2026-01-01T00:00:00Z",
    )


def _rec(
    *,
    chosen: Candidate | None,
    reason: str = "because",
    engine_used: bool = False,
    candidates: list[Candidate] | None = None,
) -> Recommendation:
    return Recommendation(
        chosen=chosen, reason=reason, candidates=candidates or [], engine_used=engine_used
    )


def _stub_orch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    one: Recommendation | None = None,
    many: list[Recommendation] | None = None,
) -> dict:
    captured: dict = {}

    class _Stub:
        def __init__(self, **kwargs: object) -> None:
            captured["kwargs"] = kwargs

        def next(self) -> Recommendation:
            captured["called"] = "next"
            return one

        def next_n(self, count: int) -> list[Recommendation]:
            captured["called"] = ("next_n", count)
            return many

    monkeypatch.setattr(cli, "Orchestrator", _Stub)
    return captured


def test_as_dict_with_a_choice() -> None:
    rec = _rec(
        chosen=_cand("repo#1"), reason="oldest",
        candidates=[_cand("repo#1"), _cand("repo#2")],
    )
    assert cli._as_dict(rec) == {
        "chosen": "repo#1",
        "reason": "oldest",
        "engine_used": False,
        "candidates": ["repo#1", "repo#2"],
    }


def test_as_dict_without_a_choice() -> None:
    assert cli._as_dict(_rec(chosen=None, reason="no candidates"))["chosen"] is None


def test_render_deterministic_choice() -> None:
    out = cli._render(_rec(chosen=_cand("repo#1", "Fix"), reason="oldest"), as_json=False)
    assert "next: repo#1" in out
    assert "deterministic" in out
    assert "Fix" in out
    assert "oldest" in out


def test_render_labels_engine_tiebreak() -> None:
    out = cli._render(_rec(chosen=_cand(), reason="r", engine_used=True), as_json=False)
    assert "engine tie-break" in out


def test_render_none_when_no_candidate() -> None:
    assert "(none)" in cli._render(_rec(chosen=None, reason="r"), as_json=False)


def test_render_json_mode() -> None:
    out = cli._render(_rec(chosen=_cand("repo#1"), reason="r"), as_json=True)
    assert json.loads(out)["chosen"] == "repo#1"


def test_render_many_mixes_choices_and_none() -> None:
    recs = [_rec(chosen=_cand("repo#1", "A"), reason="r1"), _rec(chosen=None, reason="r2")]
    out = cli._render_many(recs, as_json=False)
    assert "worker 1: repo#1" in out
    assert "worker 2: (none)" in out


def test_render_many_json_mode() -> None:
    out = cli._render_many([_rec(chosen=_cand("repo#1"), reason="r")], as_json=True)
    assert json.loads(out)[0]["chosen"] == "repo#1"


def test_next_single_calls_next(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured = _stub_orch(monkeypatch, one=_rec(chosen=_cand("repo#1"), reason="r"))

    code = cli.main(["next"])

    assert code == 0
    assert captured["called"] == "next"
    assert "repo#1" in capsys.readouterr().out


def test_count_greater_than_one_calls_next_n(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _stub_orch(monkeypatch, many=[_rec(chosen=_cand("repo#1"), reason="r")])

    cli.main(["next", "--count", "3"])

    assert captured["called"] == ("next_n", 3)


def test_count_below_one_is_a_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_orch(monkeypatch, one=_rec(chosen=None, reason="r"))
    with pytest.raises(SystemExit):
        cli.main(["next", "--count", "0"])


def test_json_flag_emits_machine_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub_orch(monkeypatch, one=_rec(chosen=_cand("repo#1"), reason="r"))

    cli.main(["next", "--json"])

    assert json.loads(capsys.readouterr().out)["chosen"] == "repo#1"


def test_claude_cli_engine_is_wired(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _stub_orch(monkeypatch, one=_rec(chosen=None, reason="r"))

    cli.main(["next", "--engine", "claude-cli"])

    assert isinstance(captured["kwargs"]["engine"], ClaudeCLIEngine)


def test_tracking_is_wired_when_both_flags_given(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _stub_orch(monkeypatch, one=_rec(chosen=None, reason="r"))

    cli.main(["next", "--tracking-repo", "MyThingsLab/x", "--tracking-issue", "4"])

    tracking = captured["kwargs"]["tracking"]
    assert tracking is not None
    assert tracking.repo == "MyThingsLab/x"
    assert tracking.issue == 4


def test_tracking_is_none_when_only_one_flag_given(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _stub_orch(monkeypatch, one=_rec(chosen=None, reason="r"))

    cli.main(["next", "--tracking-repo", "MyThingsLab/x"])

    assert captured["kwargs"]["tracking"] is None


def test_missing_subcommand_is_a_usage_error() -> None:
    with pytest.raises(SystemExit):
        cli.main([])
